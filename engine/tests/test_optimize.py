"""optimize.run on the analytic hull: objective weightings, gates, stop, log and bad requests."""
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hullopt.optimize.search import DEFAULT_BOUNDS, Objective, run_search, variables_from
from hullopt.service import INVALID_PARAMS, Session
from tests.test_morph import LIMITS, Z, write_hull
from tests.test_service import call

VARIABLES = {"bow_fine": {"min": -0.05, "max": 0.1}, "stern_narrow": {}}
SMALL = {"population": 30, "max_evaluations": 45}
PROPULSION = {"diameter_m": 1.4, "shaft_z_m": 0.7, "design_speed_kn": 11.0, "blades": 4, "engine_mcr_kw": 800.0}


class ObjectiveTests(unittest.TestCase):
    def test_the_three_weightings(self):
        q0, q = [100.0, 300.0], [99.0, 291.0]
        equal = Objective("holtrop", "equal", [8.0, 10.0])
        self.assertAlmostEqual(equal.value(q, q0)[0], (0.99 + 0.97) / 2, places=15)
        profile = Objective("brake_power", "profile", profile=[(10.0, 1.0), (8.0, 3.0)])
        self.assertEqual(profile.speeds_kn, (8.0, 10.0))
        self.assertEqual(profile.weights, (0.75, 0.25))
        self.assertAlmostEqual(profile.value(q, q0)[0], (0.75 * 99 + 0.25 * 291) / (0.75 * 100 + 0.25 * 300),
                               places=15)
        service = Objective("michell", "service", service_speed_kn=10.0)
        self.assertEqual(service.speeds_kn, (10.0,))
        self.assertAlmostEqual(service.value([291.0], [300.0])[0], 0.97, places=15)
        # Equal shares in the profile are the ratio of totals, not the mean ratio: each mode keeps its meaning.
        flat = Objective("holtrop", "profile", profile=[(8.0, 1.0), (10.0, 1.0)])
        self.assertAlmostEqual(flat.value(q, q0)[0], 390.0 / 400.0, places=15)
        self.assertIn("mean", equal.describe()["formula"])

    def test_default_bounds_scale_with_the_hull(self):
        variables = variables_from({"stern_lift": {}, "bow_fine": {"max": 0.2}, "lack_aft": None}, 40.0, 2.0)
        self.assertEqual([v.name for v in variables], ["lack_aft", "bow_fine", "stern_lift"])
        lack, bow, lift = variables
        self.assertAlmostEqual(lack.high, DEFAULT_BOUNDS["lack_aft"][1] * 40.0)
        self.assertEqual((bow.low, bow.high, bow.automatic), (DEFAULT_BOUNDS["bow_fine"][0], 0.2, ("min",)))
        self.assertAlmostEqual(lift.high, DEFAULT_BOUNDS["stern_lift"][1] * 2.0)
        for bad in ({}, {"no_mode": {}}, {"bow_fine": {"min": 0.1, "max": 0.1}}, {"bow_fine": {"lo": 0}},
                    {"bow_fine": {"max": "x"}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                variables_from(bad, 40.0, 2.0)


class OptimizeServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = str(write_hull(Path(cls.directory.name)))
        cls.session = Session()
        cls.base = {"hgeo": cls.path, "rho_kg_m3": 1025.9, "z_wl_m": Z, "tessellation": {"min_grid": 40, "max_grid": 60},
                    "limits": LIMITS, "speeds_kn": [8.0, 10.0], "methods": ["holtrop"], "variables": VARIABLES,
                    "search": SMALL}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_optimize(self, **extra):
        response, sent = call(self.session, "optimize.run", {**self.base, **extra})
        self.assertNotIn("error", response, response.get("error"))
        return response["result"], sent

    def check_candidates(self, r, objective):
        q0 = r["baseline"]["values"]
        for c in r["candidates"]:
            self.assertTrue(c["feasible"])
            self.assertTrue(all(g["pass"] for g in c["gates"]))
            self.assertAlmostEqual(c["J"], objective.value(c["values"], q0)[0], places=14)
        self.assertEqual([c["J"] for c in r["candidates"]], sorted(c["J"] for c in r["candidates"]))

    def test_equal_weighting_search(self):
        log = Path(self.directory.name) / "equal.jsonl"
        r, sent = self.run_optimize(objective={"quantity": "holtrop", "weighting": "equal"}, log_path=str(log))
        self.assertEqual(r["objective"]["speeds_kn"], [8.0, 10.0])
        self.assertEqual(r["search"]["evaluations"], 45)
        self.assertFalse(r["search"]["stopped"])
        self.assertEqual([h["generation"] for h in r["history"]], [0, 1])
        self.assertEqual(r["counts"]["evaluated"], 45)
        self.assertEqual(r["counts"]["feasible"] + r["counts"]["failed_gates"] + r["counts"]["errors"], 45)
        self.check_candidates(r, Objective("holtrop", "equal", [8.0, 10.0]))
        best = r["best"]
        self.assertLessEqual(best["J"], 1.0 + 1e-12)       # the baseline is in the first generation
        self.assertEqual(best["J"], r["candidates"][0]["J"])
        self.assertAlmostEqual(best["J"], float(np.mean(best["ratios"])), places=14)
        self.assertEqual(len(r["best_by_evaluation"]), 45)
        trace = [v for v in r["best_by_evaluation"] if v is not None]
        self.assertTrue(all(b <= a for a, b in zip(trace, trace[1:])))
        records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 45)
        self.assertEqual(records[0]["amplitudes"], {"bow_fine": 0.0, "stern_narrow": 0.0})
        self.assertAlmostEqual(records[0]["J"], 1.0, places=9)
        self.assertTrue(all(rec["J"] is None for rec in records if not rec["feasible"]))
        data = [m["params"]["data"] for m in sent if m.get("method") == "progress" and "data" in m["params"]]
        self.assertEqual(data[-1]["evaluations"], 45)
        self.assertEqual(data[-1]["best_J"], best["J"])
        # At most one candidate message a second, plus one per generation and the final ranking.
        self.assertLess(len(data), 45)
        self.assertEqual(sum(1 for m in sent if m["params"]["stage"].startswith("Generation")), 2)

    def test_profile_and_service_weightings(self):
        profile = [[10.0, 1.0], [8.0, 3.0]]
        r, _ = self.run_optimize(objective={"quantity": "holtrop", "weighting": "profile", "profile": profile},
                                 search={"population": 30, "max_evaluations": 30})
        self.assertEqual(r["objective"]["speeds_kn"], [8.0, 10.0])
        self.assertEqual(r["objective"]["weights"], [0.75, 0.25])
        self.check_candidates(r, Objective("holtrop", "profile", profile=profile))
        s, _ = self.run_optimize(objective={"quantity": "holtrop", "weighting": "service", "service_speed_kn": 9.0},
                                 search={"population": 30, "max_evaluations": 30})
        self.assertEqual(s["objective"]["speeds_kn"], [9.0])
        self.assertEqual(s["conditions"]["evaluated_speeds_kn"], [9.0])
        self.check_candidates(s, Objective("holtrop", "service", service_speed_kn=9.0))
        self.assertAlmostEqual(s["best"]["J"], s["best"]["ratios"][0], places=15)

    def test_brake_power_objective_keeps_the_baseline_propeller(self):
        r, _ = self.run_optimize(objective={"quantity": "brake_power", "weighting": "service", "service_speed_kn": 10.0},
                                 propulsion=PROPULSION, search={"population": 30, "max_evaluations": 30})
        self.assertEqual(r["conditions"]["evaluated_speeds_kn"], [10.0, 11.0])
        self.assertEqual(r["conditions"]["added_speeds_kn"], [11.0])
        design = r["baseline"]["propulsion_design"]
        self.assertEqual((design["pitch_ratio_source"], design["area_ratio_source"]), ("best_eta0", "keller"))
        for c in r["candidates"]:
            held = c["propulsion_design"]
            self.assertEqual((held["pitch_ratio"], held["area_ratio"]), (design["pitch_ratio"], design["area_ratio"]))
            self.assertEqual((held["pitch_ratio_source"], held["area_ratio_source"]), ("given", "given"))
        redesign, _ = self.run_optimize(objective={"quantity": "brake_power", "weighting": "service",
                                                   "service_speed_kn": 10.0, "propeller": "redesign"},
                                        propulsion=PROPULSION, search={"population": 30, "max_evaluations": 30})
        self.assertTrue(all(c["propulsion_design"]["pitch_ratio_source"] == "best_eta0"
                            for c in redesign["candidates"]))
        self.assertEqual(r["objective"]["propeller"], "baseline")
        self.assertEqual(r["objective"]["units"], "W")

    def test_stop_returns_what_was_found(self):
        state = {"calls": 0}

        def stop_after_ten():
            state["calls"] += 1
            return state["calls"] > 10

        model = self.session.morph(self.base, lambda *args: None)
        objective = Objective("holtrop", "equal", [8.0])

        def measure(mesh, z, hydro, baseline):
            return {"values": [hydro.wetted_area_m2], "notes": [[]]}

        r = run_search(model, variables_from(VARIABLES, model.hydro0.lwl_m, model.hydro0.draft_m), objective,
                       measure=measure, limits=LIMITS, population=30, max_evaluations=60, stop_requested=stop_after_ten)
        self.assertTrue(r["search"]["stopped"])
        self.assertEqual(r["search"]["evaluations"], 10)
        self.assertTrue(any("Stopped" in w for w in r["warnings"]))
        self.assertIsNotNone(r["best"])

    def test_a_candidate_that_cannot_be_floated_is_recorded_not_fatal(self):
        model = self.session.morph(self.base, lambda *args: None)

        class Odd:
            def __getattr__(self, name):
                return getattr(model, name)

            def evaluate(self, amplitudes, **kwargs):
                if amplitudes["bow_fine"] > 0.05:
                    raise ValueError("Candidate equilibrium did not converge")
                return model.evaluate(amplitudes, **kwargs)

        def measure(mesh, z, hydro, baseline):
            return {"values": [hydro.wetted_area_m2], "notes": [[]]}

        r = run_search(Odd(), variables_from(VARIABLES, model.hydro0.lwl_m, model.hydro0.draft_m),
                       Objective("holtrop", "equal", [8.0]), measure=measure, limits=LIMITS, population=30,
                       max_evaluations=30)
        self.assertGreater(r["counts"]["errors"], 0)
        self.assertEqual(r["search"]["evaluations"], 30)
        self.assertTrue(all(c["amplitudes"]["bow_fine"] <= 0.05 for c in r["candidates"]))

    def test_a_stop_file_ends_the_search_with_what_it_found(self):
        from hullopt.service import handle
        stop = Path(self.directory.name) / "search.stop"
        sent, asked = [], []

        def send(message):
            sent.append(message)
            data = message.get("params", {}).get("data") or {}
            if data.get("evaluations", 0) >= 5 and not asked:
                stop.write_text("stop", encoding="utf-8")
                asked.append(data["evaluations"])

        request = {"jsonrpc": "2.0", "id": 5, "method": "optimize.run",
                   "params": {**self.base, "objective": {"weighting": "equal"}, "stop_path": str(stop)}}
        try:
            response, _ = handle(self.session, request, send)
        finally:
            stop.unlink(missing_ok=True)
        r = response["result"]
        self.assertTrue(r["search"]["stopped"])
        self.assertEqual(r["search"]["evaluations"], asked[0])   # the candidate under way finishes, no more
        self.assertLess(asked[0], SMALL["max_evaluations"])
        self.assertIsNotNone(r["best"])
        stop.write_text("stop", encoding="utf-8")
        try:
            response, _ = call(self.session, "optimize.run", {**self.base, "stop_path": str(stop)})
        finally:
            stop.unlink()
        self.assertEqual(response["error"]["code"], INVALID_PARAMS)

    def test_bad_requests(self):
        for extra in ({"variables": {}}, {"variables": {"no_mode": {}}},
                      {"variables": {"bow_fine": {"min": 0.1, "max": 0.0}}},
                      {"objective": {"weighting": "profile"}},
                      {"objective": {"weighting": "service"}},
                      {"objective": {"weighting": "equal", "service_speed_kn": 10.0}},
                      {"objective": {"weighting": "profile", "profile": [[8.0, 0.0]]}},
                      {"objective": {"quantity": "brake_power"}},
                      {"objective": {"quantity": "pe"}},
                      {"search": {"population": 20}},
                      {"search": {"population": 40, "max_evaluations": 39}},
                      {"log_path": str(Path(self.directory.name) / "no" / "log.jsonl")}):
            response, _ = call(self.session, "optimize.run", {**self.base, **extra})
            self.assertEqual(response["error"]["code"], INVALID_PARAMS, extra)

    def test_violations_rank_failures(self):
        tight = {**LIMITS, "normal_deviation_m": 0.002}
        r, _ = self.run_optimize(limits=tight, objective={"weighting": "equal"},
                                 search={"population": 30, "max_evaluations": 30})
        self.assertGreater(r["counts"]["gate_failures"].get("normal_deviation_m", 0), 0)
        for c in r["candidates"]:
            dev = next(g for g in c["gates"] if g["name"] == "normal_deviation_m")
            self.assertLessEqual(dev["value"], 0.002 + 1e-12)
        self.assertTrue(math.isfinite(r["best"]["J"]))


if __name__ == "__main__":
    unittest.main()
