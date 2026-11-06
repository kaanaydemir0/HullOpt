"""Section areas by a two-dimensional divergence integral.

The input skin must close below the waterline and have outward normals.
The missing waterplane cap contributes zero to integral(y*n_y ds).
"""

from __future__ import annotations

import numpy as np


def triangles_array(mesh) -> np.ndarray:
    triangles = np.asarray(getattr(mesh, "triangles", mesh), dtype=float)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise ValueError("Expected triangles with shape (n, 3, 3), in metres.")
    if not triangles.size or not np.isfinite(triangles).all():
        raise ValueError("Mesh must contain finite triangles.")
    return triangles


def plane_segments(triangles: np.ndarray, coordinate: float, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """Intersect triangles with a plane using a consistent half-open rule.

    Returns segments and the indices of their source triangles. Coplanar
    triangles contribute no segment. This avoids duplicate edges at vertices.
    """
    values = triangles[:, :, axis]
    minimum, maximum = values.min(axis=1), values.max(axis=1)
    indices = np.flatnonzero((minimum <= coordinate) & (maximum > coordinate))
    if not indices.size:
        return np.empty((0, 2, 3)), indices
    tri = triangles[indices]
    points = np.empty((len(tri), 3, 3))
    valid = np.zeros((len(tri), 3), dtype=bool)
    for edge, (a, b) in enumerate(((0, 1), (1, 2), (2, 0))):
        first, second = tri[:, a], tri[:, b]
        start, end = first[:, axis], second[:, axis]
        crosses = (start <= coordinate) != (end <= coordinate)
        valid[:, edge] = crosses
        fraction = np.divide(coordinate - start, end - start,
                             out=np.zeros(len(tri)), where=crosses)
        points[:, edge] = first + fraction[:, None] * (second - first)
    keep = valid.sum(axis=1) == 2
    return points[keep][valid[keep]].reshape(-1, 2, 3), indices[keep]


def clip_segments_below(segments: np.ndarray, z_wl_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Clip line segments to z<=WL, returning kept segments and source indices."""
    below = segments[:, :, 2] <= z_wl_m
    indices = np.flatnonzero(below.any(axis=1))
    kept = segments[indices].copy()
    mixed = below[indices].sum(axis=1) == 1
    if mixed.any():
        pos = np.flatnonzero(mixed)
        first, second = kept[pos, 0], kept[pos, 1]
        fraction = (z_wl_m - first[:, 2]) / (second[:, 2] - first[:, 2])
        intersections = first + fraction[:, None] * (second - first)
        above_index = (~below[indices][pos]).argmax(axis=1)
        kept[pos, above_index] = intersections
    return kept, indices


def sectional_areas(mesh, xs, z_wl_m: float) -> np.ndarray:
    """Return submerged areas (m²) at x stations (m), +x forward.

    At a mesh end plane, the one-sided interior limit is used. The routine
    does not infer closure by joining unmatched intersection endpoints.
    """
    triangles = triangles_array(mesh)
    stations = np.asarray(xs, dtype=float)
    if stations.ndim != 1 or not np.isfinite(stations).all() or not np.isfinite(z_wl_m):
        raise ValueError("Stations must be a finite one-dimensional array and WL must be finite.")
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    yz_norm = np.linalg.norm(normals[:, 1:], axis=1)
    usable_normals = yz_norm > np.linalg.norm(normals, axis=1) * (64 * np.finfo(float).eps)
    xmin = triangles[:, :, 0].min(axis=1)
    xmax = triangles[:, :, 0].max(axis=1)
    lo, hi = xmin.min(), xmax.max()
    endpoint_step = max(hi - lo, 1.0) * 1e-10
    areas = np.zeros(len(stations))
    for i, station in enumerate(stations):
        x = station
        if x == lo:
            x = lo + endpoint_step
        elif x == hi:
            x = hi - endpoint_step
        candidates = np.flatnonzero((xmin <= x) & (xmax > x) & usable_normals)
        segments, selected = plane_segments(triangles[candidates], x, 0)
        if not len(segments):
            continue
        segments, clipped = clip_segments_below(segments, z_wl_m)
        if not len(segments):
            continue
        source = candidates[selected[clipped]]
        length = np.linalg.norm(segments[:, 1, 1:] - segments[:, 0, 1:], axis=1)
        y_mid = segments[:, :, 1].mean(axis=1)
        areas[i] = np.sum(y_mid * normals[source, 1] / yz_norm[source] * length)
    return areas


sectional_area_curve = sectional_areas
