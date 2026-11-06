"""Any speed the user asks for is computed; speeds outside a method's range are named, not dropped."""
import math
import tempfile
import unittest
from pathlib import Path

from hullopt.config import MichellConfig
from hullopt.service import INVALID_PARAMS, Session
from tests.test_morph import LIMITS, Z, write_hull
from tests.test_service import call

KNOT = 1852.0 / 3600.0
LWL = 40.0      # the analytic hull's waterline length, metres
FROUDE = (0.05, 0.2, 0.4, 0.9, 1.3)
SPEEDS = [round(fn * math.sqrt(9.80665 * LWL) / KNOT, 3) for fn in FROUDE]
GRID = {"min_grid": 40, "max_grid": 60}


class SpeedRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = str(write_hull(Path(cls.directory.name)))
        cls.session = Session()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def resistance(self, **extra):
        response, sent = call(self.session, "resistance.evaluate",
                              {"hgeo": self.path, "z_wl_m": Z, "speeds_kn": SPEEDS, "tessellation": GRID,
                               "methods": ["holtrop", "michell", "friction"], **extra})
        self.assertNotIn("error", response, response.get("error"))
        return {r["backend"]: r for r in response["result"]["results"]}, sent

    def test_every_speed_is_computed_and_flagged_where_a_method_extrapolates(self):
        results, sent = self.resistance()
        for backend, result in results.items():
            self.assertEqual(list(result["speeds_kn"]), SPEEDS)
            self.assertEqual(len(result["values"]), len(SPEEDS), backend)
            self.assertEqual(len(result["speed_notes"]), len(SPEEDS), backend)
        holtrop = results["PY-HOLTROP-1984"]["speed_notes"]
        self.assertEqual([bool(n) for n in holtrop], [fn > 0.8 for fn in FROUDE])
        self.assertIn("Holtrop extrapolated", holtrop[-1][0])
        self.assertFalse(any(results["PY-ITTC57-FRICTION-ONLY"]["speed_notes"]))
        stages = [m["params"]["stage"] for m in sent]
        for index, speed in enumerate(SPEEDS):
            self.assertIn(f"Michell wave resistance at {speed:g} kn ({index + 1} of {len(SPEEDS)})", stages)
        fractions = [m["params"]["fraction"] for m in sent]
        self.assertEqual(fractions, sorted(fractions))

    def test_unconverged_michell_speed_is_kept_and_named(self):
        results, _ = self.resistance(michell={"lambda_max": 1.01, "lambda_max_limit": None})
        wave, partial = results["PY-MICHELL"], results["PY-MICHELL-PLUS-ITTC57-PARTIAL"]
        flags = [item["converged"] for item in wave["metadata"]["numerical_convergence"]]
        self.assertIn(False, flags)
        for result in (wave, partial):
            self.assertEqual([any("not converged" in n for n in notes) for notes in result["speed_notes"]],
                             [flag is False for flag in flags])
        self.assertTrue(any("not converged at" in w for w in partial["warnings"]))

    def test_adaptive_refinement_repairs_a_short_cutoff(self):
        results, _ = self.resistance(michell={"lambda_max": 1.01})
        items = results["PY-MICHELL"]["metadata"]["numerical_convergence"]
        self.assertTrue(all(item["converged"] for item in items))
        self.assertTrue(any(item.get("adaptive_refinements") for item in items))
        self.assertFalse(any(results["PY-MICHELL"]["speed_notes"]))

    def test_morph_comparison_covers_every_speed(self):
        response, _ = call(self.session, "morph.evaluate",
                           {"hgeo": self.path, "z_wl_m": Z, "tessellation": GRID, "rho_kg_m3": 1025.9,
                            "amplitudes": {"bow_fine": 0.03}, "limits": LIMITS, "speeds_kn": SPEEDS,
                            "methods": ["holtrop", "friction"]})
        self.assertNotIn("error", response, response.get("error"))
        for row in response["result"]["resistance"]:
            self.assertEqual(list(row["speeds_kn"]), SPEEDS)
            self.assertEqual(len(row["ratio"]), len(SPEEDS))
            self.assertEqual(len(row["speed_notes"]), len(SPEEDS))
        holtrop = next(r for r in response["result"]["resistance"] if r["backend"] == "PY-HOLTROP-1984")
        self.assertTrue(holtrop["speed_notes"][-1][0].startswith("baseline: Fn"))
        self.assertTrue(any(n.startswith("candidate: Fn") for n in holtrop["speed_notes"][-1]))

    def test_speed_lists_must_be_positive_and_increasing(self):
        for speeds in ([], [0.0], [5.0, 5.0], [7.0, 6.0], [-1.0]):
            response, _ = call(self.session, "resistance.evaluate",
                               {"hgeo": self.path, "z_wl_m": Z, "speeds_kn": speeds, "methods": ["friction"]})
            self.assertEqual(response["error"]["code"], INVALID_PARAMS, speeds)

    def test_adaptive_limit_must_not_be_below_the_cutoff(self):
        with self.assertRaises(ValueError):
            MichellConfig(lambda_max=64.0, lambda_max_limit=32.0)
        self.assertIsNone(MichellConfig(lambda_max_limit=None).lambda_max_limit)


if __name__ == "__main__":
    unittest.main()
