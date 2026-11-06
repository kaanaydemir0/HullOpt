"""Meaningful candidate rejection cases; sampled checks are not CAD proofs."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import json
import unittest

import numpy as np

from hullopt.geometry import TriangleMesh
from hullopt.geometry.checks import check_candidate, check_mesh_geometry, sampled_section_parity
from hullopt.hydro.hydrostatics import measure

try:
    from .hydro_fixtures import box_mesh
except ImportError:
    from hydro_fixtures import box_mesh


class CandidateConstraintTests(unittest.TestCase):
    def setUp(self):
        self.mesh = box_mesh()
        self.baseline = measure(self.mesh, 1.0)

    def report(self, candidate=None, **kwargs):
        return check_candidate(self.baseline, self.baseline if candidate is None else candidate,
                               target_volume_m3=20.0, reference_z_wl_m=1.0, **kwargs)

    @staticmethod
    def checks(report):
        return {check.name: check for check in report.checks}

    def test_baseline_identity_passes_with_mesh_and_targets(self):
        report = self.report(target_cp=1.0, target_lcb_m=5.0,
                             xmap_result=SimpleNamespace(minimum_derivative=1.0),
                             baseline_mesh=self.mesh, candidate_mesh=self.mesh,
                             same_connectivity=True, closure_bottom_z_m=2.0,
                             side_recess_bottom_z_m=1.1, min_clearance_m=0.1)
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(self.checks(report)["sampled_volume_crosscheck"].passed)
        payload = report.to_dict()
        json.dumps(payload, allow_nan=False)
        self.assertTrue(payload["limitations"])
        self.assertTrue(any("self intersections" in text for text in payload["limitations"]))

    def test_volume_and_dimension_tolerances_are_independent(self):
        candidate = replace(self.baseline, volume_m3=20.04, lwl_m=10.03, bwl_m=2.006)
        report = self.report(candidate)
        checks = self.checks(report)
        self.assertFalse(report.passed)
        for name in ("fixed_displacement", "waterline_length", "waterline_breadth"):
            self.assertFalse(checks[name].passed)
        self.assertTrue(checks["draft_change"].passed)

    def test_waterline_and_actual_draft_are_both_constrained(self):
        changed_wl = self.report(replace(self.baseline, z_wl_m=1.051))
        changed_keel = self.report(replace(self.baseline, draft_m=1.051))
        self.assertFalse(self.checks(changed_wl)["waterline_change"].passed)
        self.assertFalse(self.checks(changed_keel)["draft_change"].passed)
        boundary = self.report(replace(self.baseline, z_wl_m=1.05, draft_m=1.05))
        self.assertTrue(boundary.passed)

    def test_dry_feature_clearance_uses_candidate_waterline(self):
        report = self.report(replace(self.baseline, z_wl_m=1.02),
                             closure_bottom_z_m=1.2, side_recess_bottom_z_m=1.11,
                             min_clearance_m=0.1)
        checks = self.checks(report)
        self.assertTrue(checks["closure_clearance"].passed)
        self.assertFalse(checks["side_recess_clearance"].passed)
        self.assertAlmostEqual(checks["side_recess_clearance"].value, 0.09)

    def test_target_lcb_is_absolute_model_x_with_forward_positive(self):
        candidate = replace(self.baseline, lcb_m=5.1, cp=0.99)
        good = self.report(candidate, target_lcb_m=5.1, target_cp=0.99)
        bad = self.report(candidate, target_lcb_m=4.9, target_cp=1.01)
        self.assertTrue(good.passed)
        self.assertFalse(self.checks(bad)["target_lcb"].passed)
        self.assertFalse(self.checks(bad)["target_cp"].passed)

    def test_invalid_or_folded_design_map_is_rejected(self):
        for derivative in (0.0, -0.1, float("nan")):
            with self.subTest(derivative=derivative):
                report = self.report(xmap_result={"minimum_derivative": derivative})
                self.assertFalse(self.checks(report)["design_xmap_monotonicity"].passed)
                json.dumps(report.to_dict(), allow_nan=False)

    def test_nonfinite_candidate_measurements_are_structured_rejections(self):
        report = self.report(replace(self.baseline, volume_m3=float("nan"), cp=float("nan")), target_cp=1.0)
        self.assertFalse(report.passed)
        self.assertIsNone(self.checks(report)["fixed_displacement"].value)
        json.dumps(report.to_dict(), allow_nan=False)

    def test_input_tolerances_are_validated(self):
        for option in ({"draft_change_limit_m": 0}, {"min_clearance_m": -0.1},
                       {"cp_tolerance_abs": float("nan")}, {"length_tolerance_rel": -1}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.report(**option)


class MeshDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.mesh = box_mesh()

    def test_open_deck_above_waterline_is_allowed(self):
        triangles = self.mesh.triangles
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        open_deck = TriangleMesh(triangles[normals[:, 2] <= 0])
        report = check_mesh_geometry(open_deck, 1.0, measured_volume_m3=20.0)
        self.assertTrue(report.passed, report.to_dict())
        scan = sampled_section_parity(open_deck, 1.0)
        self.assertEqual(scan["odd_scanlines"], 0)
        self.assertAlmostEqual(scan["sampled_volume_m3"], 20.0, delta=1e-10)

    def test_missing_immersed_side_fails_parity_and_flux(self):
        triangles = self.mesh.triangles
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        broken = TriangleMesh(triangles[normals[:, 1] <= 0])
        report = check_mesh_geometry(broken, 1.0)
        checks = {check.name: check for check in report.checks}
        self.assertFalse(report.passed)
        self.assertFalse(checks["sampled_section_parity"].passed)
        self.assertFalse(checks["immersed_horizontal_normal_flux"].passed)

    def test_missing_transom_is_caught_by_flux_even_with_even_sections(self):
        triangles = self.mesh.triangles
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        broken = TriangleMesh(triangles[normals[:, 0] >= 0])
        report = check_mesh_geometry(broken, 1.0)
        checks = {check.name: check for check in report.checks}
        self.assertTrue(checks["sampled_section_parity"].passed)
        self.assertFalse(checks["immersed_horizontal_normal_flux"].passed)

    def test_corresponding_inversion_and_collapse_are_rejected(self):
        flipped = self.mesh.triangles.copy()
        flipped[0] = flipped[0, [0, 2, 1]]
        report = check_mesh_geometry(TriangleMesh(flipped), 1.0, baseline_mesh=self.mesh,
                                     same_connectivity=True, check_parity=False)
        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["same_connectivity_orientation"].passed)
        collapsed = self.mesh.triangles.copy()
        collapsed[0, 1] = collapsed[0, 0]
        report = check_mesh_geometry(TriangleMesh(collapsed), 1.0, baseline_mesh=self.mesh,
                                     same_connectivity=True, check_parity=False)
        checks = {check.name: check for check in report.checks}
        self.assertFalse(checks["same_connectivity_new_degenerates"].passed)
        self.assertFalse(checks["nonzero_triangle_area"].passed)

    def test_missing_bottom_is_caught_by_vertical_flux(self):
        triangles = self.mesh.triangles
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        broken = TriangleMesh(triangles[normals[:, 2] >= 0])
        report = check_mesh_geometry(broken, 1.0)
        checks = {check.name: check for check in report.checks}
        self.assertTrue(checks["sampled_section_parity"].passed)
        self.assertTrue(checks["immersed_horizontal_normal_flux"].passed)
        self.assertFalse(checks["sampled_vertical_normal_flux"].passed)

    def test_remesh_does_not_silently_assume_corresponding_indices(self):
        with self.assertRaises(ValueError):
            check_mesh_geometry(self.mesh, 1.0, same_connectivity=True)
        report = check_mesh_geometry(self.mesh, 1.0, same_connectivity=False)
        self.assertNotIn("same_connectivity_orientation", {check.name for check in report.checks})

    def test_persistent_parametric_pole_facets_are_allowed_with_correspondence(self):
        collapsed = np.repeat(self.mesh.triangles[0, :1], 3, axis=0)[None, :, :]
        with_pole = TriangleMesh(np.concatenate((self.mesh.triangles, collapsed)))
        report = check_mesh_geometry(with_pole, 1.0, baseline_mesh=with_pole,
                                     same_connectivity=True, measured_volume_m3=20.0)
        checks = {check.name: check for check in report.checks}
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(checks["persistent_parametric_degenerates"].value, 1)
        self.assertTrue(checks["nonzero_triangle_area"].passed)
        # Without corresponding baseline evidence the collapse remains invalid.
        self.assertFalse(check_mesh_geometry(with_pole, 1.0).passed)


if __name__ == "__main__":
    unittest.main()
