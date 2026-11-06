"""A two-half Lackenby-type control-point transform at fixed displacement.

This is a documented smooth longitudinal deformation family, not a claim to
reproduce Maxsurf's proprietary implementation. All evaluations start from the
same baseline. Rational weights and UV tessellation are fixed: therefore the
evaluated x coordinates are *exactly linear* in the two control-point shifts.
Two precomputed NURBS modes make target solving inexpensive without replacing
control-point mapping by the different pointwise map of mesh vertices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from ..geometry.hgeo import Geometry
from ..geometry.tessellate import TriangleMesh, tessellate
from ..geometry.transform import LongitudinalMap, transform_geometry
from ..hydro import Hydrostatics, measure


class TargetInfeasibleError(ValueError):
    """The target cannot be reached within the specified shape/draft bounds."""


@dataclass
class LackenbyResult:
    geometry: Geometry
    mesh: TriangleMesh
    hydrostatics: Hydrostatics
    baseline: Hydrostatics
    xmap: LongitudinalMap
    targets: dict[str, float]
    residuals: dict[str, float]
    iterations: int
    function_evaluations: int
    mode_reconstruction_max_error_m: float
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self):
        return dict(method="PY-LACKENBY-CONTROL-POINTS", xmap=self.xmap.to_dict(),
                    targets=self.targets, residuals=self.residuals, iterations=self.iterations,
                    function_evaluations=self.function_evaluations,
                    mode_reconstruction_max_error_m=self.mode_reconstruction_max_error_m,
                    hydrostatics=self.hydrostatics.to_dict(), baseline=self.baseline.to_dict(),
                    history=self.history,
                    definitions={"delta_lcb_fraction_lwl": "absolute LCB shift divided by baseline LWL, positive forward",
                                 "cp": "measured V/(measured maximum sectional area * measured LWL)",
                                 "shape_family": "two C1 quartic shifts of homogeneous NURBS control points; not exact pointwise morph",
                                 "validity": "monotone generating map does not itself certify NURBS closure or absence of intersections"})


@dataclass
class PreparedLackenby:
    geometry: Geometry
    mesh: TriangleMesh
    baseline: Hydrostatics
    aft_mode_x: np.ndarray
    fwd_mode_x: np.ndarray
    x_aft_m: float
    x_mid_m: float
    x_fwd_m: float
    tessellation_options: dict[str, int]
    rho_kg_m3: float
    section_count: int
    layers: tuple[str, ...]

    def mapping(self, aft_shift_m=0.0, fwd_shift_m=0.0):
        return LongitudinalMap(self.x_aft_m, self.x_mid_m, self.x_fwd_m,
                               float(aft_shift_m), float(fwd_shift_m))

    def candidate_mesh(self, aft_shift_m: float, fwd_shift_m: float) -> TriangleMesh:
        mapping = self.mapping(aft_shift_m, fwd_shift_m)
        triangles = self.mesh.triangles.copy()
        triangles[:, :, 0] += aft_shift_m * self.aft_mode_x + fwd_shift_m * self.fwd_mode_x
        return TriangleMesh(triangles, self.mesh.face_ids.copy(), self.mesh.layers.copy(),
                            {"longitudinal_transform": mapping.to_dict(),
                             "representation": "exact evaluated fixed-weight NURBS control-point modes"})

    def solve(self, delta_cp: float = 0.01, delta_lcb_fraction_lwl: float = 0.0, *,
              draft_change_limit_m: float = 0.05, volume_tolerance_rel: float = 1e-6,
              cp_tolerance_abs: float = 1e-5, lcb_tolerance_fraction_lwl: float = 1e-5,
              maximum_z_wl_m: float | None = None, max_function_evaluations: int = 45,
              minimum_map_derivative: float = 0.15) -> LackenbyResult:
        values = (delta_cp, delta_lcb_fraction_lwl, draft_change_limit_m, volume_tolerance_rel,
                  cp_tolerance_abs, lcb_tolerance_fraction_lwl, minimum_map_derivative)
        if not np.isfinite(values).all():
            raise ValueError("Transform targets and tolerances must be finite.")
        if min(draft_change_limit_m, volume_tolerance_rel, cp_tolerance_abs, lcb_tolerance_fraction_lwl) <= 0:
            raise ValueError("Draft limit and target tolerances must be positive.")
        if not 0 < minimum_map_derivative < 1:
            raise ValueError("minimum_map_derivative must lie between zero and one.")
        if not isinstance(max_function_evaluations, int) or max_function_evaluations < 1:
            raise ValueError("max_function_evaluations must be a positive integer.")
        baseline = self.baseline
        target_cp = float(baseline.cp + delta_cp)
        target_lcb = baseline.lcb_m + delta_lcb_fraction_lwl * baseline.lwl_m
        if not 0 < target_cp <= 1:
            raise TargetInfeasibleError("Target Cp must lie in (0, 1].")
        z_min = baseline.z_wl_m - draft_change_limit_m
        z_max = baseline.z_wl_m + draft_change_limit_m
        if maximum_z_wl_m is not None:
            if not np.isfinite(maximum_z_wl_m):
                raise ValueError("maximum_z_wl_m must be finite.")
            z_max = min(z_max, maximum_z_wl_m)
        if z_max <= z_min or not z_min <= baseline.z_wl_m <= z_max:
            raise TargetInfeasibleError("Baseline waterline is outside the permitted clearance/draft bracket.")
        aft_length = self.x_mid_m - self.x_aft_m
        fwd_length = self.x_fwd_m - self.x_mid_m
        # Work with dimensionless shifts (fraction of each half length) and
        # dimensionless WL change. A common bound guarantees map monotonicity.
        derivative_max = 16 * np.sqrt(3) / 9
        coefficient_bound = (1 - minimum_map_derivative) / derivative_max
        lower = [-coefficient_bound, -coefficient_bound, -1.0]
        upper = [coefficient_bound, coefficient_bound, (z_max - baseline.z_wl_m) / draft_change_limit_m]
        history = []

        def evaluate(parameters):
            aft, fwd = float(parameters[0] * aft_length), float(parameters[1] * fwd_length)
            z = float(baseline.z_wl_m + parameters[2] * draft_change_limit_m)
            mesh = self.candidate_mesh(aft, fwd)
            hydro = measure(mesh, z, rho_kg_m3=self.rho_kg_m3, section_count=self.section_count)
            residual = np.array([(hydro.volume_m3 - baseline.volume_m3) / baseline.volume_m3,
                                 hydro.cp - target_cp, (hydro.lcb_m - target_lcb) / baseline.lwl_m])
            history.append(dict(aft_shift_m=aft, fwd_shift_m=fwd, z_wl_m=z,
                                volume_error_rel=float(residual[0]), cp_error=float(residual[1]),
                                lcb_error_fraction_lwl=float(residual[2])))
            return residual, hydro, mesh

        if delta_cp == 0 and delta_lcb_fraction_lwl == 0:
            parameters = np.zeros(3)
            evaluate(parameters)
            iterations = 0
        else:
            solved = least_squares(lambda p: evaluate(p)[0], np.zeros(3), bounds=(lower, upper),
                                   method="trf", diff_step=1e-4, xtol=1e-10, ftol=1e-10,
                                   gtol=1e-10, max_nfev=max_function_evaluations)
            parameters, iterations = solved.x, int(solved.nfev)
        residual, _, interpolated = evaluate(parameters)
        targets = dict(volume_m3=baseline.volume_m3, cp=target_cp, lcb_m=target_lcb,
                       delta_cp=float(delta_cp), delta_lcb_fraction_lwl=float(delta_lcb_fraction_lwl))
        limits = np.array([volume_tolerance_rel, cp_tolerance_abs, lcb_tolerance_fraction_lwl])
        if np.any(np.abs(residual) > limits):
            raise TargetInfeasibleError("Cp/LCB/volume target not attained within bounded map and draft: "
                                        f"residuals [relative V, Cp, LCB/L]={residual.tolist()}; "
                                        f"limits={limits.tolist()}.")
        mapping = self.mapping(parameters[0] * aft_length, parameters[1] * fwd_length)
        geometry = transform_geometry(self.geometry, mapping, layers=self.layers)
        # Never deliver a candidate solely on the strength of a modal shortcut.
        remeshed = tessellate(geometry, **self.tessellation_options)
        _assert_same_topology(self.mesh, remeshed)
        reconstruction = float(np.max(np.abs(remeshed.triangles - interpolated.triangles)))
        if reconstruction > 1e-9:
            raise RuntimeError(f"Control-point mode reconstruction differs from remeshed NURBS by {reconstruction:.6g} m.")
        z = float(baseline.z_wl_m + parameters[2] * draft_change_limit_m)
        hydro = measure(remeshed, z, rho_kg_m3=self.rho_kg_m3, section_count=self.section_count)
        residuals = dict(volume_relative=(hydro.volume_m3 / baseline.volume_m3 - 1),
                         cp_absolute=float(hydro.cp - target_cp),
                         lcb_fraction_lwl=(hydro.lcb_m - target_lcb) / baseline.lwl_m)
        if np.any(np.abs(list(residuals.values())) > limits):
            raise TargetInfeasibleError("Remeshed NURBS candidate fails target tolerances.")
        return LackenbyResult(geometry, remeshed, hydro, baseline, mapping, targets, residuals,
                              iterations, len(history), reconstruction, history)


def _assert_same_topology(reference, candidate):
    if (reference.triangles.shape != candidate.triangles.shape or
            not np.array_equal(reference.face_ids, candidate.face_ids) or
            not np.array_equal(reference.layers, candidate.layers)):
        raise ValueError("NURBS transform changed tessellation cardinality; fixed-mode solver is not applicable.")
    if not np.allclose(reference.triangles[:, :, 1:], candidate.triangles[:, :, 1:], rtol=0, atol=1e-12):
        raise ValueError("Longitudinal-only transform changed tessellation y/z coordinates or ordering.")


def prepare_lackenby(geometry: Geometry, reference_z_wl_m: float, *,
                     tessellation_options: dict[str, int] | None = None,
                     rho_kg_m3: float = 1025.0, section_count: int = 161,
                     layers: tuple[str, ...] = ("HULL",)) -> PreparedLackenby:
    """Tessellate baseline and two fixed-weight control-point modes once."""
    options = dict(tessellation_options or {})
    # UV topology must be independent of coefficients for exact linear modes.
    # Legitimate surface poles may have zero 3D area at only some coefficients.
    options["keep_degenerate"] = True
    mesh = tessellate(geometry, **options)
    baseline = measure(mesh, reference_z_wl_m, rho_kg_m3=rho_kg_m3, section_count=section_count)
    mid = float(baseline.section_x_m[np.argmax(baseline.section_area_m2)])
    if not baseline.x_aft_m < mid < baseline.x_fwd_m:
        raise ValueError("Maximum-area station must be interior to the waterline for a two-half transform.")
    anchors = (baseline.x_aft_m, mid, baseline.x_fwd_m)
    amplitude = min(mid - anchors[0], anchors[2] - mid) * 0.05
    modes = []
    for aft, fwd in ((amplitude, 0.0), (0.0, amplitude)):
        mode_geometry = transform_geometry(geometry, LongitudinalMap(*anchors, aft, fwd), layers=layers)
        mode_mesh = tessellate(mode_geometry, **options)
        _assert_same_topology(mesh, mode_mesh)
        modes.append((mode_mesh.triangles[:, :, 0] - mesh.triangles[:, :, 0]) / amplitude)
    return PreparedLackenby(geometry, mesh, baseline, *modes, *anchors, options, rho_kg_m3, section_count, layers)
