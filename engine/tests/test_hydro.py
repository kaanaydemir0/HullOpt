"""Analytic acceptance tests for exact clipping, moments, sections and balance."""

from __future__ import annotations

import math
import unittest

import numpy as np

from hullopt.geometry import TriangleMesh, tessellate
from hullopt.hydro.hydrostatics import measure
from hullopt.hydro.equilibrium import solve_equilibrium
from hullopt.hydro.sections import sectional_areas

try:
    from .hydro_fixtures import (box_mesh, box_hgeo, geometry_from_hgeo,
                                 tetrahedron_mesh, semicircle_prism_geometry,
                                 parity_scan_volume)
except ImportError:
    from hydro_fixtures import (box_mesh, box_hgeo, geometry_from_hgeo,
                                tetrahedron_mesh, semicircle_prism_geometry,
                                parity_scan_volume)


class AnalyticHydrostaticsTests(unittest.TestCase):
    def assertRelative(self, actual, expected, tolerance):
        self.assertLessEqual(abs(actual - expected), abs(expected) * tolerance,
                             f"{actual!r} differs from {expected!r} by >{tolerance:.3%}")

    def test_box_exact_hydrostatics(self):
        result = measure(box_mesh(), 1.0)
        expected = {"volume_m3": 20.0, "waterplane_area_m2": 20.0,
                    "lwl_m": 10.0, "bwl_m": 2.0, "draft_m": 1.0,
                    "lcb_m": 5.0, "kb_m": 0.5, "lcf_m": 5.0,
                    "wetted_area_m2": 44.0, "transom_area_m2": 2.0,
                    "transom_wetted_area_m2": 2.0, "max_section_area_m2": 2.0,
                    "cb": 1.0, "cp": 1.0, "cx": 1.0, "cwp": 1.0,
                    "lcb_percent_lwl": 0.0}
        for field, value in expected.items():
            with self.subTest(field=field):
                self.assertAlmostEqual(getattr(result, field), value, delta=2e-12)
        np.testing.assert_allclose(result.section_area_m2[1:-1], 2.0,
                                   rtol=0, atol=2e-12)
        payload = result.to_dict()
        self.assertEqual(payload["volume_m3"], result.volume_m3)
        self.assertIsInstance(payload["section_x_m"], list)

    def test_bilinear_box_reversed_face_and_millimeter_scaling(self):
        for meters_per_unit in (1.0, 0.001):
            with self.subTest(meters_per_unit=meters_per_unit):
                geometry = geometry_from_hgeo(box_hgeo(meters_per_unit=meters_per_unit))
                result = measure(tessellate(geometry), 1.0)
                for field, value in (("volume_m3", 20.0), ("lcb_m", 5.0),
                                     ("kb_m", 0.5), ("waterplane_area_m2", 20.0),
                                     ("wetted_area_m2", 44.0)):
                    self.assertAlmostEqual(getattr(result, field), value, delta=2e-10)

    def test_clipped_tetrahedron_quadratic_moments(self):
        # Full tetrahedron V=4; cutting z<=2 removes V=.5 with centroid
        # (.25,.375,2.5). Full centroid is (.5,.75,1), giving these exact moments.
        result = measure(tetrahedron_mesh(), 2.0)
        self.assertAlmostEqual(result.volume_m3, 3.5, delta=2e-13)
        self.assertAlmostEqual(result.lcb_m, 15 / 28, delta=2e-13)
        self.assertAlmostEqual(result.kb_m, 11 / 14, delta=2e-13)
        self.assertAlmostEqual(result.waterplane_area_m2, 0.75, delta=2e-13)
        self.assertAlmostEqual(result.lcf_m, 1 / 3, delta=2e-13)

    def test_vertical_translation_keeps_draft_and_moves_kb(self):
        original = box_mesh()
        shift = np.array([7.0, -9.0, 12.5])
        moved = TriangleMesh(original.triangles + shift)
        result = measure(moved, 13.5)
        self.assertAlmostEqual(result.volume_m3, 20.0, delta=2e-12)
        self.assertAlmostEqual(result.draft_m, 1.0, delta=2e-12)
        self.assertAlmostEqual(result.lcb_m, 12.0, delta=2e-12)
        self.assertAlmostEqual(result.kb_m, 13.0, delta=2e-12)
        self.assertAlmostEqual(result.lcf_m, 12.0, delta=2e-12)

    def test_sectional_areas_and_independent_parity_volume(self):
        mesh = box_mesh()
        xs = np.array([0.137, 2.375, 4.6, 7.117, 9.921])
        np.testing.assert_allclose(sectional_areas(mesh, xs, 1.0), 2.0, rtol=0, atol=2e-12)
        volume, scanlines = parity_scan_volume(mesh.triangles, 1.0, nx=61, nz=41)
        self.assertAlmostEqual(volume, 20.0, delta=5e-11)
        self.assertEqual(scanlines, 61 * 41)

    def test_volume_derivative_is_waterplane_area(self):
        mesh = tetrahedron_mesh()
        wl = 1.37
        step = 1e-5
        above = measure(mesh, wl + step, include_sections=False).volume_m3
        below = measure(mesh, wl - step, include_sections=False).volume_m3
        result = measure(mesh, wl, include_sections=False)
        self.assertAlmostEqual((above - below) / (2 * step), result.waterplane_area_m2,
                               delta=2e-9)

    def test_rational_semicircle_prism(self):
        geometry = semicircle_prism_geometry()
        mesh = tessellate(geometry, min_grid=65)
        result = measure(mesh, 0.0)
        self.assertRelative(result.volume_m3, 2 * math.pi, 0.001)
        self.assertRelative(result.waterplane_area_m2, 8.0, 0.001)
        self.assertRelative(result.kb_m, -4 / (3 * math.pi), 0.001)
        self.assertRelative(result.wetted_area_m2, 5 * math.pi, 0.001)
        self.assertAlmostEqual(result.lcb_m, 2.0, delta=2e-10)

    def test_density_does_not_change_geometric_quantities(self):
        salt = measure(box_mesh(), 1.0, rho_kg_m3=1025)
        fresh = measure(box_mesh(), 1.0, rho_kg_m3=998)
        self.assertEqual(salt.volume_m3, fresh.volume_m3)
        self.assertEqual(salt.waterplane_area_m2, fresh.waterplane_area_m2)
        self.assertAlmostEqual(salt.displacement_kg, 20 * 1025, delta=1e-9)
        self.assertAlmostEqual(fresh.displacement_kg, 20 * 998, delta=1e-9)

    def test_coplanar_deck_is_excluded_from_wetted_area(self):
        result = measure(box_mesh(), 2.0)
        self.assertAlmostEqual(result.volume_m3, 40.0, delta=2e-12)
        self.assertAlmostEqual(result.waterplane_area_m2, 20.0, delta=2e-12)
        self.assertAlmostEqual(result.wetted_area_m2, 68.0, delta=2e-12)

    def test_globally_inward_oriented_skin_is_rejected(self):
        original = box_mesh()
        inward = TriangleMesh(original.triangles[:, [0, 2, 1]])
        with self.assertRaises(ValueError):
            measure(inward, 1.0)

    def test_invalid_waterline_density_and_mesh_are_rejected(self):
        for wl in (float("nan"), float("inf"), -1.0):
            with self.subTest(wl=wl), self.assertRaises(ValueError):
                measure(box_mesh(), wl)
        for density in (0.0, -1025.0, float("nan")):
            with self.subTest(density=density), self.assertRaises(ValueError):
                measure(box_mesh(), 1.0, rho_kg_m3=density)


