"""Longitudinal-map mathematics and baseline-preserving representation tests."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import unittest

import numpy as np

from hullopt.geometry import Face, Geometry, NurbsSurface, TriangleMesh, TrimLoop, evaluate_surface, load_hgeo, tessellate
from hullopt.geometry.transform import LongitudinalMap, transform_geometry, transform_mesh
from tests.hydro_fixtures import box_hgeo, geometry_from_hgeo, semicircle_prism_geometry


class LongitudinalMapTests(unittest.TestCase):
    def test_fixed_anchors_identity_outside_and_peak_shifts(self):
        xmap = LongitudinalMap(-2, 3, 13, 0.4, -0.8)
        anchors = np.array([-10.0, -2.0, 3.0, 13.0, 20.0])
        np.testing.assert_array_equal(xmap.map_x(anchors), anchors)
        np.testing.assert_array_equal(xmap.derivative(anchors), np.ones(5))
        self.assertAlmostEqual(xmap.map_x(0.5), 0.9)
        self.assertAlmostEqual(xmap.map_x(8.0), 7.2)

    def test_derivative_and_analytic_minimum(self):
        xmap = LongitudinalMap(0, 4, 10, -0.2, 0.4)
        x = np.linspace(-1, 11, 1001)
        step = 1e-6
        numerical = (xmap.map_x(x + step) - xmap.map_x(x - step)) / (2 * step)
        np.testing.assert_allclose(xmap.derivative(x), numerical, atol=2e-9)
        critical = (3 + math.sqrt(3)) / 6
        minimum_at = 4 + critical * 6  # positive forward shift has its minimum here
        self.assertAlmostEqual(xmap.derivative(minimum_at), xmap.minimum_derivative, places=14)
        self.assertGreater(np.diff(xmap.map_x(x)).min(), 0)

    def test_monotonicity_bound_rejects_both_shift_signs(self):
        limits = LongitudinalMap(0, 4, 10).coefficient_limit_m
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                with self.assertRaisesRegex(ValueError, "positive derivative"):
                    LongitudinalMap(0, 4, 10, sign * limits[0] * 1.001, 0)
                with self.assertRaisesRegex(ValueError, "positive derivative"):
                    LongitudinalMap(0, 4, 10, 0, sign * limits[1] * 1.001)
                with self.assertRaisesRegex(ValueError, "positive derivative"):
                    LongitudinalMap(0, 4, 10, sign * limits[0], 0)
        for anchors in ((0, 0, 10), (10, 4, 0), (0, float("nan"), 10)):
            with self.assertRaises(ValueError):
                LongitudinalMap(*anchors)

    def test_zero_map_is_exact_and_does_not_share_memory(self):
        xmap = LongitudinalMap(0, 4, 10)
        x = np.array([0.1, 2.0, 4.7, 9.9])
        mapped = xmap.map_x(x)
        np.testing.assert_array_equal(mapped, x)
        self.assertFalse(np.shares_memory(x, mapped))
        self.assertTrue(xmap.is_identity)
        with self.assertRaisesRegex(ValueError, "finite"):
            xmap.map_x(float("inf"))


class GeometryTransformTests(unittest.TestCase):
    def test_rational_control_points_weights_trims_and_baseline_immutable(self):
        geometry = semicircle_prism_geometry()
        geometry.metadata["nested"] = {"baseline": [1, 2]}
        before = deepcopy(geometry)
        xmap = LongitudinalMap(-1, 2, 5, 0.1, -0.2)
        result = transform_geometry(geometry, xmap)
        self.assertEqual(len(result.faces), len(geometry.faces))
        for source, target, saved in zip(geometry.faces, result.faces, before.faces):
            np.testing.assert_array_equal(source.surface.cv, saved.surface.cv)
            np.testing.assert_array_equal(target.surface.cv[..., 1:], source.surface.cv[..., 1:])
            expected = xmap.map_x(source.surface.cv[..., 0] / source.surface.cv[..., 3]) * source.surface.cv[..., 3]
            np.testing.assert_array_equal(target.surface.cv[..., 0], expected)
            self.assertFalse(np.shares_memory(source.surface.cv, target.surface.cv))
            for key in ("knots_u", "knots_v"):
                np.testing.assert_array_equal(getattr(source.surface, key), getattr(target.surface, key))
                self.assertFalse(np.shares_memory(getattr(source.surface, key), getattr(target.surface, key)))
            for source_loop, target_loop in zip(source.loops, target.loops):
                np.testing.assert_array_equal(source_loop.uv, target_loop.uv)
                self.assertFalse(np.shares_memory(source_loop.uv, target_loop.uv))
        result.metadata["nested"]["baseline"].append(3)
        self.assertEqual(geometry.metadata["nested"]["baseline"], [1, 2])
        self.assertEqual(result.metadata["longitudinal_transform"]["representation"], "nurbs_control_points")
        self.assertFalse(result.metadata["longitudinal_transform"]["requires_nurbs_verification"])
        with self.assertRaisesRegex(ValueError, "chained"):
            transform_geometry(result, xmap)

    def test_default_layer_scope_keeps_appendage_fixed(self):
        geometry = geometry_from_hgeo(box_hgeo())
        geometry.faces[-1].layer = "SKEG"
        xmap = LongitudinalMap(-1, 5, 11, 0.1, 0.2)
        result = transform_geometry(geometry, xmap)
        np.testing.assert_array_equal(result.faces[-1].surface.cv, geometry.faces[-1].surface.cv)
        self.assertFalse(np.array_equal(result.faces[0].surface.cv, geometry.faces[0].surface.cv))
        with self.assertRaisesRegex(ValueError, "layers unavailable"):
            transform_geometry(geometry, xmap, layers=["MISSING"])

    def test_zero_geometry_map_preserves_all_control_coordinates_exactly(self):
        geometry = semicircle_prism_geometry()
        result = transform_geometry(geometry, LongitudinalMap(0, 2, 4))
        for source, target in zip(geometry.faces, result.faces):
            np.testing.assert_array_equal(source.surface.cv, target.surface.cv)
        np.testing.assert_array_equal(tessellate(geometry).triangles, tessellate(result).triangles)

    def test_nonlinear_control_map_differs_from_pointwise_vertex_map(self):
        # A coarse quadratic net makes the representation distinction visible.
        cv = np.zeros((3, 2, 4))
        cv[..., 3] = 1
        cv[:, :, 0] = np.array([0, 2, 4])[:, None]
        cv[:, :, 1] = np.array([0, 1])[None, :]
        surface = NurbsSurface((3, 2), cv, [0, 0, 1, 1], [0, 1])
        geometry = Geometry((Face(surface),))
        xmap = LongitudinalMap(0, 2, 4, 0.2, -0.1)
        transformed = transform_geometry(geometry, xmap)
        u, v = np.array([0.25, 0.75]), np.array([0.5, 0.5])
        original = evaluate_surface(surface, u, v)
        remapped_control_surface = evaluate_surface(transformed.faces[0].surface, u, v)
        self.assertGreater(np.max(np.abs(remapped_control_surface[:, 0] - xmap.map_x(original[:, 0]))), 0.1)
        # All three control-column x values are anchors, so NURBS is unchanged.
        np.testing.assert_array_equal(remapped_control_surface, original)

    def test_mesh_map_is_labelled_approximation_and_preserves_metadata(self):
        triangle = np.array([[[1, 0, 0], [2, 1, 0], [3, 0, 1]],
                             [[1, 0, 0], [2, 1, 0], [3, 0, 1]]], dtype=float)
        mesh = TriangleMesh(triangle, face_ids=[4, 5], layers=["HULL", "SKEG"], metadata={"source": [1]})
        result = transform_mesh(mesh, LongitudinalMap(0, 2, 4, 0.2, -0.1), layers=["HULL"])
        np.testing.assert_array_equal(mesh.triangles, triangle)
        np.testing.assert_array_equal(result.triangles[1], mesh.triangles[1])
        np.testing.assert_array_equal(result.triangles[:, :, 1:], mesh.triangles[:, :, 1:])
        np.testing.assert_array_equal(result.face_ids, mesh.face_ids)
        np.testing.assert_array_equal(result.layers, mesh.layers)
        self.assertFalse(np.shares_memory(result.triangles, mesh.triangles))
        self.assertTrue(result.metadata["longitudinal_transform"]["requires_nurbs_verification"])
        self.assertEqual(result.metadata["longitudinal_transform"]["representation"], "mesh_vertices_approximation")
        with self.assertRaisesRegex(ValueError, "chained"):
            transform_mesh(result, LongitudinalMap(0, 2, 4))

    def test_collapsed_uv_valid_facets_are_retained_only_when_requested(self):
        cv = np.zeros((2, 2, 4))
        cv[..., 3] = 1
        cv[:, :, 0] = [[0, 1], [2, 3]]
        geometry = Geometry((Face(NurbsSurface((2, 2), cv, [0, 1], [0, 1])),))
        filtered = tessellate(geometry)
        retained = tessellate(geometry, keep_degenerate=True)
        self.assertEqual(len(filtered.triangles), 0)
        self.assertGreater(len(retained.triangles), 0)
        self.assertEqual(retained.metadata["collapsed_triangle_count"], len(retained.triangles))
        self.assertEqual(retained.area_m2, 0)


if __name__ == "__main__":
    unittest.main()
