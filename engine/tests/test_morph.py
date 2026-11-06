"""Shape-mode candidates through the service: frame, evaluation, gates, export (analytic hull)."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hullopt.geometry import evaluate_surface, load_hgeo
from hullopt.morph import MorphModel, rational_basis
from hullopt.optimize.shape_modes_v2 import MODE_NAMES_V2, apply_modes_v2
from hullopt.service import INVALID_PARAMS, Session
from tests.hydro_fixtures import sheer_hull_hgeo
from tests.test_service import call

Z = 2.2          # waterline of the 40 m analytic hull, metres
LIMITS = {"draft_change_m": 0.05, "lcb_shift_percent_lwl": 0.25, "lwl_change_percent": 0.2,
          "bwl_change_percent": 0.2, "normal_deviation_m": 0.1, "upper_edge_m": 0.005}


def write_hull(directory: Path) -> Path:
    """The analytic hull written in millimetres, to exercise the unit scale."""
    data = sheer_hull_hgeo(spans=(10, 4))
    for face in data["faces"]:
        for point in face["nurbs"]["cv"]:
            point[:3] = [1000.0 * c for c in point[:3]]
    data.update(unit_system="Millimeters", meters_per_unit=0.001, abs_tol=1e-6)
    path = directory / "hull.hgeo.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class MorphServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = write_hull(Path(cls.directory.name))
        cls.session = Session()
        cls.base = {"hgeo": str(cls.path), "rho_kg_m3": 1025.9, "z_wl_m": Z,
                    "tessellation": {"min_grid": 40, "max_grid": 60}}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def result(self, method, params):
        response, _ = call(self.session, method, params)
        self.assertNotIn("error", response, response.get("error"))
        return response["result"]

    def test_frame_and_modes(self):
        r = self.result("morph.frame", self.base)
        self.assertEqual([m["name"] for m in r["modes"]], list(MODE_NAMES_V2))
        self.assertEqual(r["notes"], [])
        self.assertAlmostEqual(r["z_wl_m"], Z)
        self.assertEqual(len(r["plan"]["stations"]), 41)
        self.assertGreater(r["frame"]["x_bow0"], r["frame"]["x_stern0"])

    def test_zero_amplitudes_change_nothing(self):
        r = self.result("morph.evaluate", {**self.base, "amplitudes": {}, "limits": LIMITS,
                                           "speeds_kn": [8.0, 10.0], "methods": ["holtrop", "friction"]})
        self.assertTrue(r["feasible"])
        self.assertTrue(all(abs(v) < 1e-9 for v in r["changes"].values() if v is not None))
        self.assertEqual(r["deviation"]["normal_deviation_m"], 0.0)
        self.assertTrue(r["fairness"]["pass"])
        for row in r["resistance"]:
            np.testing.assert_allclose(row["ratio"], 1.0, rtol=1e-9)

    def test_finer_bow_keeps_displacement_and_sinks(self):
        r = self.result("morph.evaluate", {**self.base, "amplitudes": {"bow_fine": 0.05}, "limits": LIMITS,
                                           "speeds_kn": [8.0, 10.0], "methods": ["holtrop"]})
        base, cand = r["baseline"]["hydrostatics"], r["hydrostatics"]
        self.assertAlmostEqual(cand["volume_m3"] / base["volume_m3"], 1.0, places=6)
        self.assertGreater(r["changes"]["z_wl_m"], 0.0)
        self.assertLess(r["changes"]["entrance_half_angle_deg"], 0.0)
        self.assertGreater(r["deviation"]["normal_deviation_m"], 0.0)
        # A CV morph moves the band between the top curve and the edge a little (shared control points).
        self.assertLess(r["deviation"]["upper_edge_m"], 0.05 * r["deviation"]["normal_deviation_m"])
        self.assertEqual({g["name"] for g in r["gates"]} >= set(LIMITS), True)
        self.assertEqual(r["feasible"], all(g["pass"] for g in r["gates"]))
        self.assertEqual(len(r["resistance"][0]["ratio"]), 2)

    def test_export_keeps_weights_and_units(self):
        zero = self.result("morph.export", {**self.base, "amplitudes": {}})
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(zero["meters_per_unit"], 0.001)
        for face, record in zip(zero["faces"], raw["faces"]):
            np.testing.assert_allclose(face["cv"], record["nurbs"]["cv"], rtol=1e-12, atol=1e-9)
        moved = self.result("morph.export", {**self.base, "amplitudes": {"bilge_full": 0.02}})
        for face, record in zip(moved["faces"], raw["faces"]):
            cv = np.asarray(face["cv"])
            np.testing.assert_array_equal(cv[:, 3], np.asarray(record["nurbs"]["cv"])[:, 3])
            self.assertEqual(face["cv_count"], record["nurbs"]["cv_count"])
        self.assertGreater(max(np.abs(np.asarray(m["cv"]) - np.asarray(r["nurbs"]["cv"])).max()
                               for m, r in zip(moved["faces"], raw["faces"])), 0.1)   # millimetres

    def test_displacement_condition(self):
        r = self.result("morph.frame", {**{k: v for k, v in self.base.items() if k != "z_wl_m"},
                                        "displacement_t": 300.0})
        self.assertAlmostEqual(r["hydrostatics"]["displacement_kg"] / 1000.0, 300.0, places=3)

    def test_bad_parameters(self):
        for extra in ({"amplitudes": {"no_such_mode": 1.0}}, {"limits": {"no_such_limit": 1.0}},
                      {"limits": {"draft_change_m": -1.0}}, {"amplitudes": {"bow_fine": "x"}},
                      {"rules": {"bow_start": 2.0}}):
            response, _ = call(self.session, "morph.evaluate", {**self.base, **extra})
            self.assertEqual(response["error"]["code"], INVALID_PARAMS, extra)


class SparseBasisTests(unittest.TestCase):
    def test_unit_modes_equal_the_morphed_surfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            geometry = load_hgeo(write_hull(Path(directory)))
        model = MorphModel(geometry, z_wl_m=Z, rho_kg_m3=1025.0, grid={"min_grid": 20, "max_grid": 30})
        for name in MODE_NAMES_V2:
            moved = apply_modes_v2(model.geometry, model.frame, {name: 1.0})
            for index, face in enumerate(moved.faces):
                mask = model.mesh.face_ids == index
                uv = model.mesh.uv_triangles[mask]
                exact = evaluate_surface(face.surface, uv[:, :, 0], uv[:, :, 1]) - model.mesh.triangles[mask]
                np.testing.assert_allclose(model.units[name][mask], exact, atol=1e-12, err_msg=name)
        surface = model.geometry.faces[0].surface
        u, v = np.array([0.0, 0.3, 1.0]), np.array([1.0, 0.6, 0.0])
        iu, iv, r = rational_basis(surface, u, v)
        np.testing.assert_allclose(r.sum(axis=(1, 2)), 1.0)
        cv = surface.cv[..., :3] / surface.cv[..., 3:4]
        points = np.einsum("nab,nabk->nk", r, cv[iu[:, None, None] + np.arange(4)[None, :, None],
                                                 iv[:, None, None] + np.arange(4)[None, None, :]])
        np.testing.assert_allclose(points, evaluate_surface(surface, u, v), atol=1e-12)


if __name__ == "__main__":
    unittest.main()