class EquilibriumTests(unittest.TestCase):
    def test_box_fixed_displacement_within_fifty_millimeters(self):
        result = solve_equilibrium(box_mesh(), 20.6, 1.0)
        self.assertAlmostEqual(result.z_wl_m, 1.03, delta=1e-8)
        self.assertAlmostEqual(result.hydrostatics.volume_m3, 20.6, delta=2.06e-5)
        self.assertLessEqual(result.iterations, 5)
        self.assertIsInstance(result.to_dict(), dict)

    def test_nonlinear_tetrahedron_equilibrium(self):
        # V(z)=4[1-(1-z/4)^3] for 0<z<4.
        target_wl = 1.031
        target = 4 * (1 - (1 - target_wl / 4) ** 3)
        result = solve_equilibrium(tetrahedron_mesh(), target, 1.0,
                                   relative_tolerance=1e-10)
        self.assertAlmostEqual(result.z_wl_m, target_wl, delta=2e-9)
        self.assertRelativeVolume(result.hydrostatics.volume_m3, target, 1e-10)

    def assertRelativeVolume(self, actual, expected, tolerance):
        self.assertLessEqual(abs(actual / expected - 1), tolerance)

    def test_displacement_outside_draft_change_bracket_is_rejected(self):
        for volume in (18.9, 21.1, 0.0, -1.0, float("nan")):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                solve_equilibrium(box_mesh(), volume, 1.0)

    def test_safeguard_when_newton_step_leaves_bracket(self):
        # Near the tetrahedron tip dV/dz is small: a direct Newton update from
        # z=3 to this target leaves [2.1,3.9], requiring the bracket safeguard.
        target_wl = 2.11
        target = 4 * (1 - (1 - target_wl / 4) ** 3)
        result = solve_equilibrium(tetrahedron_mesh(), target, 3.0,
                                   draft_change_limit_m=0.9, relative_tolerance=1e-10)
        self.assertAlmostEqual(result.z_wl_m, target_wl, delta=2e-9)
        self.assertRelativeVolume(result.hydrostatics.volume_m3, target, 1e-10)

    def test_exact_bracket_boundary_is_allowed(self):
        for volume, wl in ((19.0, 0.95), (21.0, 1.05)):
            with self.subTest(volume=volume):
                result = solve_equilibrium(box_mesh(), volume, 1.0)
                self.assertAlmostEqual(result.z_wl_m, wl, delta=1e-8)

    def test_requested_section_count_is_retained(self):
        result = solve_equilibrium(box_mesh(), 20.6, 1.0, section_count=13)
        self.assertEqual(len(result.hydrostatics.section_x_m), 13)
        self.assertEqual(len(result.hydrostatics.section_area_m2), 13)
        np.testing.assert_allclose(result.hydrostatics.section_area_m2, 2.06,
                                   rtol=0, atol=2e-10)

    def test_invalid_solver_limits_and_section_count_are_rejected(self):
        for limit in (float("nan"), float("inf"), 0.0, -0.05):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                solve_equilibrium(box_mesh(), 20.6, 1.0, draft_change_limit_m=limit)
        for count in (0, 2, 3.5, float("nan")):
            with self.subTest(section_count=count), self.assertRaises(ValueError):
                solve_equilibrium(box_mesh(), 20.6, 1.0, section_count=count)


if __name__ == "__main__":
    unittest.main()
