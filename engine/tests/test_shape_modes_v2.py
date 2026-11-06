"""Shape modes v2 on a frame derived from a synthetic hull: C2 blends, linearity, centreline and BWL kept."""
import numpy as np
import pytest

from hullopt.geometry import tessellate
from hullopt.optimize.mode_frame import derive_frame
from hullopt.optimize.shape_modes_v2 import MODE_NAMES_V2, bump3, displacement_v2, mode_displacement_v2, s5
from tests.hydro_fixtures import geometry_from_hgeo, sheer_hull_hgeo

Z_WL = 2.2
MESH = tessellate(geometry_from_hgeo(sheer_hull_hgeo(40.0, 8.0, 4.0)), min_grid=40, max_grid=60, grid_factor=3)
F = derive_frame(MESH, Z_WL)
X0, X1 = F.x_min, F.x_max
Z_HIGH = 4.9            # just below the highest deck edge of the synthetic hull


def _x(fraction):
    return X0 + fraction * (X1 - X0)


def _jump_ratio(fn, t0, t1, n):
    """max |second-difference jump| at step h and at h/2: C2 -> ratio ~2, C1 -> ratio ~1."""
    out = []
    for m in (n, 2 * n):
        t = np.linspace(t0, t1, m)
        h = t[1] - t[0]
        d2 = np.diff(fn(t), 2) / h ** 2
        out.append(np.abs(np.diff(d2)).max())
    if out[1] < 1e-6:          # constant / linear / quadratic along this line: trivially smooth
        return np.inf
    return out[0] / out[1]


def test_blend_functions_are_c2():
    assert _jump_ratio(s5, -0.5, 1.5, 4001) > 1.8
    assert _jump_ratio(bump3, -0.5, 1.5, 4001) > 1.8
    assert _jump_ratio(lambda t: np.clip(t, 0, 1) ** 2, -0.5, 1.5, 4001) < 1.2   # v1 ramp is not C2


# Lines through the frame: (varying axis, the two fixed coordinates as fractions).
# Axis 0 (x varies): (y / y_ref, z / z_wl); axis 1 (y varies): (x along the frame, z / z_wl);
# axis 2 (z varies): (x along the frame, y / y_ref).
LINES = [(0, (0.5, 0.55)), (0, (0.75, 1.18)), (2, (0.12, 0.75)), (2, (0.9, 0.25)), (2, (0.45, 0.85)),
         (1, (0.2, 0.9))]


def _fixed(axis, fixed):
    a, b = fixed
    if axis == 0:
        return a * F.y_ref, b * Z_WL
    if axis == 1:
        return _x(a), b * Z_WL
    return _x(a), b * F.y_ref


@pytest.mark.parametrize("name", MODE_NAMES_V2)
@pytest.mark.parametrize("axis,fixed", LINES)
def test_modes_are_c2_along_lines(name, axis, fixed):
    lo, hi = {0: (X0 - 1.0, X1 + 1.0), 1: (0.0, 1.1 * F.y_ref), 2: (0.1, Z_HIGH)}[axis]
    first, second = _fixed(axis, fixed)

    def fn(t):
        p = np.zeros((len(t), 3))
        p[:, axis] = t
        others = [i for i in range(3) if i != axis]
        p[:, others[0]], p[:, others[1]] = first, second
        return mode_displacement_v2(name, p, F).sum(axis=1)
    if np.allclose(fn(np.linspace(lo, hi, 50)), 0):
        return
    assert _jump_ratio(fn, lo, hi, 20001) > 1.6


def test_linearity_and_identity():
    rng = np.random.default_rng(1)
    p = rng.uniform([X0, 0.0, 0.1], [X1, F.y_ref, Z_HIGH], size=(500, 3))
    a = {n: rng.uniform(-0.3, 0.3) for n in MODE_NAMES_V2}
    b = {n: rng.uniform(-0.3, 0.3) for n in MODE_NAMES_V2}
    ab = {n: a[n] + b[n] for n in MODE_NAMES_V2}
    assert np.allclose(displacement_v2(p, F, ab), displacement_v2(p, F, a) + displacement_v2(p, F, b))
    assert np.abs(displacement_v2(p, F, {})).max() == 0.0


def test_bwl_unchanged_by_midbody_modes():
    x = np.linspace(X0, X1, 200)
    p = np.stack([x, np.full_like(x, F.y_ref), np.full_like(x, F.z_wl - F.mid_wl_gap)], axis=1)
    for n in ("bilge_full", "deadrise"):
        assert np.abs(mode_displacement_v2(n, p, F)).max() < 1e-12


def test_centreline_stays_on_centreline():
    p = np.stack([np.linspace(X0, X1, 100), np.zeros(100), np.linspace(0.1, Z_HIGH, 100)], axis=1)
    for n in MODE_NAMES_V2:
        assert np.abs(mode_displacement_v2(n, p, F)[:, 1]).max() == 0.0
