"""Tessellate sampled UV trim loops into outward-oriented SI triangles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from matplotlib.path import Path
import numpy as np
from scipy.spatial import Delaunay, QhullError

from .hgeo import Face, Geometry
from .nurbs import evaluate_surface


@dataclass
class TriangleMesh:
    triangles: np.ndarray
    face_ids: np.ndarray | None = None
    layers: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    uv_triangles: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.triangles = np.asarray(self.triangles, dtype=float)
        if self.triangles.size == 0:
            self.triangles = self.triangles.reshape(0, 3, 3)
        if self.triangles.ndim != 3 or self.triangles.shape[1:] != (3, 3):
            raise ValueError("Mesh triangles must have shape (n, 3, 3)")
        if not np.all(np.isfinite(self.triangles)):
            raise ValueError("Mesh triangle coordinates must be finite")
        count = len(self.triangles)
        self.face_ids = np.zeros(count, dtype=int) if self.face_ids is None else np.asarray(self.face_ids)
        self.layers = np.full(count, "HULL") if self.layers is None else np.asarray(self.layers)
        if self.face_ids.shape != (count,) or self.layers.shape != (count,):
            raise ValueError("Mesh face_ids and layers must have one entry per triangle")
        if self.uv_triangles is not None:
            self.uv_triangles = np.asarray(self.uv_triangles, float)
            if self.uv_triangles.shape != (count, 3, 2) or not np.isfinite(self.uv_triangles).all():
                raise ValueError('UV triangles must be finite and shaped (n,3,2)')

    @property
    def area_m2(self) -> float:
        t = self.triangles
        return float(0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum())


def _inside(points: np.ndarray, outer: list[np.ndarray], inner: list[np.ndarray]) -> np.ndarray:
    selected = np.zeros(len(points), dtype=bool) if outer else np.ones(len(points), dtype=bool)
    for polygon in outer:
        selected |= Path(polygon).contains_points(points)
    for polygon in inner:
        selected &= ~Path(polygon).contains_points(points)
    return selected


def _densify_boundary(polygon: np.ndarray, count: np.ndarray) -> np.ndarray:
    """Sample straight UV edges finely enough to follow curved 3D boundaries."""
    points = []
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        steps = max(1, int(np.ceil(np.max(np.abs(end - start) * (count - 1)))))
        points.append(start + (end - start) * (np.arange(steps) / steps)[:, None])
    return np.vstack(points)


def _away_from_boundary(points: np.ndarray, polygons: list[np.ndarray], gap: float) -> np.ndarray:
    """Remove grid points too close to any boundary segment, not just samples."""
    selected = np.ones(len(points), dtype=bool)
    for polygon in polygons:
        starts, ends = polygon, np.roll(polygon, -1, axis=0)
        edges = ends - starts
        norms = np.sum(edges * edges, axis=1)
        for start in range(0, len(points), 512):
            relative = points[start:start + 512, None, :] - starts
            projection = np.divide(np.sum(relative * edges, axis=2), norms,
                                   out=np.zeros(relative.shape[:2]), where=norms > 0)
            delta = relative - np.clip(projection, 0, 1)[..., None] * edges
            selected[start:start + 512] &= np.min(np.sum(delta * delta, axis=2), axis=1) > gap * gap
    return selected


# Trims may run past their surface's parameter domain by a modelling tolerance (Rhino joins, for one,
# adjust trims to close edge gaps). Within this fraction of the span they are clamped onto the domain.
TRIM_DOMAIN_TOLERANCE = 1e-5


def _inside_domain(uv: np.ndarray, lower: np.ndarray, upper: np.ndarray, face: Face) -> np.ndarray:
    excess = np.max(np.maximum(lower - uv, uv - upper) / (upper - lower))
    if excess > TRIM_DOMAIN_TOLERANCE:
        raise ValueError(f"Face {face.object_index}/{face.face_index}: a trim lies outside its surface "
                         f"({excess:.2e} of the parameter span)")
    return np.clip(uv, lower, upper) if excess > 0 else uv


def tessellate_face(face: Face, *, min_grid: int = 12, max_grid: int = 100, grid_factor: int = 3,
                    keep_degenerate: bool = False, include_uv: bool = False):
    """Return triangles for a face, respecting holes and ``rev`` orientation.

    Weighted parameter directions use at least 65 samples, needed for the
    analytic circular-prism volume tolerance. Straight ruled directions retain
    the usual ``clip(grid_factor * cv_count, min_grid, max_grid)`` rule.
    ``max_grid`` also caps this circular-direction refinement.

    ``keep_degenerate=True`` retains geometrically collapsed triangles after
    valid UV triangulation. Connectivity then depends only on knots, weights,
    trims and sampling options, supporting exact fixed-weight NURBS modes.
    Candidate checks must distinguish baseline singularities from new collapses.
    """
    if min_grid < 2 or max_grid < min_grid or grid_factor < 1:
        raise ValueError("Require 2 <= min_grid <= max_grid and grid_factor >= 1")
    surface = face.surface
    count = np.clip(np.asarray(surface.cv_count) * grid_factor, min_grid, max_grid)
    weights = surface.cv[..., 3]
    for axis in range(2):
        if surface.rational and np.max(np.abs(np.diff(weights, axis=axis)), initial=0) > 1e-12:
            count[axis] = max(count[axis], min(65, max_grid))
    u = np.linspace(*surface.domain_u, int(count[0]))
    v = np.linspace(*surface.domain_v, int(count[1]))
    uu, vv = np.meshgrid(u, v, indexing="ij")
    uv = np.column_stack((uu.ravel(), vv.ravel()))
    lower = np.array((u[0], v[0]))
    scale = np.array((u[-1] - u[0], v[-1] - v[0]))
    normalized = (uv - lower) / scale
    outer, inner, boundaries = [], [], []
    for loop in face.loops:
        polygon = _densify_boundary((_inside_domain(loop.uv, lower, lower + scale, face) - lower) / scale, count)
        if loop.type == "outer":
            outer.append(polygon)
        elif loop.type == "inner":
            inner.append(polygon)
        boundaries.append(polygon)
    if not outer:
        rectangle = np.array(((0, 0), (1, 0), (1, 1), (0, 1)), dtype=float)
        rectangle = _densify_boundary(rectangle, count)
        outer.append(rectangle)
        boundaries.append(rectangle)
    selected = _inside(normalized, outer, inner)
    gap = 0.2 / max(int(count.max()) - 1, 1)
    selected &= _away_from_boundary(normalized, boundaries, gap)
    boundary = np.vstack(boundaries)
    points = np.vstack((normalized[selected], boundary))
    # Round only the duplicate-detection keys, keeping evaluated UV unchanged.
    _, unique = np.unique(np.round(points, 14), axis=0, return_index=True)
    unique.sort()
    points = points[unique]
    xyz_grid = evaluate_surface(surface, u, v, grid=True).reshape(-1, 3)
    boundary_uv = lower + boundary * scale
    xyz_boundary = evaluate_surface(surface, boundary_uv[:, 0], boundary_uv[:, 1])
    xyz = np.vstack((xyz_grid[selected], xyz_boundary))[unique]
    if len(points) < 3:
        raise ValueError(f"Face {face.object_index}/{face.face_index} has fewer than three trim points")
    try:
        # Use the sampling grid's metric. On an anisotropic control net (e.g.
        # 892x6 CVs), isotropic [0,1]^2 triangulation connects distant points
        # along the sparsely sampled axis and overestimates a narrow curved
        # closure. Equal grid intervals in this metric retain local neighbors.
        simplices = Delaunay(points * (count - 1)).simplices
    except QhullError as exc:
        raise ValueError(f"Cannot triangulate face {face.object_index}/{face.face_index}: {exc}") from exc
    simplices = simplices[_inside(points[simplices].mean(axis=1), outer, inner)]
    parameter_triangles = points[simplices]
    first = parameter_triangles[:, 1] - parameter_triangles[:, 0]
    second = parameter_triangles[:, 2] - parameter_triangles[:, 0]
    signed_uv_area = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
    # Qhull can emit roundoff-sized UV triangles along straight trim segments.
    # Mapping three collinear UV points through a curved NURBS gives a finite
    # 3D chord triangle, spuriously adding surface area unless discarded here.
    edge_scale = np.maximum(np.sum(first * first, axis=1), np.sum(second * second, axis=1))
    valid = np.abs(signed_uv_area) > 128 * np.finfo(float).eps * edge_scale
    simplices, signed_uv_area = simplices[valid], signed_uv_area[valid]
    flip = (signed_uv_area < 0) ^ bool(face.rev)
    simplices[flip] = simplices[flip][:, [0, 2, 1]]
    triangles = xyz[simplices]
    uv_triangles = lower + points[simplices] * scale
    if keep_degenerate:
        return (triangles, uv_triangles) if include_uv else triangles
    # Singular parameter edges may collapse to one 3D point. They have no area.
    area_vectors = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    keep = np.linalg.norm(area_vectors, axis=1) > 1e-24
    return (triangles[keep], uv_triangles[keep]) if include_uv else triangles[keep]


def tessellate(geometry: Geometry, *, min_grid: int = 12, max_grid: int = 100, grid_factor: int = 3,
               keep_degenerate: bool = False, include_uv: bool = False) -> TriangleMesh:
    """Tessellate once; ``face_ids`` index ``geometry.faces`` and SI metadata."""
    triangles, face_ids, layers, uv = [], [], [], []
    for index, face in enumerate(geometry.faces):
        face_triangles = tessellate_face(face, min_grid=min_grid, max_grid=max_grid, grid_factor=grid_factor,
                                        keep_degenerate=keep_degenerate, include_uv=include_uv)
        if include_uv:
            face_triangles, face_uv = face_triangles
            uv.append(face_uv)
        triangles.append(face_triangles)
        face_ids.append(np.full(len(face_triangles), index, dtype=int))
        layers.append(np.full(len(face_triangles), face.layer))
    metadata = dict(geometry.metadata)
    metadata["tessellation"] = {"min_grid": min_grid, "max_grid": max_grid, "grid_factor": grid_factor,
                                "boundary_margin_fraction": 0.2, "rational_min_grid": 65,
                                "uv_metric": "grid_intervals",
                                "keep_degenerate": keep_degenerate,
                                "method": "sampled_uv_trim_delaunay"}
    metadata["face_table"] = [{"object_index": f.object_index, "face_index": f.face_index,
                               "layer": f.layer, "object_name": f.object_name, "rev": f.rev} for f in geometry.faces]
    joined = np.concatenate(triangles) if triangles else np.empty((0, 3, 3))
    area_vectors = np.cross(joined[:, 1] - joined[:, 0], joined[:, 2] - joined[:, 0])
    metadata["collapsed_triangle_count"] = int(np.count_nonzero(np.linalg.norm(area_vectors, axis=1) <= 1e-24))
    return TriangleMesh(joined,
                        np.concatenate(face_ids) if face_ids else np.empty(0, dtype=int),
                        np.concatenate(layers) if layers else np.empty(0, dtype=str), metadata,
                        np.concatenate(uv) if uv else None)
