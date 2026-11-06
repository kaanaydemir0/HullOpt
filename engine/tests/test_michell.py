"""Michell numerical verification, distinct from experimental validation."""

from __future__ import annotations

import math
import unittest

import numpy as np
from scipy.integrate import quad

from hullopt.geometry import TriangleMesh
from hullopt.resistance.base import Conditions
from hullopt.resistance.friction import ITTC57Friction, KNOT_TO_M_S
from hullopt.resistance.michell import MichellThinShip, OffsetHull

try:
    from .hydro_fixtures import box_mesh, oriented_triangles
except ImportError:
    from hydro_fixtures import box_mesh, oriented_triangles


def wigley_offsets(nx=161, nz=81, *, length=10.0, beam=1.0, draft=0.625, shift=(0, 0)):
    """Published parabolic Wigley shape, Pierre2015 p7; z is upward here."""
    x, z = np.linspace(-length / 2, length / 2, nx), np.linspace(-draft, 0, nz)
    y = beam / 2 * (1 - (2 * x / length)**2)[None, :] * (1 - (z / draft)**2)[:, None]
    return OffsetHull(x + shift[0], z + shift[1], y, shift[1])


def wigley_analytic_amplitude(lambdas, velocity, *, length=10.0, beam=1.0, draft=0.625, gravity=9.80665):
    """Independent elementary inner integrals, without offset panels."""
    lam = np.asarray(lambdas)
    k0 = gravity / velocity**2
    k, r = k0 * lam, k0 * lam**2 * draft
    horizontal = 4j * beam / length**2 * (length * np.cos(k * length / 2) / k
                                                    - 2 * np.sin(k * length / 2) / k**2)
    vertical = draft * (r**2 - 2 + 2 * (r + 1) * np.exp(-r)) / r**3
    return horizontal * vertical


def independent_wigley_force(froude, *, rho=1000.0, length=10.0, beam=1.0, draft=0.625):
    velocity = froude * math.sqrt(9.80665 * length)

    def integrand(t):
        lam = math.cosh(t)
        amplitude = wigley_analytic_amplitude(np.array([lam]), velocity,
                                              length=length, beam=beam, draft=draft)[0]
        return lam**2 * abs(amplitude)**2

    # Adaptive scalar integration on t panels, independent of production Gauss.
    edges = np.linspace(0, math.acosh(256), 201)
    integral = sum(quad(integrand, a, b, epsabs=1e-12, epsrel=2e-9)[0]
                   for a, b in zip(edges[:-1], edges[1:]))
    return 4 * rho * 9.80665**2 / (math.pi * velocity**2) * integral


def pointed_bow_mesh():
    # Constant stern halfbreadth=1 at x=0, sharp bow at x=10.
    vertices = [(0, -1, 0), (0, 1, 0), (0, -1, 2), (0, 1, 2),
                (10, 0, 0), (10, 0, 2)]
    triangles = oriented_triangles(vertices, [(0, 1, 3, 2), (0, 2, 5, 4),
                                              (1, 4, 5, 3), (0, 4, 1), (2, 3, 5)])
    return TriangleMesh(triangles)


