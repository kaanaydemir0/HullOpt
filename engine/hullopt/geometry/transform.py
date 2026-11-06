"""Two-half longitudinal deformation applied directly to a baseline.

``PY-LACKENBY`` here denotes the project's explicit longitudinal-control-point
method, rather than an implementation certified equal to Maxsurf's proprietary
transformation. All lengths and shift coefficients are in metres.

A nonlinear map of NURBS control points generally differs from applying that
map to evaluated surface points. The mesh route is therefore an approximation
for initial guesses; the remeshed transformed NURBS is the authoritative model.
Positive derivative of the scalar map does not by itself certify NURBS fairness,
absence of self intersections or preservation of adjacent-face joins.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .hgeo import Geometry
from .tessellate import TriangleMesh


# max_{0<=xi<=1} |d/dxi [16*xi^2*(1-xi)^2]| = 16*sqrt(3)/9.
# Its extrema are xi=(3+-sqrt(3))/6, so monotonicity is checked analytically.
_MAX_BUMP_DERIVATIVE = 16.0 * np.sqrt(3.0) / 9.0


@dataclass(frozen=True)
class LongitudinalMap:
    """C1 map with independent aft/forward shifts and fixed three anchors.

    In either half, ``xi=(x-start)/(end-start)`` and
    ``x'=x+c*16*xi**2*(1-xi)**2``. Outside the two-half interval, x is unchanged.
    A positive coefficient moves that half's centre forward. Each coefficient
    must satisfy ``abs(c) < half_length / (16*sqrt(3)/9)``.
    """

    x_aft_m: float
    x_mid_m: float
    x_fwd_m: float
    aft_shift_m: float = 0.0
    fwd_shift_m: float = 0.0

    def __post_init__(self) -> None:
        values = (self.x_aft_m, self.x_mid_m, self.x_fwd_m, self.aft_shift_m, self.fwd_shift_m)
        if not all(np.isfinite(value) for value in values):
            raise ValueError("Longitudinal-map anchors and shifts must be finite")
        if not self.x_aft_m < self.x_mid_m < self.x_fwd_m:
            raise ValueError("Require x_aft_m < x_mid_m < x_fwd_m")
        limits = self.coefficient_limit_m
        if abs(self.aft_shift_m) >= limits[0] or abs(self.fwd_shift_m) >= limits[1] or self.minimum_derivative <= 0:
            raise ValueError("Longitudinal map must have a strictly positive derivative everywhere")

    @property
    def coefficient_limit_m(self) -> tuple[float, float]:
        """Exclusive absolute coefficient bounds for a monotone scalar map."""
        return ((self.x_mid_m - self.x_aft_m) / _MAX_BUMP_DERIVATIVE,
                (self.x_fwd_m - self.x_mid_m) / _MAX_BUMP_DERIVATIVE)

    @property
    def minimum_derivative(self) -> float:
        aft_length = self.x_mid_m - self.x_aft_m
        fwd_length = self.x_fwd_m - self.x_mid_m
        if aft_length <= 0 or fwd_length <= 0:
            return float("-inf")
        return float(1.0 - _MAX_BUMP_DERIVATIVE * max(abs(self.aft_shift_m) / aft_length,
                                                     abs(self.fwd_shift_m) / fwd_length))

    @property
    def is_identity(self) -> bool:
        return self.aft_shift_m == 0.0 and self.fwd_shift_m == 0.0

    def _evaluate(self, x: np.ndarray | float, *, derivative: bool) -> np.ndarray | float:
        source = np.asarray(x, dtype=float)
        if not np.all(np.isfinite(source)):
            raise ValueError("Longitudinal coordinates must be finite")
        result = np.ones_like(source) if derivative else source.copy()
        for start, end, coefficient in ((self.x_aft_m, self.x_mid_m, self.aft_shift_m),
                                         (self.x_mid_m, self.x_fwd_m, self.fwd_shift_m)):
            if coefficient == 0.0:
                continue
            selected = (source > start) & (source < end)
            xi = (source[selected] - start) / (end - start)
            if derivative:
                result[selected] += coefficient * 32.0 * xi * (1 - xi) * (1 - 2 * xi) / (end - start)
            else:
                result[selected] += coefficient * 16.0 * xi**2 * (1 - xi)**2
        return float(result) if source.ndim == 0 else result

    def map_x(self, x: np.ndarray | float) -> np.ndarray | float:
        """Map scalar or array coordinates; the input array is never modified."""
        return self._evaluate(x, derivative=False)

    def derivative(self, x: np.ndarray | float) -> np.ndarray | float:
        return self._evaluate(x, derivative=True)

    def to_dict(self) -> dict[str, float | str]:
        return {"x_aft_m": float(self.x_aft_m), "x_mid_m": float(self.x_mid_m),
                "x_fwd_m": float(self.x_fwd_m), "aft_shift_m": float(self.aft_shift_m),
                "fwd_shift_m": float(self.fwd_shift_m), "basis": "16*xi^2*(1-xi)^2",
                "outside_domain": "identity", "minimum_derivative": self.minimum_derivative}


def _selected_layers(layers: Iterable[str] | None, available: set[str]) -> set[str]:
    selected = available if layers is None else ({layers} if isinstance(layers, str) else set(layers))
    if not selected or selected - available:
        raise ValueError(f"Requested transform layers unavailable: {sorted((selected - available) or selected)}")
    return selected


def _require_baseline(metadata: dict) -> None:
    if "longitudinal_transform" in metadata:
        raise ValueError("Generate every candidate directly from its untransformed baseline; chained transforms are forbidden")


def _transform_record(xmap: LongitudinalMap, selected: set[str], representation: str) -> dict:
    return {"method": "PY-LACKENBY", "version": "1", "map": xmap.to_dict(),
            "selected_layers": sorted(selected), "representation": representation,
            "source_is_baseline": True,
            "requires_nurbs_verification": representation == "mesh_vertices_approximation"}


def transform_geometry(geometry: Geometry, xmap: LongitudinalMap,
                       layers: Iterable[str] | None = ("HULL",)) -> Geometry:
    """Copy a baseline and map Cartesian CV x, retaining homogeneous weights.

    For homogeneous CV ``[X,Y,Z,W]``, only ``X`` changes to
    ``map_x(X/W)*W``. All knot vectors, UV trims, faces, other coordinates and
    metadata are copied. Layers outside ``layers`` remain geometrically fixed.
    The input's baseline geometry and every mutable array stay untouched.
    """
    _require_baseline(geometry.metadata)
    selected = _selected_layers(layers, {face.layer for face in geometry.faces})
    result = deepcopy(geometry)
    if not xmap.is_identity:
        for face in result.faces:
            if face.layer not in selected:
                continue
            cv = face.surface.cv
            weights = cv[..., 3]
            if np.any(weights == 0):
                raise ValueError("Cannot map a homogeneous NURBS control point with zero weight")
            x = cv[..., 0] / weights
            mapped = xmap.map_x(x)
            changed = mapped != x
            # Keep fixed-anchor/outside-domain homogeneous X exactly, avoiding
            # an unnecessary divide/multiply round trip for rational CVs.
            cv[..., 0][changed] = mapped[changed] * weights[changed]
    result.metadata["longitudinal_transform"] = _transform_record(xmap, selected, "nurbs_control_points")
    return result


def transform_mesh(mesh: TriangleMesh, xmap: LongitudinalMap,
                   layers: Iterable[str] | None = None) -> TriangleMesh:
    """Map baseline mesh vertices for an explicitly labelled approximation.

    This operation preserves connectivity and orientation order. It does not
    assert equivalence to remeshing ``transform_geometry`` and must not replace
    NURBS validation of a final candidate.
    """
    _require_baseline(mesh.metadata)
    selected = _selected_layers(layers, set(mesh.layers.tolist()))
    result = deepcopy(mesh)
    if not xmap.is_identity:
        active = np.isin(result.layers, sorted(selected))
        result.triangles[active, :, 0] = xmap.map_x(result.triangles[active, :, 0])
    result.metadata["longitudinal_transform"] = _transform_record(xmap, selected, "mesh_vertices_approximation")
    return result
