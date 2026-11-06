"""Risk-based checks for configuration, provenance, state and labelled backends."""

from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from pydantic import ValidationError

from hullopt.cli import main, run_baseline
from hullopt.config import StudyConfig, file_sha256, load_study
from hullopt.evaluation.record import EvaluationRecord, candidate_hash
from hullopt.resistance import Conditions, ITTC57Friction, MockBackend
from hullopt.store import ResultStore


def study_document(source_hash: str) -> dict:
    return {
        "study_id": "TEST-001",
        "baseline": {"geometry_dump": "../inputs/model.hgeo.json", "source_3dm": "../inputs/model.3dm",
                     "source_sha256": source_hash, "hull_layers": ["HULL"], "appendage_layers": ["SKEG"]},
        "immersion": {"reference_z_wl_m": 1.0}, "speeds_kn": [10, 15, 20],
        "checks": {"closure_bottom_z_m": 2.0, "side_recess_bottom_z_m": 2.0, "min_clearance_m": 0.1},
    }


def prepare_study(root: Path):
    inputs = root / "inputs"
    studies = root / "studies"
    inputs.mkdir()
    studies.mkdir()
    source = inputs / "model.3dm"
    source.write_bytes(b"immutable source fixture")
    (inputs / "model.hgeo.json").write_text("{}", encoding="utf-8")
    path = studies / "study.yaml"
    path.write_text(yaml.safe_dump(study_document(file_sha256(source))), encoding="utf-8")
    return load_study(path)


class ConfigTests(unittest.TestCase):
    def test_removed_recess_does_not_limit_equilibrium_waterline(self):
        document = study_document("0" * 64)
        document['checks'].update(closure_bottom_z_m=2.89, side_recess_bottom_z_m=None)
        config = StudyConfig.model_validate(document)
        self.assertIsNone(config.checks.side_recess_bottom_z_m)
        self.assertAlmostEqual(config.checks.waterline_ceiling_m, 2.79)
        config.checks.side_recess_bottom_z_m = 2.73
        self.assertAlmostEqual(config.checks.waterline_ceiling_m, 2.63)

    def test_paths_are_relative_to_yaml_not_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = prepare_study(root)
            self.assertEqual(config.baseline.geometry_dump, root / "inputs" / "model.hgeo.json")
            self.assertEqual(config.run_root, root / "runs")
            self.assertEqual(config.selected_layers, ["HULL"])
            config.scope = "hull_with_appendages"
            self.assertEqual(config.selected_layers, ["HULL", "SKEG"])
            self.assertEqual(config.input_hashes()["source_3dm"], config.baseline.source_sha256)
            config.baseline.source_3dm.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                config.input_hashes()

    def test_invalid_physics_and_unknown_fields_are_rejected(self):
        original = study_document("0" * 64)
        for mutation in (
            {"speeds_kn": [10, 10]}, {"speeds_kn": [-1]}, {"speeds_kn": [float("nan")]},
            {"water": {"rho_kg_m3": -1}}, {"immersion": {"reference_z_wl_m": 1, "trim_deg": 1}},
            {"misspelled_scope": "bare_hull"}, {"study_id": "../unsafe"},
            {"tessellation": {"min_grid": 100, "max_grid": 12}},
        ):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(original)
                document.update(mutation)
                with self.assertRaises(ValidationError):
                    StudyConfig.model_validate(document)


class RecordStoreTests(unittest.TestCase):
    def test_identity_is_order_independent_and_finite(self):
        self.assertEqual(candidate_hash({"a": 1, "b": 2}), candidate_hash({"b": 2, "a": 1}))
        self.assertNotEqual(candidate_hash({"a": 1}), candidate_hash({"a": 2}))
        with self.assertRaises(ValueError):
            candidate_hash({"a": math.nan})
        with self.assertRaises(ValueError):
            EvaluationRecord("S", "C", "0" * 64, {})

    def test_state_machine_and_transactional_rollback(self):
        with tempfile.TemporaryDirectory() as temporary, ResultStore(Path(temporary) / "store.sqlite3") as store:
            identity = {"baseline_hash": "abc", "x": [0, 0]}
            record = EvaluationRecord("S", "C", candidate_hash(identity), identity)
            evaluation_id = store.create_evaluation(record)
            with self.assertRaises(ValueError):
                store.transition(evaluation_id, "succeeded")
            store.transition(evaluation_id, "running")
            with self.assertRaises(ValueError):
                store.transition(evaluation_id, "succeeded", results={"FIRST": {"ok": 1}, "BAD": {"x": math.nan}})
            self.assertEqual(store.find_candidate("S", "C")["status"], "running")
            self.assertEqual(store.get_results(evaluation_id), {})
            store.transition(evaluation_id, "succeeded", results={"MOCK": {"backend": "MOCK", "is_physical_prediction": False}})
            self.assertEqual(store.find_candidate("S", "C")["status"], "succeeded")
            self.assertEqual(store.get_results(evaluation_id)["MOCK"]["backend"], "MOCK")
            with self.assertRaises(ValueError):
                store.transition(evaluation_id, "running")
            with self.assertRaises(sqlite3.IntegrityError):
                store.create_evaluation(record)

    def test_physical_rejection_differs_from_failure(self):
        identity = {"x": 0}
        record = EvaluationRecord("S", "C", candidate_hash(identity), identity)
        with self.assertRaises(ValueError):
            record.transition("rejected")
        record.transition("rejected", reason="draft limit")
        self.assertEqual(record.to_dict()["status"], "rejected")
        self.assertEqual(record.reason, "draft limit")


