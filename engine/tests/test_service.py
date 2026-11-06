"""JSON-RPC service used by the Rhino plug-in: framing, errors and analytic hydrostatics."""

import io
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hullopt.service import INVALID_PARAMS, METHOD_NOT_FOUND, PARSE_ERROR, Session, handle, serve

LENGTH, BEAM, DEPTH = 20.0, 6.0, 4.0  # metres; written in millimetres to exercise the unit scale
ROOT = Path(__file__).resolve().parents[2]


def _face(origin, a, b, index):
    """Bilinear patch whose u x v normal points outward."""
    o, a, b = (np.asarray(v, float) * 1000.0 for v in (origin, a, b))
    cv = [[list(o) + [1.0], list(o + b) + [1.0]], [list(o + a) + [1.0], list(o + a + b) + [1.0]]]
    return {"layer": "HULL", "rev": 0, "object_index": 0, "face_index": index, "object_name": "box",
            "nurbs_form_exact": 1, "loops": [],
            "nurbs": {"rational": 0, "order": [2, 2], "cv_count": [2, 2], "knots_u": [0.0, 1.0],
                      "knots_v": [0.0, 1.0], "cv": [p for row in cv for p in row]}}


def box_dump(path: Path) -> Path:
    L, B, D = LENGTH, BEAM, DEPTH
    faces = [_face((0, B / 2, 0), (L, 0, 0), (0, -B, 0), 0),     # bottom, -z
             _face((0, -B / 2, D), (L, 0, 0), (0, B, 0), 1),     # top, +z
             _face((0, B / 2, 0), (0, 0, D), (L, 0, 0), 2),      # side, +y
             _face((0, -B / 2, 0), (L, 0, 0), (0, 0, D), 3),     # side, -y
             _face((0, -B / 2, 0), (0, 0, D), (0, B, 0), 4),     # stern, -x
             _face((L, -B / 2, 0), (0, B, 0), (0, 0, D), 5)]     # bow, +x
    path.write_text(json.dumps({"format": "hullgeo-1", "source": "analytic box", "abs_tol": 0.01,
                                "unit_system": "Millimeters", "meters_per_unit": 0.001, "warnings": 0,
                                "face_count": len(faces), "faces": faces, "layers": {"0": "HULL"},
                                "document_strings": {}}), encoding="utf-8")
    return path


def call(session, method, params, request_id=1):
    sent = []
    response, _ = handle(session, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                         sent.append)
    return response, sent


class FramingTests(unittest.TestCase):
    def run_lines(self, *lines):
        output = io.BytesIO()
        serve(io.BytesIO(("\n".join(lines) + "\n").encode("utf-8")), output)
        return [json.loads(line) for line in output.getvalue().decode("ascii").splitlines()]

    def test_ready_info_errors_and_shutdown(self):
        messages = self.run_lines(
            '{"jsonrpc":"2.0","id":1,"method":"engine.info"}',
            '{"jsonrpc":"2.0","id":2,"method":"no.such.method"}',
            'not json',
            '{"jsonrpc":"2.0","id":3,"method":"hydro.measure","params":{"hgeo":"x.json"}}',
            '{"jsonrpc":"2.0","method":"engine.info"}',
            '{"jsonrpc":"2.0","id":4,"method":"engine.shutdown"}',
            '{"jsonrpc":"2.0","id":5,"method":"engine.info"}')
        self.assertEqual(messages[0]["method"], "ready")
        self.assertIn("hydro.equilibrium", messages[1]["result"]["methods"])
        self.assertEqual(messages[2]["error"]["code"], METHOD_NOT_FOUND)
        self.assertEqual(messages[3]["error"]["code"], PARSE_ERROR)
        self.assertEqual(messages[4]["error"]["code"], INVALID_PARAMS)
        self.assertEqual(messages[5], {"jsonrpc": "2.0", "id": 4, "result": {"stopping": True}})
        self.assertEqual(len(messages), 6, "notifications get no reply and nothing runs after shutdown")

    def test_output_is_ascii_even_for_non_ascii_paths(self):
        messages = self.run_lines(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "geometry.summary",
                                              "params": {"hgeo": "C:/Çalışma/yok.hgeo.json"}}))
        self.assertIn("error", messages[1])


class BoxHullTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.dump = str(box_dump(Path(cls.folder.name) / "box.hgeo.json"))
        cls.session = Session()

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_measure_matches_analytic_box(self):
        z = 1.5
        response, progress = call(self.session, "hydro.measure", {"hgeo": self.dump, "z_wl_m": z, "rho_kg_m3": 1000.0})
        h = response["result"]["hydrostatics"]
        self.assertAlmostEqual(h["volume_m3"], LENGTH * BEAM * z, places=9)
        self.assertAlmostEqual(h["displacement_kg"], 1000.0 * LENGTH * BEAM * z, places=6)
        self.assertAlmostEqual(h["lcb_m"], LENGTH / 2, places=9)
        self.assertAlmostEqual(h["kb_m"], z / 2, places=9)
        self.assertAlmostEqual(h["waterplane_area_m2"], LENGTH * BEAM, places=9)
        self.assertAlmostEqual(h["wetted_area_m2"], LENGTH * BEAM + 2 * (LENGTH + BEAM) * z, places=9)
        self.assertTrue(progress and all(m["method"] == "progress" for m in progress))
        lines = response["result"]["waterline_m"]
        points = np.vstack([np.asarray(line) for line in lines])
        np.testing.assert_allclose(points[:, 2], z, atol=1e-12)
        perimeter = sum(np.linalg.norm(np.diff(np.asarray(line), axis=0), axis=1).sum() for line in lines)
        self.assertAlmostEqual(perimeter, 2 * (LENGTH + BEAM), places=9)

    def test_equilibrium_finds_the_analytic_draft(self):
        draft = 2.25
        response, _ = call(self.session, "hydro.equilibrium",
                           {"hgeo": self.dump, "displacement_t": 1.025 * LENGTH * BEAM * draft, "rho_kg_m3": 1025.0})
        self.assertAlmostEqual(response["result"]["z_wl_m"], draft, places=6)

    def test_unreachable_displacement_is_an_error(self):
        response, _ = call(self.session, "hydro.equilibrium",
                           {"hgeo": self.dump, "displacement_t": 10 * LENGTH * BEAM * DEPTH})
        self.assertIn("exceeds the largest immersed volume", response["error"]["message"])

    def test_resistance_reports_power_and_rejects_bad_options(self):
        response, _ = call(self.session, "resistance.evaluate",
                           {"hgeo": self.dump, "z_wl_m": 1.5, "speeds_kn": [4, 6], "methods": ["friction"]})
        result = response["result"]["results"][0]
        self.assertEqual(result["backend"], "PY-ITTC57-FRICTION-ONLY")
        for force, power, speed in zip(result["values"], result["power_W"], result["speeds_kn"]):
            self.assertAlmostEqual(power, force * speed * 1852 / 3600, places=6)
        response, _ = call(self.session, "resistance.evaluate",
                           {"hgeo": self.dump, "z_wl_m": 1.5, "speeds_kn": [6, 4]})
        self.assertEqual(response["error"]["code"], INVALID_PARAMS)
        response, _ = call(self.session, "resistance.evaluate",
                           {"hgeo": self.dump, "z_wl_m": 1.5, "speeds_kn": [4],
                            "holtrop": {"entrance_angle_policy": "given"}})
        self.assertEqual(response["error"]["code"], INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
