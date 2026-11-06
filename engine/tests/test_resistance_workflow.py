"""M4 persistence and force labels on actual synthetic hull geometry."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hullopt.evaluation.resistance import run_resistance, per_speed_comparison, _verified_candidate_geometry
from hullopt.evaluation.candidate import run_candidate
from hullopt.store import ResultStore
try:
    from .test_candidate_workflow import make_config
except ImportError:
    from test_candidate_workflow import make_config


def fake_artifact_plot(directory, payload):
    (directory / "resistance.csv").write_text("verified by separate actual integration run", encoding="utf-8")
    (directory / "resistance_comparison.png").write_bytes(b"plot fixture")


class ResistanceWorkflowTests(unittest.TestCase):
    def test_baseline_models_labels_persistence_reuse_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_config(Path(temporary))
            config.resistance.michell.nx = 31
            config.resistance.michell.nz = 21
            with patch("hullopt.evaluation.resistance._write_tables_and_plot", side_effect=fake_artifact_plot):
                directory, payload = run_resistance(config)
            self.assertEqual(payload["record"]["status"], "succeeded")
            self.assertIsNone(payload["candidate"])
            forces = payload["baseline"]["forces"]
            self.assertEqual(forces["PY-HOLTROP-1984"]["quantity"], "total_resistance")
            self.assertEqual(forces["PY-MICHELL"]["quantity"], "wave_force")
            self.assertEqual(forces["PY-MICHELL-PLUS-ITTC57-PARTIAL"]["quantity"], "wave_plus_friction_force")
            self.assertFalse(payload["baseline"]["assumptions"]["maxsurf_validated"])
            self.assertTrue(payload["baseline"]["numerical_status"]["michell_outer_verified"])
            _, reused = run_resistance(config, reuse=True)
            self.assertEqual(reused["identity"], payload["identity"])
            with self.assertRaises(FileExistsError):
                run_resistance(config)
            with ResultStore(config.database) as store:
                row = store.find_candidate(config.study_id, "R0001_M4")
                self.assertEqual(row["status"], "succeeded")
                self.assertIn("M4-RESISTANCE", store.get_results(row["evaluation_id"]))
            manifest_path = directory / "manifest.json"
            original_manifest = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(original_manifest)
            del manifest["artifact_sha256"]["resistance.csv"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected artifact set"):
                run_resistance(config, reuse=True)
            manifest_path.write_text(original_manifest, encoding="utf-8")
            (directory / "resistance.csv").write_text("changed")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                run_resistance(config, reuse=True)

    def test_historical_candidate_is_remeasured_and_native_missing_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_config(Path(temporary))
            config.resistance.michell.nx = 31
            config.resistance.michell.nz = 21
            with patch("hullopt.evaluation.candidate._comparison_plot", side_effect=lambda p, *a: p.write_bytes(b"sac")), \
                 patch("hullopt.evaluation.candidate._section_plot", side_effect=lambda p, *a: p.write_bytes(b"sections")):
                candidate_dir, candidate = run_candidate(config, .01, 0)
            with self.assertRaisesRegex(ValueError, "no verified native"):
                _verified_candidate_geometry(config, candidate_dir, "native")
            with patch("hullopt.evaluation.resistance._write_tables_and_plot", side_effect=fake_artifact_plot):
                _, payload = run_resistance(config, candidate_directory=candidate_dir, representation="control_points")
            self.assertAlmostEqual(payload["candidate"]["hydrostatics"]["volume_m3"],
                                   payload["baseline"]["hydrostatics"]["volume_m3"], delta=1e-5)
            self.assertEqual(len(payload["comparison"]["rows"]), 4 * len(config.speeds_kn))
            self.assertEqual(payload["identity"]["candidate"]["candidate_hash"], candidate["record"]["candidate_hash"])

    def test_failure_and_physical_rejection_are_recorded_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = make_config(Path(temporary))
            with patch("hullopt.evaluation.resistance.evaluate_models", side_effect=RuntimeError("model fault")):
                with self.assertRaisesRegex(RuntimeError, "model fault"):
                    run_resistance(config, evaluation_id="failed")
            config.checks.closure_bottom_z_m = .05
            with self.assertRaisesRegex(ValueError, "constraints failed"):
                run_resistance(config, evaluation_id="rejected")
            with ResultStore(config.database) as store:
                self.assertEqual(store.find_candidate(config.study_id, "failed")["status"], "failed")
                self.assertEqual(store.find_candidate(config.study_id, "rejected")["status"], "rejected")

    def test_per_speed_comparison_uses_relative_percent_and_handles_zero(self):
        first = {"forces": {"METHOD": {"speeds_kn": [10, 15], "values": [100, 0]}}}
        other = {"forces": {"METHOD": {"speeds_kn": [10, 15], "values": [90, 0]}}}
        rows = per_speed_comparison(first, other)["rows"]
        self.assertAlmostEqual(rows[0]["change_percent"], -10)
        self.assertIsNone(rows[1]["change_percent"])


if __name__ == "__main__":
    unittest.main()
