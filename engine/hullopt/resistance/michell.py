"""Infinite-depth Michell wave resistance of a symmetric, single-valued hull.

The offset surface is bilinear in x/z. Its longitudinal derivative is
integrated analytically in both directions, including the exponential depth
factor. Only the regularised outer integral needs numerical quadrature.
See validation/sources/MICHELL_METHOD.md for equations and limitations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

import numpy as np
from numpy.polynomial.legendre import leggauss

from ..hydro.sections import plane_segments, triangles_array
from .base import Conditions, RawResult
from .friction import ITTC57Friction, KNOT_TO_M_S

# Highest Gauss order the adaptive outer integral may reach (its check then uses twice this).
MAX_ADAPTIVE_ORDER = 64


def _immutable_array(value) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    # A bytes buffer prevents callers from re-enabling WRITEABLE with setflags.
    return np.frombuffer(array.tobytes(), dtype=np.float64).reshape(array.shape)


@dataclass(frozen=True, eq=False)
class OffsetHull:
    """SI half-breadths at (z,x) nodes; +x forward, +z up, surface z=z_wl_m.

    The data are copied into immutable buffers. Symmetry is an input assumption
    for directly supplied offsets; from_mesh checks it against both hull sides.
    """

    xs_m: np.ndarray
    zs_m: np.ndarray
    halfbreadths_m: np.ndarray
    z_wl_m: float
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        x, z, y = (_immutable_array(v) for v in
                   (self.xs_m, self.zs_m, self.halfbreadths_m))
        if x.ndim != 1 or z.ndim != 1 or len(x) < 3 or len(z) < 2:
            raise ValueError("Offsets need at least three x nodes and two z nodes")
        if y.shape != (len(z), len(x)):
            raise ValueError("halfbreadths_m must have shape (len(zs_m), len(xs_m))")
        if not all(np.isfinite(v).all() for v in (x, z, y)) or not math.isfinite(self.z_wl_m):
            raise ValueError("Offset coordinates and waterline must be finite")
        if np.any(np.diff(x) <= 0) or np.any(np.diff(z) <= 0):
            raise ValueError("x and z offset coordinates must be strictly increasing")
        if abs(z[-1] - self.z_wl_m) > 1e-9 or z[0] >= self.z_wl_m:
            raise ValueError("Offset z nodes must extend from below to exactly the waterline")
        if np.any(y < 0) or not np.any(y > 0):
            raise ValueError("Half-breadths must be nonnegative with a nonzero hull")
        object.__setattr__(self, "xs_m", x)
        object.__setattr__(self, "zs_m", z)
        object.__setattr__(self, "halfbreadths_m", y)
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @classmethod
    def from_mesh(cls, mesh, z_wl_m: float, *, nx: int = 81, nz: int = 41,
                  symmetry_tolerance: float = 0.02, centerplane_y_m: float = 0.0,
                  multi_interval_policy: str = "reject",
                  bow_padding_fraction_lwl: float = 0.0) -> "OffsetHull":
        """Cut full-hull triangles at x, then scan z with an even-odd width rule.

        Zero intersections mean the scanline lies outside the hull. Odd parity,
        multiple disjoint intervals or excessive asymmetry are errors by
        default. Explicit equivalent_width sums immersed intervals/2, preserving
        volume at each scanline while replacing re-entrant geometry. End planes
        use their one-sided interior limit. No virtual transom is added here.
        """
        if (not isinstance(nx, int) or not isinstance(nz, int) or nx < 5 or nz < 3):
            raise ValueError("Mesh offsets require integer nx>=5 and nz>=3")
        if not all(math.isfinite(v) for v in (z_wl_m, symmetry_tolerance, centerplane_y_m,
                                              bow_padding_fraction_lwl)):
            raise ValueError("Waterline, symmetry tolerance and centreplane must be finite")
        if symmetry_tolerance < 0:
            raise ValueError("symmetry_tolerance must be nonnegative")
        if bow_padding_fraction_lwl < 0 or bow_padding_fraction_lwl > 0.1:
            raise ValueError("bow_padding_fraction_lwl must lie in [0,0.1]")
        if multi_interval_policy not in ("reject", "equivalent_width"):
            raise ValueError("multi_interval_policy must be 'reject' or 'equivalent_width'")
        triangles = triangles_array(mesh)
        vertices = triangles.reshape(-1, 3)
        wet_vertices = vertices[vertices[:, 2] <= z_wl_m]
        wl_segments, _ = plane_segments(triangles, z_wl_m, 2)
        if len(wl_segments):
            wet_vertices = np.vstack((wet_vertices, wl_segments.reshape(-1, 3)))
        if not len(wet_vertices):
            raise ValueError("Mesh has no submerged geometry")
        xmin, xmax = wet_vertices[:, 0].min(), wet_vertices[:, 0].max()
        zmin = wet_vertices[:, 2].min()
        if xmax <= xmin or zmin >= z_wl_m:
            raise ValueError("Submerged hull must have positive length and draft")
        bow_padding_m = bow_padding_fraction_lwl * (xmax - xmin)
        xs = np.linspace(xmin, xmax + bow_padding_m, nx)
        zs = np.linspace(zmin, z_wl_m, nz)
        offsets = np.zeros((nz, nx))
        center_errors = []
        multi_positions = []
        multi_count = max_intervals = 0
        x_step, z_step = max(xmax - xmin, 1.0) * 1e-9, max(z_wl_m - zmin, 1.0) * 1e-9
        merge_tolerance = max(float(np.ptp(vertices[:, 1])), 1.0) * 1e-8
        scan_count = empty_count = 0
        for ix, station in enumerate(xs):
            sample_x = max(station, xmin + x_step)
            if station == xmax:
                sample_x = xmax - x_step
            segments, _ = plane_segments(triangles, sample_x, 0)
            for iz, waterline in enumerate(zs):
                sample_z = min(max(waterline, zmin + z_step), z_wl_m - z_step)
                first, second = segments[:, 0], segments[:, 1]
                # Coincident triangulation vertices can differ by a few ULPs
                # after the first plane cut. Snap comparisons to the scanline
                # so a shared endpoint cannot be counted zero or twice.
                first_z = np.where(abs(first[:, 2] - sample_z) < z_step * 0.01, sample_z, first[:, 2])
                second_z = np.where(abs(second[:, 2] - sample_z) < z_step * 0.01, sample_z, second[:, 2])
                crosses = (first_z <= sample_z) != (second_z <= sample_z)
                first, second = first[crosses], second[crosses]
                first_z, second_z = first_z[crosses], second_z[crosses]
                if not len(first):
                    empty_count += 1
                    continue
                fraction = (sample_z - first_z) / (second_z - first_z)
                hits = np.sort(first[:, 1] + fraction * (second[:, 1] - first[:, 1]))
                # Shared triangulation edges may produce numerically identical hits.
                unique = hits[np.r_[True, np.diff(hits) > merge_tolerance]]
                if len(unique) == 1 and np.ptp(hits) <= merge_tolerance:
                    # A tangent at the centreplane has zero physical width.
                    if abs(unique[0] - centerplane_y_m) <= merge_tolerance:
                        empty_count += 1
                        continue
                if len(unique) % 2:
                    raise ValueError(f"Odd section intersection parity at x={station:g}, z={waterline:g}; open or invalid mesh")
                intervals = len(unique) // 2
                max_intervals = max(max_intervals, intervals)
                if intervals > 1:
                    if multi_interval_policy == "reject":
                        raise ValueError(f"Multiple submerged width intervals at x={station:g}, z={waterline:g}; thin-ship offsets require one body")
                    multi_count += 1
                    if len(multi_positions) < 20:
                        multi_positions.append((float(station), float(waterline), intervals))
                offsets[iz, ix] = 0.5 * float(np.sum(unique[1::2] - unique[::2]))
                # All corresponding mirrored crossings must agree, including
                # inner cavity boundaries; an outer envelope check is insufficient.
                center_errors.extend(abs(0.5 * (unique + unique[::-1]) - centerplane_y_m))
                scan_count += 1
        beam = 2 * float(offsets.max())
        if beam <= 0:
            raise ValueError("Mesh cuts did not produce a submerged hull")
        asymmetry = max(center_errors, default=0.0) / beam
        if asymmetry > symmetry_tolerance:
            raise ValueError(f"Hull asymmetry {asymmetry:.3%} of beam exceeds {symmetry_tolerance:.3%}")
        physical_columns = (xs > xmin + x_step) & (xs < xmax - x_step)
        if np.any(np.max(offsets[:, physical_columns], axis=0) == 0):
            raise ValueError("Empty internal station: disconnected hull or missing section data")
        # A raked/curved transom does not coincide with a single x station.
        # Derive each z-row anchor from aft-facing skin cuts, independently of
        # nx. The |normal_x| filter also permits meshes with reversed normals.
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normal_size = np.linalg.norm(normal, axis=1)
        transom_faces = (abs(normal[:, 0]) > 0.7 * normal_size) & (triangles[:, :, 0].mean(axis=1) < xmin + 2.0)
        transom_rows = []
        for iz, waterline in enumerate(zs):
            sample_z = min(max(waterline, zmin + z_step), z_wl_m - z_step)
            transom_segments, _ = plane_segments(triangles[transom_faces], sample_z, 2)
            if not len(transom_segments):
                continue
            spans = sorted((float(pair[:, 1].min()), float(pair[:, 1].max()))
                           for pair in transom_segments if np.ptp(pair[:, 1]) > merge_tolerance)
            if not spans:
                continue
            total_width = 0.0
            left, right = spans[0]
            for next_left, next_right in spans[1:]:
                if next_left <= right + merge_tolerance:
                    right = max(right, next_right)
                else:
                    total_width += right - left
                    left, right = next_left, next_right
            total_width += right - left
            transom_rows.append((iz, float(transom_segments[:, :, 0].max()), 0.5 * total_width))
        section_areas = np.sum((offsets[:, :-1] + offsets[:, 1:]) * np.diff(xs), axis=1)
        offset_volume = float(np.sum(0.5 * (section_areas[:-1] + section_areas[1:]) * np.diff(zs)))
        return cls(xs, zs, offsets, z_wl_m, {
            "source": "full-hull triangle section cuts and width parity",
            "physical_x_extents_m": (float(xmin), float(xmax)),
            "bow_padding_fraction_lwl": bow_padding_fraction_lwl,
            "bow_padding_m": bow_padding_m,
            "nx": nx, "nz": nz, "centerplane_y_m": centerplane_y_m,
            "symmetry_center_error_over_beam": asymmetry,
            "symmetry_tolerance": symmetry_tolerance,
            "nonempty_scanlines": scan_count, "empty_scanlines": empty_count,
            "multi_interval_policy": multi_interval_policy,
            "multi_interval_scanlines": multi_count,
            "multi_interval_fraction_of_nonempty": multi_count / scan_count,
            "max_intervals_per_scanline": max_intervals,
            "multi_interval_examples_x_z_count": tuple(multi_positions),
            "multi_interval_examples_limit": 20,
            "offset_volume_m3": offset_volume,
            "transom_row_anchors_index_x_halfbreadth": tuple(transom_rows),
            "transom_anchor_rule": "aft-face abs(normal_x)/norm>0.7, centroid x<xmin+2m; row x=max(z-cut x), halfbreadth=union projected-y width/2",
            "endpoint_sampling": "one-sided interior limit, 1e-9 of bounding dimension",
        })


class MichellThinShip:
    """Linear inviscid wave component, optionally plus smooth ITTC-57 friction.

    An immersed transom requires an explicit positive virtual-closure length.
    This implementation uses a cubic taper and is not Maxsurf's proprietary
    transom correction. The measured wetted area excludes the virtual closure.
    """

    name = "PY-MICHELL"

    def __init__(self, offsets: OffsetHull, *, include_friction: bool = False,
                 gravity_m_s2: float = 9.80665, lambda_max: float = 32.0,
                 quadrature_order: int = 12, transom_closure_length_m: float = 0.0,
                 convergence_tolerance: float = 0.01, check_convergence: bool = True,
                 lambda_max_limit: float | None = None):
        """``lambda_max_limit`` enables adaptive refinement: at a speed whose checked outer
        integral misses the tolerance, the cut-off is doubled (up to this limit) and then the
        Gauss order (up to ``MAX_ADAPTIVE_ORDER``). Speeds that converge with the requested
        settings are computed exactly as without it."""
        if not isinstance(offsets, OffsetHull):
            raise TypeError("MichellThinShip requires an OffsetHull")
        if not all(math.isfinite(v) for v in (gravity_m_s2, lambda_max,
                                             transom_closure_length_m, convergence_tolerance)):
            raise ValueError("Michell numerical settings must be finite")
        if gravity_m_s2 <= 0 or lambda_max <= 1 or transom_closure_length_m < 0 or convergence_tolerance <= 0:
            raise ValueError("Require g>0, lambda_max>1, closure length>=0 and convergence tolerance>0")
        if not isinstance(quadrature_order, int) or quadrature_order < 4 or quadrature_order > 64:
            raise ValueError("quadrature_order must be an integer in [4,64]")
        if lambda_max_limit is not None and not (math.isfinite(lambda_max_limit) and lambda_max_limit >= lambda_max):
            raise ValueError("lambda_max_limit must be finite and at least lambda_max")
        self.offsets = offsets
        self.include_friction = include_friction
        self.gravity_m_s2 = gravity_m_s2
        self.lambda_max = lambda_max
        self.quadrature_order = quadrature_order
        self.transom_closure_length_m = transom_closure_length_m
        self.convergence_tolerance = convergence_tolerance
        self.check_convergence = check_convergence
        self.lambda_max_limit = lambda_max_limit
        x, y = offsets.xs_m, offsets.halfbreadths_m
        physical_extents = offsets.diagnostics.get("physical_x_extents_m", (x[0], x[-1]))
        self._physical_length_m = float(physical_extents[1] - physical_extents[0])
        tolerance = max(float(y.max()) * 1e-7, 1e-8)
        if np.max(y[:, -1]) > tolerance:
            raise ValueError("Forward offsets must close on the centreplane; enlarge the mesh extent or repair the bow")
        transom_rows = offsets.diagnostics.get("transom_row_anchors_index_x_halfbreadth", ())
        self._closure_applied = bool(transom_rows or np.max(y[:, 0]) > tolerance)
        if self._closure_applied:
            if transom_closure_length_m == 0:
                raise ValueError("Immersed transom requires explicit transom_closure_length_m>0 (heuristic virtual closure)")
            if transom_rows:
                # Insert cut-derived anchor positions, so a raked transom's
                # physical location is not rounded to the nearest x station.
                s = np.linspace(0, 1, 17)
                closure_x = [anchor - transom_closure_length_m * (1 - s)
                             for _, anchor, _ in transom_rows]
                all_x = np.sort(np.r_[x, *closure_x])
                coordinate_tolerance = max(float(np.ptp(all_x)) * 1e-12,
                                           float(np.max(abs(all_x))) * 64 * np.finfo(float).eps)
                refined_x = all_x[np.r_[True, np.diff(all_x) > coordinate_tolerance]]
                refined_y = np.vstack([np.interp(refined_x, x, row, left=0., right=0.) for row in y])
                for iz, anchor, halfbreadth in transom_rows:
                    forward_nodes = x > anchor
                    refined_y[iz] = np.interp(refined_x, np.r_[anchor, x[forward_nodes]],
                                              np.r_[halfbreadth, y[iz, forward_nodes]], left=0., right=0.)
                    aft = refined_x <= anchor
                    fraction = np.clip((refined_x[aft] - anchor + transom_closure_length_m)
                                       / transom_closure_length_m, 0., 1.)
                    refined_y[iz, aft] = halfbreadth * (3 * fraction**2 - 2 * fraction**3)
                x, y = refined_x, refined_y
            else:
                s = np.linspace(0, 1, 17)[:-1]
                aft_x = x[0] - transom_closure_length_m * (1 - s)
                aft_y = y[:, :1] * (3 * s**2 - 2 * s**3)
                x = np.concatenate((aft_x, x))
                y = np.column_stack((aft_y, y))
        self._x = _immutable_array(x - 0.5 * (x[0] + x[-1]))
        self._y = _immutable_array(y)
        self._slopes = np.diff(self._y, axis=1) / np.diff(self._x)
        self._maximum_measured_slope = float(np.max(abs(np.diff(offsets.halfbreadths_m, axis=1)
                                                      / np.diff(offsets.xs_m))))
        self._maximum_effective_slope = float(np.max(abs(self._slopes)))

    def amplitudes(self, lambdas, velocity_m_s: float) -> np.ndarray:
        """Return P+iQ in m² using exact bilinear-panel Fourier/Laplace integrals."""
        lambdas = np.asarray(lambdas, dtype=float)
        if lambdas.ndim != 1 or not np.isfinite(lambdas).all() or np.any(lambdas < 1):
            raise ValueError("lambdas must be a finite 1D array with values>=1")
        if not math.isfinite(velocity_m_s) or velocity_m_s <= 0:
            raise ValueError("velocity_m_s must be positive and finite")
        result = np.empty(len(lambdas), dtype=complex)
        z = self.offsets.zs_m - self.offsets.z_wl_m
        dz, dx = np.diff(z), np.diff(self._x)
        mid_x = 0.5 * (self._x[:-1] + self._x[1:])
        k0 = self.gravity_m_s2 / velocity_m_s**2
        # Bound working memory when small speeds require many outer panels.
        for start in range(0, len(lambdas), 1024):
            lam = lambdas[start:start + 1024]
            a = k0 * lam**2
            r = a[:, None] * dz
            exponential = np.exp(-r)
            left = (1 - (1 + r) * exponential) / r**2
            right = (r + np.expm1(-r)) / r**2
            small = r < 1e-3
            rs = r[small]
            left[small] = 0.5 - rs / 3 + rs**2 / 8 - rs**3 / 30 + rs**4 / 144
            right[small] = 0.5 - rs / 6 + rs**2 / 24 - rs**3 / 120 + rs**4 / 720
            scale = dz * np.exp(a[:, None] * z[None, 1:])
            weights = np.zeros((len(lam), len(z)))
            weights[:, :-1] += scale * left
            weights[:, 1:] += scale * right
            integrated_slopes = weights @ self._slopes
            k = k0 * lam[:, None]
            fourier = dx * np.sinc(k * dx / (2 * np.pi)) * np.exp(1j * k * mid_x)
            result[start:start + len(lam)] = np.sum(integrated_slopes * fourier, axis=1)
        return result

    def _integral(self, velocity_m_s: float, cutoff: float, order: int) -> tuple[float, int]:
        # lambda=cosh(t) removes the lambda=1 square-root singularity.
        # Limit phase advance per panel and t step; exact hull-panel integrals
        # prevent high-lambda aliasing of the hull offsets themselves.
        phase_rate = self.gravity_m_s2 / velocity_m_s**2 * np.ptp(self._x)
        dlambda = min(1.0, np.pi / max(float(phase_rate), 1e-12))
        edges_lambda = np.linspace(1, cutoff, max(1, math.ceil((cutoff - 1) / dlambda)) + 1)
        edges = np.unique(np.r_[np.arccosh(edges_lambda),
                                np.linspace(0, np.arccosh(cutoff), math.ceil(np.arccosh(cutoff) / 0.15) + 1)])
        nodes, weights = leggauss(order)
        half = 0.5 * np.diff(edges)
        t = (0.5 * (edges[:-1] + edges[1:]))[:, None] + half[:, None] * nodes
        lam = np.cosh(t.ravel())
        amplitude = self.amplitudes(lam, velocity_m_s)
        integrand = lam**2 * np.abs(amplitude)**2
        integral = float(np.sum(integrand.reshape(t.shape) * half[:, None] * weights))
        return integral, len(lam)

    def evaluate(self, candidate_measurements: Any, conditions: Conditions,
                 progress: Callable[[int, int, float], None] | None = None) -> RawResult:
        """``progress(index, count, speed_kn)`` is called before each speed."""
        forces, diagnostics = [], []
        warnings = ["Linear infinite-depth thin-ship wave theory; no viscous pressure, breaking waves, dynamic sinkage/trim, air, appendage, shallow-water or correlation corrections; not a validated total-resistance prediction."]
        if self._closure_applied:
            warnings.append("Immersed transom uses an explicit cubic virtual-closure heuristic; closure-length sensitivity is required. This is not Maxsurf's transom model.")
        equivalent_width = bool(self.offsets.diagnostics.get("multi_interval_scanlines", 0))
        if equivalent_width:
            warnings.append("Actual hull has re-entrant/multiple immersed width intervals. Explicit equivalent-width mapping preserves scanline immersed width but replaces the geometry; this Michell result is for screening only and requires validation.")
        if self._maximum_effective_slope >= 1:
            warnings.append(f"Maximum effective longitudinal half-breadth slope is {self._maximum_effective_slope:.3g}; the thin-ship small-slope assumption fails locally.")
        if not self.check_convergence:
            warnings.append("Outer-integral quadrature and cutoff convergence checks were disabled.")
        for index, speed in enumerate(conditions.speeds_kn):
            if progress is not None:
                progress(index, len(conditions.speeds_kn), speed)
            velocity = speed * KNOT_TO_M_S
            computed: dict[tuple[float, int], tuple[float, int]] = {}

            def integral_at(cutoff: float, order: int) -> float:
                if (cutoff, order) not in computed:
                    computed[cutoff, order] = self._integral(velocity, cutoff, order)
                return computed[cutoff, order][0]

            cutoff, order = self.lambda_max, self.quadrature_order
            integral = integral_at(cutoff, order)
            numerical = {"speed_kn": speed, "requested_lambda_max": self.lambda_max,
                         "requested_quadrature_order": self.quadrature_order,
                         "checked": self.check_convergence}
            if self.check_convergence:
                refinements = 0
                while True:
                    integral = integral_at(cutoff, order)
                    refined = integral_at(cutoff, 2 * order)
                    extended = integral_at(2 * cutoff, 2 * order)
                    denominator = max(abs(extended), np.finfo(float).tiny)
                    quadrature_error = abs(refined - integral) / denominator
                    cutoff_change = abs(extended - refined) / denominator
                    converged = max(quadrature_error, cutoff_change) <= self.convergence_tolerance
                    if converged or self.lambda_max_limit is None:
                        break
                    if cutoff_change > self.convergence_tolerance and 2 * cutoff <= self.lambda_max_limit:
                        cutoff *= 2
                    elif quadrature_error > self.convergence_tolerance and 2 * order <= MAX_ADAPTIVE_ORDER:
                        order *= 2
                    else:
                        break
                    refinements += 1
                numerical.update({"quadrature_relative_change": quadrature_error,
                                  "cutoff_relative_change": cutoff_change, "converged": converged,
                                  "returned_lambda_max": 2 * cutoff,
                                  "returned_quadrature_order": 2 * order})
                if refinements:
                    numerical["adaptive_refinements"] = refinements
                if not converged:
                    message = (f"At {speed:g} kn outer integral has not converged within {self.convergence_tolerance:.2%}; "
                               "increase lambda_max/quadrature_order.")
                    if self.lambda_max_limit is not None:
                        message += (f" Adaptive refinement stopped at lambda_max {2 * cutoff:g}, "
                                    f"quadrature order {2 * order}.")
                    warnings.append(message)
                integral = extended
            else:
                numerical.update({"converged": None, "returned_lambda_max": self.lambda_max,
                                  "returned_quadrature_order": self.quadrature_order})
            numerical["amplitude_evaluations"] = sum(count for _, count in computed.values())
            forces.append(4 * conditions.rho_kg_m3 * self.gravity_m_s2**2 / (np.pi * velocity**2) * integral)
            diagnostics.append(numerical)
        wave_forces = tuple(forces)
        friction_forces = None
        if self.include_friction:
            friction = ITTC57Friction().evaluate(candidate_measurements, conditions)
            friction_forces = friction.values
            forces = [wave + force for wave, force in zip(forces, friction.values)]
            warnings.append("Combined estimate is Michell wave + ITTC-57 smooth friction only, with form factor k=0; omissions above remain.")
        beam = 2 * float(self._y.max())
        length = self._physical_length_m
        if beam / length > 0.1:
            warnings.append(f"B/L={beam / length:.3f}; thinness is limited and physical validation is required.")
        return RawResult(
            backend=self.name,
            label=(("Approximate equivalent-width Michell wave + ITTC-57 smooth friction (k=0)"
                    if equivalent_width else "Approximate Michell wave + ITTC-57 smooth friction (k=0)")
                   if self.include_friction else
                   ("Equivalent-width Michell wave component (screening only)" if equivalent_width
                    else "Michell linear thin-ship wave component only")),
            quantity=("wave_plus_friction_force" if self.include_friction else "wave_force"),
            is_physical_prediction=True, speeds_kn=conditions.speeds_kn,
            values=tuple(float(force) for force in forces), units="N", warnings=tuple(warnings),
            metadata={"method": "Michell 1898, infinite depth, symmetric monohull",
                      "coordinate_convention": "+x forward, +z up; z relative to still waterline",
                      "gravity_m_s2": self.gravity_m_s2, "z_wl_m": self.offsets.z_wl_m,
                      "hull_length_m": length, "beam_over_length": beam / length,
                      "effective_domain_length_m": float(np.ptp(self._x)),
                      "maximum_measured_longitudinal_slope": self._maximum_measured_slope,
                      "maximum_effective_longitudinal_slope": self._maximum_effective_slope,
                      "offset_shape_nz_nx": list(self.offsets.halfbreadths_m.shape),
                      "offset_diagnostics": dict(self.offsets.diagnostics),
                      "equivalent_width_geometry": equivalent_width,
                      "panel_integration": "exact Fourier in x; exact linear-exponential in z",
                      "outer_integration": "lambda=cosh(t), phase-limited composite Gauss-Legendre",
                      "convergence_tolerance": self.convergence_tolerance, "numerical_convergence": diagnostics,
                      **({"lambda_max_limit": self.lambda_max_limit} if self.lambda_max_limit is not None else {}),
                      "transom_closure": {"applied": self._closure_applied,
                                          "length_m": self.transom_closure_length_m,
                                          "model": "17-node cubic smoothstep taper per transom waterline; explicit heuristic",
                                          "row_anchor_rule": self.offsets.diagnostics.get("transom_anchor_rule", "offset stern column"),
                                          "row_anchor_count": len(self.offsets.diagnostics.get("transom_row_anchors_index_x_halfbreadth", ())),
                                          "included_in_friction_area": False},
                      "wave_force_n": list(wave_forces),
                      "friction_force_n": list(friction_forces) if friction_forces is not None else None,
                      "omitted_components": ["viscous pressure/form factor", "breaking waves", "air",
                                             "appendages", "correlation allowance", "roughness"]},
        )