class OffsetHullTests(unittest.TestCase):
    def test_offsets_are_copied_and_deeply_readonly(self):
        x = np.array([0., 1., 2.])
        z = np.array([-1., 0.])
        y = np.array([[0., 0., 0.], [0., 1., 0.]])
        hull = OffsetHull(x, z, y, 0.)
        y[1, 1] = 99
        self.assertEqual(hull.halfbreadths_m[1, 1], 1.)
        for array in (hull.xs_m, hull.zs_m, hull.halfbreadths_m):
            with self.assertRaises(ValueError):
                array.setflags(write=True)
        with self.assertRaises(TypeError):
            hull.diagnostics["test"] = 1

    def test_invalid_offsets_rejected(self):
        for x, z, y, wl in (([0, 0, 1], [-1, 0], [[0, 1, 0]] * 2, 0),
                            ([0, 1, 2], [-1, 0], [[0, -1, 0]] * 2, 0),
                            ([0, 1, 2], [-1, 0], [[0, math.nan, 0]] * 2, 0),
                            ([0, 1, 2], [-1, 0], [[0, 1, 0]] * 2, 1)):
            with self.subTest(x=x, z=z, y=y, wl=wl), self.assertRaises(ValueError):
                OffsetHull(x, z, y, wl)

    def test_box_mesh_widths_independent_of_normals(self):
        original = box_mesh()
        reversed_mesh = TriangleMesh(original.triangles[:, [0, 2, 1]])
        for mesh in (original, reversed_mesh):
            hull = OffsetHull.from_mesh(mesh, 1.0, nx=11, nz=9)
            np.testing.assert_allclose(hull.halfbreadths_m, 1., atol=2e-12)
            self.assertAlmostEqual(hull.diagnostics["symmetry_center_error_over_beam"], 0.)

    def test_pointed_bow_widths_and_waterline_translation(self):
        mesh = pointed_bow_mesh()
        hull = OffsetHull.from_mesh(mesh, 1., nx=21, nz=11)
        expected = np.broadcast_to(1 - hull.xs_m[None, :] / 10, hull.halfbreadths_m.shape)
        np.testing.assert_allclose(hull.halfbreadths_m, expected, atol=2e-9)
        moved = TriangleMesh(mesh.triangles + [27., 4., 15.])
        translated = OffsetHull.from_mesh(moved, 16., nx=21, nz=11, centerplane_y_m=4.)
        np.testing.assert_allclose(translated.halfbreadths_m, hull.halfbreadths_m, atol=1e-13)

    def test_asymmetric_hull_rejected(self):
        moved = TriangleMesh(box_mesh().triangles + [0., 0.2, 0.])
        with self.assertRaisesRegex(ValueError, "asymmetry"):
            OffsetHull.from_mesh(moved, 1.)

    def test_open_side_rejected_for_odd_parity(self):
        mesh = box_mesh()
        # Remove the side at y=+1 without changing all other triangles.
        keep = ~np.all(mesh.triangles[:, :, 1] == 1., axis=1)
        with self.assertRaisesRegex(ValueError, "Odd section intersection parity"):
            OffsetHull.from_mesh(TriangleMesh(mesh.triangles[keep]), 1.)

    def test_multiple_width_intervals_rejected(self):
        first, second = box_mesh(), box_mesh()
        mesh = TriangleMesh(np.concatenate((first.triangles + [0., -2., 0.],
                                            second.triangles + [0., 2., 0.])))
        with self.assertRaisesRegex(ValueError, "Multiple submerged width intervals"):
            OffsetHull.from_mesh(mesh, 1.)

    def test_explicit_equivalent_width_preserves_total_immersed_width(self):
        mesh = TriangleMesh(np.concatenate((box_mesh().triangles + [0., -2., 0.],
                                            box_mesh().triangles + [0., 2., 0.])))
        hull = OffsetHull.from_mesh(mesh, 1., nx=11, nz=9, multi_interval_policy="equivalent_width")
        np.testing.assert_allclose(hull.halfbreadths_m, 2., atol=1e-13)
        self.assertEqual(hull.diagnostics["multi_interval_scanlines"], 99)
        self.assertEqual(hull.diagnostics["max_intervals_per_scanline"], 2)
        self.assertAlmostEqual(hull.diagnostics["offset_volume_m3"], 40.)

    def test_equivalent_width_checks_inner_intervals_for_symmetry(self):
        # Symmetric outer envelope, asymmetric internal cavity boundaries.
        left = box_mesh(breadth=1.).triangles + [0., -2.5, 0.]
        right = box_mesh(breadth=2.).triangles + [0., 2., 0.]
        mesh = TriangleMesh(np.concatenate((left, right)))
        with self.assertRaisesRegex(ValueError, "asymmetry"):
            OffsetHull.from_mesh(mesh, 1., multi_interval_policy="equivalent_width")

    def test_raked_transom_anchors_come_from_cuts_not_x_grid(self):
        triangles = pointed_bow_mesh().triangles.copy()
        triangles[:, :, 0] -= 0.5 * triangles[:, :, 2]
        mesh = TriangleMesh(triangles)
        coarse = OffsetHull.from_mesh(mesh, 1., nx=21, nz=11, bow_padding_fraction_lwl=.01)
        fine = OffsetHull.from_mesh(mesh, 1., nx=81, nz=11, bow_padding_fraction_lwl=.01)
        anchors = coarse.diagnostics["transom_row_anchors_index_x_halfbreadth"]
        np.testing.assert_allclose(anchors, fine.diagnostics["transom_row_anchors_index_x_halfbreadth"], atol=1e-13)
        for iz, x, halfbreadth in anchors:
            self.assertAlmostEqual(x, -0.5 * coarse.zs_m[iz], delta=1e-9)
            self.assertAlmostEqual(halfbreadth, 1., delta=2e-9)
        with self.assertRaisesRegex(ValueError, "explicit transom_closure_length_m"):
            MichellThinShip(coarse)
        result = MichellThinShip(coarse, transom_closure_length_m=1., check_convergence=False).evaluate(None, Conditions((7.,)))
        self.assertEqual(result.metadata["transom_closure"]["row_anchor_count"], 11)