class BackendTests(unittest.TestCase):
    def test_friction_matches_independent_formula_and_transom_option(self):
        hydro = {"lwl_m": 40.0, "wetted_area_m2": 300.0, "transom_wetted_area_m2": 5.0, "volume_m3": 250.0}
        conditions = Conditions((10.0,), 1025.0, 1.2e-6)
        result = ITTC57Friction().evaluate(hydro, conditions)
        velocity = 10 * 1852 / 3600
        coefficient = 0.075 / (math.log10(velocity * 40 / 1.2e-6) - 2)**2
        expected = 0.5 * 1025 * velocity**2 * 300 * coefficient
        self.assertAlmostEqual(result.values[0], expected, places=9)
        self.assertEqual(result.quantity, "friction_force")
        self.assertIn("only", result.label)
        reduced = ITTC57Friction(include_transom=False).evaluate(hydro, conditions)
        self.assertAlmostEqual(reduced.values[0] / result.values[0], 295 / 300)
        mock = MockBackend().evaluate(hydro, conditions)
        self.assertFalse(mock.is_physical_prediction)
        self.assertEqual(mock.quantity, "software_test_score")
        self.assertEqual(mock.units, "arbitrary")
        self.assertIn("NOT A RESISTANCE", mock.label)

    def test_invalid_conditions_and_low_reynolds_rejected(self):
        for conditions in (((), 1025, 1e-6), ((0,), 1025, 1e-6), ((10,), 0, 1e-6), ((10,), 1025, math.nan)):
            with self.assertRaises(ValueError):
                Conditions(*conditions)
        with self.assertRaisesRegex(ValueError, "Reynolds"):
            ITTC57Friction().evaluate({"lwl_m": 0.001, "wetted_area_m2": 1}, Conditions((0.1,)))


class BaselineTests(unittest.TestCase):
    def test_baseline_accepts_absent_recess_and_records_null_clearance(self):
        try:
            from .hydro_fixtures import box_mesh
        except ImportError:
            from hydro_fixtures import box_mesh
        with tempfile.TemporaryDirectory() as temporary:
            config = prepare_study(Path(temporary))
            config.checks.side_recess_bottom_z_m = None
            with patch("hullopt.cli._build_mesh", return_value=(box_mesh(), {})):
                directory, payload = run_baseline(config)
            saved = json.loads((directory / 'measurements.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['record']['status'], 'succeeded')
            self.assertIsNone(saved['clearance_checks']['side_recess_clearance_m'])

    def test_cli_equilibrium_retains_configured_section_count(self):
        try:
            from .hydro_fixtures import box_mesh
        except ImportError:
            from hydro_fixtures import box_mesh
        with tempfile.TemporaryDirectory() as temporary:
            config = prepare_study(Path(temporary))
            config.section_count = 13
            output = io.StringIO()
            with patch("hullopt.cli.load_study", return_value=config), \
                 patch("hullopt.cli._build_mesh", return_value=(box_mesh(), {"tessellation_s": 0.0})), \
                 redirect_stdout(output):
                code = main(["equilibrium", "fixture.yaml", "--volume", "20.6"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(len(payload["result"]["hydrostatics"]["section_x_m"]), 13)
            self.assertEqual(len(payload["result"]["hydrostatics"]["section_area_m2"]), 13)
            self.assertAlmostEqual(payload["result"]["z_wl_m"], 1.03)
            self.assertEqual(payload["provenance"]["study_settings"]["section_count"], 13)

    def test_baseline_artifacts_database_reuse_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = prepare_study(Path(temporary))
            hydro_values = {"volume_m3": 20.0, "lwl_m": 10.0, "wetted_area_m2": 44.0,
                            "transom_wetted_area_m2": 2.0, "section_x_m": [0.0, 5.0, 10.0],
                            "section_area_m2": [2.0, 2.0, 2.0]}
            hydro = SimpleNamespace(**hydro_values, to_dict=lambda: hydro_values)
            mesh = SimpleNamespace(triangles=[None] * 12, area_m2=88.0, metadata={"tessellation": "fixture"})
            with patch("hullopt.cli._build_mesh", return_value=(mesh, {"tessellation_s": 0.0})), \
                 patch("hullopt.cli._measure", return_value=hydro), \
                 patch("hullopt.cli._save_sac", side_effect=lambda path, _: path.write_bytes(b"plot fixture")):
                directory, payload = run_baseline(config)
                self.assertTrue((directory / "measurements.json").is_file())
                self.assertEqual(payload["record"]["status"], "succeeded")
                self.assertEqual(payload["provenance"]["input_sha256"], config.input_hashes())
                self.assertFalse(payload["software_test"]["is_physical_prediction"])
                self.assertTrue(payload["friction_component"]["is_physical_prediction"])
                with self.assertRaises(FileExistsError):
                    run_baseline(config)
                _, reused = run_baseline(config, reuse=True)
                self.assertEqual(reused["record"]["candidate_hash"], payload["record"]["candidate_hash"])
                with ResultStore(config.database) as store:
                    row = store.find_candidate(config.study_id, "C0000_baseline")
                    self.assertEqual(row["status"], "succeeded")
                    self.assertEqual(len(store.get_results(row["evaluation_id"])), 3)
                (directory / "SAC.png").write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "artifact changed"):
                    run_baseline(config, reuse=True)

    def test_failure_is_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = prepare_study(Path(temporary))
            with patch("hullopt.cli._build_mesh", side_effect=ValueError("invalid geometry")):
                with self.assertRaisesRegex(ValueError, "invalid geometry"):
                    run_baseline(config)
            directory = config.run_root / config.study_id / "C0000_baseline"
            failure = json.loads((directory / "failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")
            with ResultStore(config.database) as store:
                self.assertEqual(store.find_candidate(config.study_id, "C0000_baseline")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
