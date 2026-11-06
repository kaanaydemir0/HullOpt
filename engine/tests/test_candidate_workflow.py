"""Actual geometry/solver/record integration without requiring a CAD licence."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from hullopt.config import load_study
from hullopt.geometry.export import write_hgeo
from hullopt.evaluation.candidate import run_candidate
from hullopt.store import ResultStore
from hullopt.optimize.lackenby import TargetInfeasibleError
try:
    from .test_lackenby_solver import wigley_geometry
except ImportError:
    from test_lackenby_solver import wigley_geometry


def make_config(root):
    source = root / "source.3dm"
    source.write_bytes(b"source identity fixture; CAD export is not requested")
    write_hgeo(wigley_geometry(), root / "geometry.hgeo.json")
    document = {"study_id": "TEST-M3", "baseline": {
        "geometry_dump": "geometry.hgeo.json", "source_3dm": "source.3dm",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "immersion": {"reference_z_wl_m": 0.0}, "speeds_kn": [10, 15],
        "tessellation": {"min_grid": 21, "max_grid": 21}, "section_count": 41,
        "run_root": "runs", "database": "runs/results.sqlite3",
        "variables": [{"name": "dCp", "min": -.03, "max": .03}]}
    path = root / "study.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return load_study(path)


class CandidateWorkflowTests(unittest.TestCase):
    def test_actual_candidate_persistence_reuse_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_config(Path(temporary))
            with patch("hullopt.evaluation.candidate._comparison_plot", side_effect=lambda p, *a: p.write_bytes(b"sac")), \
                 patch("hullopt.evaluation.candidate._section_plot", side_effect=lambda p, *a: p.write_bytes(b"sections")):
                directory, payload = run_candidate(config, .01, .0)
            self.assertEqual(payload["record"]["status"], "succeeded")
            self.assertTrue(payload["checks"]["passed"])
            self.assertLess(abs(payload["transformation"]["residuals"]["volume_relative"]), 1e-6)
            self.assertTrue((directory / "candidate.hgeo.json").is_file())
            self.assertTrue((directory / "candidate_m.stl").is_file())
            self.assertIsNone(payload["native_cad"])
            _, reused = run_candidate(config, .01, .0, reuse=True)
            self.assertEqual(reused["record"]["candidate_hash"], payload["record"]["candidate_hash"])
            with self.assertRaises(FileExistsError):
                run_candidate(config, .01, .0)
            with self.assertRaisesRegex(ValueError, "identity changed"):
                run_candidate(config, .02, .0, reuse=True)
            (directory / "candidate_m.stl").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                run_candidate(config, .01, .0, reuse=True)

    def test_infeasible_target_is_rejected_and_failure_is_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_config(Path(temporary))
            config.immersion.draft_change_limit_m = .00001
            with self.assertRaises(TargetInfeasibleError):
                run_candidate(config, .03, .0, candidate_id="rejected")
            with ResultStore(config.database) as store:
                self.assertEqual(store.find_candidate(config.study_id, "rejected")["status"], "rejected")
            with patch("hullopt.optimize.lackenby.prepare_lackenby", side_effect=RuntimeError("solver fault")):
                with self.assertRaisesRegex(RuntimeError, "solver fault"):
                    run_candidate(config, .01, .0, candidate_id="failed")
            with ResultStore(config.database) as store:
                self.assertEqual(store.find_candidate(config.study_id, "failed")["status"], "failed")

    def test_configured_variable_bounds_and_bare_hull_scope_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_config(Path(temporary))
            with self.assertRaises(TargetInfeasibleError):
                run_candidate(config, .04, .0)
            config.scope = "hull_with_appendages"
            with self.assertRaisesRegex(ValueError, "bare_hull"):
                run_candidate(config, .01, .0)


if __name__ == "__main__":
    unittest.main()
