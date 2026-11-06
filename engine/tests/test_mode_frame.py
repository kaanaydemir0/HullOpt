"""Mode frame and fairness plan derived from the hull."""
import unittest

import numpy as np

from hullopt.geometry import tessellate
from hullopt.hydro.hydrostatics import measure
from hullopt.hydro.sections import plane_segments
from hullopt.optimize.fairness import FairnessPlan, FairnessRules, LineSet
from hullopt.optimize.mode_frame import FrameRules, _top_curve, check_frame, derive_frame, upper_edge
from hullopt.optimize.shape_modes_v2 import MODE_NAMES_V2, mode_displacement_v2
from tests.hydro_fixtures import box_triangles, geometry_from_hgeo, sheer_hull_hgeo

def hull(scale=1.0):
    g = geometry_from_hgeo(sheer_hull_hgeo(40.0 * scale, 8.0 * scale, 4.0 * scale))
    return tessellate(g, min_grid=40, max_grid=60, grid_factor=3)


class DerivedFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh = hull()
        cls.z = 2.2
        cls.frame = derive_frame(cls.mesh, cls.z)

    def test_upper_edge_equals_plane_cuts(self):
        xs = np.linspace(-19.9, 19.9, 37)
        cut = [plane_segments(self.mesh.triangles, float(x), 0)[0][:, :, 2].max() for x in xs]
        np.testing.assert_allclose(upper_edge(self.mesh.triangles, xs), cut, rtol=0, atol=1e-12)
        self.assertTrue(np.isnan(upper_edge(self.mesh.triangles, [25.0])[0]))

    def test_frame_follows_the_hull(self):
        f = self.frame
        h = measure(self.mesh, self.z, include_sections=True)
        self.assertAlmostEqual(f.x_bow0, h.x_aft_m + 0.43 * h.lwl_m)
        self.assertAlmostEqual(f.z_lift_lo, h.keel_z_m + 0.55 * h.draft_m)
        self.assertAlmostEqual(f.y_ref, 4.0, delta=0.01)       # largest half-beam of the mesh
        self.assertEqual(check_frame(f, self.mesh), [])
        xs = np.linspace(f.x_min + 0.05, f.x_max - 0.05, 2001)
        gap = upper_edge(self.mesh.triangles, xs) - f.z_top(xs)
        self.assertGreaterEqual(float(np.min(gap)), 0.03 * h.draft_m - 1e-9)

    def test_upper_edge_never_moves(self):
        f = self.frame
        x = np.linspace(f.x_min, f.x_max, 400)
        for name in [n for n in MODE_NAMES_V2 if not n.startswith("lack")]:
            for dz in (0.0, 0.05, 0.5):
                p = np.stack([x, np.full_like(x, 1.5), f.z_top(x) + dz], axis=1)
                self.assertLess(np.abs(mode_displacement_v2(name, p, f)).max(), 1e-12, name)

    def test_modes_stay_c2_with_the_spline_top(self):
        f = self.frame
        for name in ("bow_fine", "stern_narrow", "stern_lift"):
            for x in (-15.0, 12.0, 17.0):
                def along_z(t):
                    p = np.stack([np.full_like(t, x), np.full_like(t, 1.0), t], axis=1)
                    return mode_displacement_v2(name, p, f).sum(axis=1)
                out = []
                for m in (20001, 40002):
                    t = np.linspace(0.2, 5.0, m)
                    d2 = np.diff(along_z(t), 2) / (t[1] - t[0]) ** 2
                    out.append(np.abs(np.diff(d2)).max())
                if out[1] > 1e-6:
                    self.assertGreater(out[0] / out[1], 1.6, f"{name} at x={x}")

    def test_geometrically_similar_hulls_get_similar_frames(self):
        small = derive_frame(hull(0.25), 0.25 * self.z)
        a, b = self.frame.to_dict(), small.to_dict()
        for key, value in a.items():
            np.testing.assert_allclose(np.asarray(b[key]), 0.25 * np.asarray(value), rtol=1e-9, atol=1e-12, err_msg=key)

    def test_fairness_verdict_is_scale_free(self):
        amps = {"lack_aft": 0.4, "bow_fine": 0.08, "bilge_full": 0.05, "stern_lift": 0.2, "deadrise": 0.1}
        results = []
        for scale in (1.0, 0.25):
            mesh = hull(scale) if scale != 1.0 else self.mesh
            frame = derive_frame(mesh, scale * self.z)
            plan = FairnessPlan.from_frame(frame)
            lines = LineSet.from_mesh(mesh.triangles, frame, plan=plan)
            lines.precompute(lambda n, p: mode_displacement_v2(n, p, frame), MODE_NAMES_V2)
            scaled = {k: v * (scale if k in ("lack_aft", "lack_fwd", "stern_lift", "deadrise") else 1.0)
                      for k, v in amps.items()}
            results.append((frame.x_fwd - frame.x_aft, len(lines.curves), lines.evaluate(lines.deformed_points(scaled))))
        (l1, n1, r1), (l2, n2, r2) = results
        self.assertEqual(n1, n2)
        self.assertGreater(r1["dk_max"], 0.0)
        for key in ("pass", "pass_G1", "pass_G2", "pass_G3", "pass_G5", "new_infl"):
            self.assertEqual(r1[key], r2[key], key)
        self.assertAlmostEqual(r1["dk_max"] * l1, r2["dk_max"] * l2, delta=1e-6 * r1["dk_max"] * l1)
        self.assertAlmostEqual(r1["bilge_radius_ratio_min"], r2["bilge_radius_ratio_min"], places=6)
        for family in r1["energy_ratio"]:
            self.assertAlmostEqual(r1["energy_ratio"][family], r2["energy_ratio"][family], places=6)


class TopCurveTests(unittest.TestCase):
    def test_deck_step_is_passed_on_its_high_side(self):
        low = box_triangles(10.0, 2.0, 2.0)
        high = box_triangles(10.0, 2.0, 3.0) + np.array([10.0, 0.0, 0.0])
        rules = FrameRules()
        knots, values, edge, xs = _top_curve(np.concatenate([low, high]), 0.0, 20.0, 1.0, rules)
        from scipy.interpolate import CubicSpline
        top = CubicSpline(knots, values, bc_type="natural")(xs)
        self.assertLessEqual(float(np.max(top - (edge - 0.03))), 1e-12)
        self.assertAlmostEqual(float(top[xs < 9.0].max()), 1.97, delta=1e-4)
        self.assertGreater(float(top[xs > 13.0].min()), 2.9)

    def test_rules_are_checked(self):
        with self.assertRaises(ValueError):
            FrameRules(bow_start=0.2, stern_end=0.3)
        with self.assertRaises(ValueError):
            FrameRules(top_window=0.6)
        with self.assertRaises(ValueError):
            FairnessPlan.from_frame(derive_frame(hull(), 2.2), FairnessRules(energy_ratio_max=0.9))


if __name__ == "__main__":
    unittest.main()