class MichellTests(unittest.TestCase):
    def test_panel_amplitudes_match_analytic_wigley_without_aliasing(self):
        backend = MichellThinShip(wigley_offsets(321, 161))
        lam = np.array([1., 1.7, 4., 12., 50., 100.])
        velocity = .35 * math.sqrt(9.80665 * 10)
        exact = wigley_analytic_amplitude(lam, velocity)
        actual = backend.amplitudes(lam, velocity)
        # Finite offset-grid interpolation error; includes high-lambda cases.
        np.testing.assert_allclose(actual, exact, rtol=0.009, atol=3e-7)
        np.testing.assert_allclose(actual.real, 0., atol=1e-15)

    def test_wigley_wave_force_matches_independent_analytic_integration(self):
        offsets = wigley_offsets()
        backend = MichellThinShip(offsets)
        for froude in (.25, .35, .45):
            speed = froude * math.sqrt(9.80665 * 10) / KNOT_TO_M_S
            result = backend.evaluate(None, Conditions((speed,), rho_kg_m3=1000.))
            expected = independent_wigley_force(froude)
            self.assertAlmostEqual(result.values[0] / expected, 1., delta=0.002)
            self.assertTrue(result.metadata["numerical_convergence"][0]["converged"])
            self.assertEqual(result.quantity, "wave_force")

    def test_wave_force_scales_with_beam_squared_and_density(self):
        settings = {"check_convergence": False, "lambda_max": 12.}
        conditions = Conditions((7.,), rho_kg_m3=1000.)
        baseline = MichellThinShip(wigley_offsets(41, 21), **settings).evaluate(None, conditions).values[0]
        wide = MichellThinShip(wigley_offsets(41, 21, beam=2.), **settings).evaluate(None, conditions).values[0]
        dense = MichellThinShip(wigley_offsets(41, 21), **settings).evaluate(
            None, Conditions((7.,), rho_kg_m3=1025.)).values[0]
        self.assertAlmostEqual(wide / baseline, 4., places=12)
        self.assertAlmostEqual(dense / baseline, 1.025, places=12)

    def test_translation_preserves_wave_force(self):
        settings = {"check_convergence": False, "lambda_max": 12.}
        condition = Conditions((7.,))
        first = MichellThinShip(wigley_offsets(41, 21), **settings).evaluate(None, condition)
        moved = MichellThinShip(wigley_offsets(41, 21, shift=(1e5, 12.)), **settings).evaluate(None, condition)
        self.assertAlmostEqual(first.values[0], moved.values[0], delta=1e-9)

    def test_immersed_transom_requires_explicit_closure_and_records_assumption(self):
        hull = OffsetHull.from_mesh(pointed_bow_mesh(), 1.)
        with self.assertRaisesRegex(ValueError, "explicit transom_closure_length_m"):
            MichellThinShip(hull)
        backend = MichellThinShip(hull, transom_closure_length_m=1., check_convergence=False)
        result = backend.evaluate(None, Conditions((7.,)))
        self.assertGreater(result.values[0], 0)
        self.assertTrue(result.metadata["transom_closure"]["applied"])
        self.assertFalse(result.metadata["transom_closure"]["included_in_friction_area"])
        self.assertTrue(any("heuristic" in warning for warning in result.warnings))

    def test_combined_result_keeps_components_and_omissions_explicit(self):
        offsets = wigley_offsets(41, 21)
        conditions = Conditions((7.,))
        measurements = {"lwl_m": 10., "wetted_area_m2": 15.}
        result = MichellThinShip(offsets, include_friction=True, check_convergence=False).evaluate(measurements, conditions)
        friction = ITTC57Friction().evaluate(measurements, conditions)
        self.assertAlmostEqual(result.values[0], result.metadata["wave_force_n"][0] + friction.values[0])
        self.assertEqual(result.quantity, "wave_plus_friction_force")
        self.assertIn("viscous pressure/form factor", result.metadata["omitted_components"])

    def test_unconverged_cutoff_cannot_look_converged(self):
        result = MichellThinShip(wigley_offsets(41, 21), lambda_max=1.01).evaluate(None, Conditions((7.,)))
        self.assertFalse(result.metadata["numerical_convergence"][0]["converged"])
        self.assertTrue(any("has not converged" in warning for warning in result.warnings))

    def test_adaptive_cutoff_converges_where_the_fixed_cutoff_does_not(self):
        offsets, conditions = wigley_offsets(41, 21), Conditions((7.,))
        reference = MichellThinShip(offsets).evaluate(None, conditions)
        adapted = MichellThinShip(offsets, lambda_max=1.01, lambda_max_limit=1024.).evaluate(None, conditions)
        item = adapted.metadata["numerical_convergence"][0]
        self.assertTrue(item["converged"])
        self.assertGreater(item["adaptive_refinements"], 0)
        self.assertGreater(item["returned_lambda_max"], 2 * 1.01)
        self.assertAlmostEqual(adapted.values[0] / reference.values[0], 1., delta=0.01)
        self.assertFalse(any("has not converged" in warning for warning in adapted.warnings))

    def test_adaptive_limit_leaves_converged_speeds_unchanged(self):
        offsets, conditions = wigley_offsets(41, 21), Conditions((6., 7.))
        fixed = MichellThinShip(offsets).evaluate(None, conditions)
        seen = []
        adaptive = MichellThinShip(offsets, lambda_max_limit=1024.).evaluate(
            None, conditions, progress=lambda index, count, speed: seen.append((index, count, speed)))
        self.assertEqual(adaptive.values, fixed.values)
        self.assertEqual(seen, [(0, 2, 6.), (1, 2, 7.)])
        for item in adaptive.metadata["numerical_convergence"]:
            self.assertNotIn("adaptive_refinements", item)

    def test_adaptive_limit_is_respected_and_reported(self):
        result = MichellThinShip(wigley_offsets(41, 21), lambda_max=1.01, lambda_max_limit=2.02).evaluate(
            None, Conditions((7.,)))
        item = result.metadata["numerical_convergence"][0]
        self.assertFalse(item["converged"])
        self.assertLessEqual(item["returned_lambda_max"], 2 * 2.02)
        self.assertTrue(any("Adaptive refinement stopped" in warning for warning in result.warnings))

    def test_invalid_solver_settings_rejected(self):
        hull = wigley_offsets(11, 5)
        for settings in ({"lambda_max": 1.}, {"gravity_m_s2": 0.}, {"quadrature_order": 3},
                         {"transom_closure_length_m": -1.}, {"convergence_tolerance": math.nan},
                         {"lambda_max_limit": 16.}, {"lambda_max_limit": math.inf}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                MichellThinShip(hull, **settings)


if __name__ == "__main__":
    unittest.main()
