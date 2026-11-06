"""Geometric target solving on a reproducible smooth synthetic hull."""

import unittest
import numpy as np

from hullopt.geometry import Geometry, Face, NurbsSurface, TrimLoop, basis_matrix
from hullopt.geometry.tessellate import tessellate
from hullopt.geometry.transform import transform_geometry
from hullopt.hydro import measure
from hullopt.optimize.lackenby import prepare_lackenby, TargetInfeasibleError


def wigley_geometry(length=10.0, breadth=2.0, depth=1.0):
    """Exact polynomial half breadth B/2*(1-(2x/L-1)^2)*(1-(z/T)^2).

    z is extended to +0.5T so WL can move within an explicit dry deck margin.
    Six longitudinal CVs keep the design modes from being rank deficient.
    """
    ku = np.array([0, 0, .25, .5, .75, 1, 1.])
    kv = np.array([0, 0, 1, 1.])
    greville = np.array([0, .125, .375, .625, .875, 1.])
    bu = basis_matrix(greville, ku, 3, 6)
    fx = np.linalg.solve(bu, 4 * greville * (1 - greville))
    fz = np.array([0, 1.5, .75])
    faces = []
    for sign in (-1, 1):
        cv = np.ones((6, 3, 4))
        cv[:, :, 0] = length * greville[:, None]
        cv[:, :, 1] = sign * breadth / 2 * fx[:, None] * fz[None, :]
        cv[:, :, 2] = depth * np.array([-1, -.25, .5])[None, :]
        surface = NurbsSurface((3, 3), cv, ku, kv)
        trim = TrimLoop("outer", [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])
        faces.append(Face(surface, (trim,), rev=sign > 0, object_index=len(faces)))
    return Geometry(tuple(faces))


class LackenbySolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = prepare_lackenby(wigley_geometry(), 0.0,
                                        tessellation_options={"min_grid": 31, "max_grid": 31},
                                        section_count=81)

    def test_synthetic_baseline_matches_polynomial_hull(self):
        self.assertLess(abs(self.prepared.baseline.volume_m3 / (80 / 9) - 1), .003)
        self.assertLess(abs(self.prepared.baseline.cp / (2 / 3) - 1), .003)

    def test_modes_equal_nurbs_remeshing_not_pointwise_mesh_map(self):
        prepared = self.prepared
        mapping = prepared.mapping(-.08, .07)
        shortcut = prepared.candidate_mesh(-.08, .07)
        exact = tessellate(transform_geometry(prepared.geometry, mapping), **prepared.tessellation_options)
        np.testing.assert_allclose(shortcut.triangles, exact.triangles, atol=1e-12, rtol=0)
        np.testing.assert_array_equal(shortcut.triangles[:, :, 1:], prepared.mesh.triangles[:, :, 1:])

    def test_identity_keeps_baseline_and_remeasures(self):
        before = self.prepared.mesh.triangles.copy()
        result = self.prepared.solve(0.0, 0.0)
        np.testing.assert_array_equal(result.mesh.triangles, before)
        self.assertEqual(result.hydrostatics.volume_m3, self.prepared.baseline.volume_m3)
        self.assertTrue(result.xmap.is_identity)
        self.assertLess(result.mode_reconstruction_max_error_m, 1e-12)

    def test_cp_increase_at_fixed_volume_and_lcb(self):
        result = self.prepared.solve(.01, 0.0)
        self.assertLess(abs(result.residuals["volume_relative"]), 1e-6)
        self.assertLess(abs(result.residuals["cp_absolute"]), 1e-5)
        self.assertLess(abs(result.residuals["lcb_fraction_lwl"]), 1e-5)
        self.assertLess(result.hydrostatics.z_wl_m, 0.0)
        self.assertLess(abs(result.hydrostatics.z_wl_m), .05)

    def test_lcb_direction_and_independent_baseline_generation(self):
        before = self.prepared.geometry.faces[0].surface.cv.copy()
        positive = self.prepared.solve(0.0, .002)
        negative = self.prepared.solve(0.0, -.002)
        self.assertGreater(positive.hydrostatics.lcb_m, self.prepared.baseline.lcb_m)
        self.assertLess(negative.hydrostatics.lcb_m, self.prepared.baseline.lcb_m)
        np.testing.assert_array_equal(self.prepared.geometry.faces[0].surface.cv, before)

    def test_unreachable_draft_and_invalid_inputs_fail(self):
        with self.assertRaises(TargetInfeasibleError):
            self.prepared.solve(.03, 0.0, draft_change_limit_m=.0001)
        for kwargs in ({"delta_cp": float("nan")}, {"minimum_map_derivative": 1.0},
                       {"draft_change_limit_m": 0}, {"maximum_z_wl_m": -.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.prepared.solve(**kwargs)


if __name__ == "__main__":
    unittest.main()
