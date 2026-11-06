"""Analytic and production-dump checks for the neutral geometry pipeline."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from hullopt.geometry import (Face, Geometry, NurbsSurface, TriangleMesh, TrimLoop,
                              basis_matrix, evaluate_surface, load_hgeo, tessellate, tessellate_face)
from tests.hydro_fixtures import box_hgeo, geometry_from_hgeo, semicircle_prism_geometry


class NurbsTests(unittest.TestCase):
    def test_basis_partition_and_upper_endpoint(self):
        knots = np.array([0, 0, 0.4, 1, 1], dtype=float)
        basis = basis_matrix(np.linspace(0, 1, 101), knots, 3, 4)
        np.testing.assert_allclose(basis.sum(axis=1), 1.0, atol=3e-15)
        np.testing.assert_allclose(basis[0], [1, 0, 0, 0], atol=3e-15)
        np.testing.assert_allclose(basis[-1], [0, 0, 0, 1], atol=3e-15)
        np.testing.assert_array_equal(basis[[0, -1]], [[1, 0, 0, 0], [0, 0, 0, 1]])

    def test_paired_grid_and_nonclamped_endpoint(self):
        # A non-clamped quadratic basis ends with [0,.5,.5], rather than
        # incorrectly forcing its last basis value to one.
        basis = basis_matrix([1, 2], np.array([0, 1, 2, 3]), 3, 3)
        np.testing.assert_allclose(basis, [[0.5, 0.5, 0], [0, 0.5, 0.5]], atol=1e-14)
        geometry = geometry_from_hgeo(box_hgeo())
        surface = geometry.faces[2].surface
        grid = evaluate_surface(surface, [0, 0.5, 1], [0, 1], grid=True)
        paired = evaluate_surface(surface, [0, 0.5, 1], [1, 1, 1])
        np.testing.assert_allclose(grid[:, 1], paired, atol=1e-14)

    def test_rational_semicircle_is_exact(self):
        surface = semicircle_prism_geometry().faces[0].surface
        points = evaluate_surface(surface, np.full(201, 0.37), np.linspace(0, 2, 201))
        np.testing.assert_allclose(points[:, 0], 4 * 0.37, atol=2e-14)
        np.testing.assert_allclose(np.sum(points[:, 1:] ** 2, axis=1), 1.0, atol=2e-14)
        self.assertLessEqual(points[:, 2].max(), 1e-14)

    def test_invalid_data_and_outside_domain(self):
        surface = geometry_from_hgeo(box_hgeo()).faces[0].surface
        with self.assertRaisesRegex(ValueError, "Parameters must lie"):
            evaluate_surface(surface, -0.01, 0.5)
        with self.assertRaisesRegex(ValueError, "OpenNURBS"):
            NurbsSurface((2, 2), np.ones((2, 2, 4)), [0], [0, 1])


class GeometryTests(unittest.TestCase):
    def test_units_orientation_and_face_provenance(self):
        metre = tessellate(geometry_from_hgeo(box_hgeo()))
        millimetre = tessellate(geometry_from_hgeo(box_hgeo(meters_per_unit=0.001)))
        np.testing.assert_allclose(metre.triangles, millimetre.triangles, atol=1e-14)
        self.assertAlmostEqual(metre.area_m2, 88.0, places=11)
        centre = np.array([5.0, 0.0, 1.0])
        triangles = metre.triangles
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        self.assertTrue(np.all(np.sum(normal * (triangles.mean(axis=1) - centre), axis=1) > 0))
        self.assertEqual(set(metre.face_ids), set(range(6)))
        self.assertEqual(set(metre.layers), {"HULL"})

    def test_trimmed_plane_with_circular_hole(self):
        cv = np.array([[[0, 0, 0, 1], [0, 2, 0, 1]],
                       [[4, 0, 0, 1], [4, 2, 0, 1]]], dtype=float)
        surface = NurbsSurface((2, 2), cv, [0, 1], [0, 1])
        angle = np.linspace(0, 2 * math.pi, 257)[:-1]
        hole = np.column_stack((0.5 + 0.125 * np.cos(angle), 0.5 + 0.25 * np.sin(angle)))
        outer = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
        face = Face(surface, (TrimLoop("outer", outer), TrimLoop("inner", hole)))
        triangles = tessellate_face(face)
        area = TriangleMesh(triangles).area_m2
        expected = 8 - math.pi / 4
        self.assertLess(abs(area / expected - 1), 0.002)
        centroid_radius = np.linalg.norm(triangles.mean(axis=1)[:, :2] - [2, 1], axis=1)
        self.assertGreater(centroid_radius.min(), 0.4999)

    def test_trims_slightly_past_the_domain_are_clamped(self):
        cv = np.array([[[0, 0, 0, 1], [0, 2, 0, 1]],
                       [[4, 0, 0, 1], [4, 2, 0, 1]]], dtype=float)
        surface = NurbsSurface((2, 2), cv, [0, 3916.46], [0, 996.37])
        inside = np.array([[0, 0], [3916.46, 0], [3916.46, 996.37], [0, 996.37]])
        past = inside.copy()
        past[1:3, 0] += 0.0036          # 9e-7 of the span, as Rhino left it on a joined face
        exact = TriangleMesh(tessellate_face(Face(surface, (TrimLoop("outer", inside),)))).area_m2
        clamped = TriangleMesh(tessellate_face(Face(surface, (TrimLoop("outer", past),)))).area_m2
        self.assertAlmostEqual(clamped, exact, places=12)
        far = inside.copy()
        far[1:3, 0] += 1.0              # 2.6e-4 of the span: a real modelling error
        with self.assertRaisesRegex(ValueError, "outside its surface"):
            tessellate_face(Face(surface, (TrimLoop("outer", far),)))

    def test_reverse_flips_normal(self):
        geometry = geometry_from_hgeo(box_hgeo())
        face = geometry.faces[0]
        reversed_triangles = tessellate_face(face)
        face.rev = not face.rev
        forward_triangles = tessellate_face(face)
        np.testing.assert_allclose(reversed_triangles[:, [0, 2, 1]], forward_triangles)

    def test_rational_semicircle_mesh_area(self):
        mesh = tessellate(semicircle_prism_geometry())
        # Curved side pi*R*L, two half-disk caps together pi*R^2.
        self.assertLess(abs(mesh.area_m2 / (5 * math.pi) - 1), 0.001)

    def test_loader_rejects_unknown_layer_and_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.json"
            path.write_text(json.dumps(box_hgeo()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "layers unavailable"):
                load_hgeo(path, layers=["SKEG"])
            path.write_text('{"format":"other"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported geometry"):
                load_hgeo(path)


if __name__ == "__main__":
    unittest.main()
