"""Cox-de Boor evaluation of tensor-product rational NURBS surfaces."""

from __future__ import annotations

import numpy as np

from .hgeo import NurbsSurface


def basis_matrix(parameters: np.ndarray, knots: np.ndarray, order: int, cv_count: int) -> np.ndarray:
    """Return B-spline basis values for an OpenNURBS knot vector.

    OpenNURBS omits one knot at each end. Endpoint duplication restores the
    conventional vector. The upper domain endpoint is evaluated by its left
    limit, which also handles non-clamped knot vectors correctly.
    """
    parameters = np.asarray(parameters, dtype=float).reshape(-1)
    knots = np.asarray(knots, dtype=float)
    if order < 2 or cv_count < order or knots.shape != (cv_count + order - 2,):
        raise ValueError("Inconsistent NURBS order, control-point count or OpenNURBS knots")
    full = np.concatenate((knots[:1], knots, knots[-1:]))
    lower, upper = full[order - 1], full[cv_count]
    tolerance = 1e-12 * max(abs(upper - lower), 1.0)
    if not np.all(np.isfinite(parameters)) or np.any(parameters < lower - tolerance) or np.any(parameters > upper + tolerance):
        raise ValueError(f"Parameters must lie inside [{lower}, {upper}]")
    values = np.clip(parameters, lower, upper)
    values[values == upper] = np.nextafter(upper, lower)
    basis = ((values[:, None] >= full[:-1]) & (values[:, None] < full[1:])).astype(float)
    for degree in range(1, order):
        count = len(full) - degree - 1
        left_den = full[degree:degree + count] - full[:count]
        right_den = full[degree + 1:degree + count + 1] - full[1:count + 1]
        left = np.divide(values[:, None] - full[:count], left_den, out=np.zeros((len(values), count)), where=left_den != 0)
        right = np.divide(full[degree + 1:degree + count + 1] - values[:, None], right_den,
                          out=np.zeros((len(values), count)), where=right_den != 0)
        basis = left * basis[:, :count] + right * basis[:, 1:count + 1]
    # Clamped boundaries interpolate the end CV exactly. Preserving exact
    # planar coordinates matters for subsequent plane/triangle intersections.
    if np.all(full[:order] == lower):
        at_start = parameters == lower
        basis[at_start] = 0
        basis[at_start, 0] = 1
    if np.all(full[-order:] == upper):
        at_end = parameters == upper
        basis[at_end] = 0
        basis[at_end, -1] = 1
    return basis


def evaluate_surface(surface: NurbsSurface, u: np.ndarray, v: np.ndarray, *, grid: bool = False) -> np.ndarray:
    """Evaluate paired UV coordinates, or their tensor grid with ``grid=True``.

    Paired inputs broadcast and preserve their shape. The grid result has shape
    ``(len(u), len(v), 3)``. All output positions are in metres.
    """
    if grid:
        u, v = np.asarray(u, float).reshape(-1), np.asarray(v, float).reshape(-1)
        shape = (len(u), len(v), 3)
    else:
        u, v = np.broadcast_arrays(np.asarray(u, float), np.asarray(v, float))
        shape = u.shape + (3,)
    bu = basis_matrix(u, surface.knots_u, surface.order[0], surface.cv_count[0])
    bv = basis_matrix(v, surface.knots_v, surface.order[1], surface.cv_count[1])
    if grid:
        homogeneous = np.einsum("ai,ijk,bj->abk", bu, surface.cv, bv, optimize=True)
    else:
        homogeneous = np.einsum("ni,ijk,nj->nk", bu, surface.cv, bv, optimize=True)
    weights = homogeneous[..., 3]
    if np.any(np.abs(weights) < np.finfo(float).tiny):
        raise ValueError("NURBS evaluation encountered a zero homogeneous weight")
    return (homogeneous[..., :3] / weights[..., None]).reshape(shape)
