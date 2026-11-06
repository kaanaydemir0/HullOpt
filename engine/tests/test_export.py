"""Data-preserving exports, SI STL, and source/output protection."""

import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from hullopt.geometry import load_hgeo, TriangleMesh
from hullopt.geometry.export import write_hgeo, write_binary_stl, export_3dm, compare_surface_samples
from hullopt.geometry.transform import LongitudinalMap, transform_geometry
try:
    from .hydro_fixtures import box_hgeo, geometry_from_hgeo, box_mesh
except ImportError:
    from hydro_fixtures import box_hgeo, geometry_from_hgeo, box_mesh


class ExportTests(unittest.TestCase):
    def test_hgeo_roundtrip_preserves_units_cv_trims_and_integer_warning(self):
        geometry = geometry_from_hgeo(box_hgeo(meters_per_unit=.001))
        changed = transform_geometry(geometry, LongitudinalMap(0, 5, 10, -.1, .1))
        with tempfile.TemporaryDirectory() as temporary:
            for units in ("source", "m"):
                path = Path(temporary) / (units + ".hgeo.json")
                write_hgeo(changed, path, units=units)
                actual = load_hgeo(path)
                self.assertEqual(actual.metadata["warnings"], [])
                for before, after in zip(changed.faces, actual.faces):
                    np.testing.assert_allclose(after.surface.cv, before.surface.cv, atol=1e-12, rtol=0)
                    np.testing.assert_array_equal(after.loops[0].uv, before.loops[0].uv)
                    self.assertEqual(after.rev, before.rev)
                with self.assertRaises(FileExistsError):
                    write_hgeo(changed, path)
            geometry.metadata["warnings"] = 0
            path = Path(temporary) / "integer.hgeo.json"
            write_hgeo(geometry, path)
            self.assertEqual(load_hgeo(path).metadata["warnings"], 0)

    def test_unicode_paths_are_ascii_escaped_for_native_bridge(self):
        geometry = geometry_from_hgeo(box_hgeo())
        geometry.metadata["source"] = "Tasarım/ölçüm.3dm"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ölçüm.json"
            write_hgeo(geometry, path)
            raw = path.read_bytes()
            self.assertTrue(raw.isascii())
            self.assertEqual(json.loads(raw)["source"], "Tasarım/ölçüm.3dm")

    def test_binary_stl_units_count_normals_and_zero_skip(self):
        original = box_mesh().triangles
        mesh = TriangleMesh(np.concatenate((original, np.zeros((1, 3, 3)))))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hull_m.stl"
            report = Path(temporary) / "stl.json"
            write_binary_stl(mesh, path, report_path=report)
            raw = path.read_bytes()
            count = struct.unpack("<I", raw[80:84])[0]
            self.assertEqual(count, len(original))
            self.assertEqual(len(raw), 84 + 50 * count)
            first = np.frombuffer(raw[84:132], dtype="<f4")
            np.testing.assert_allclose(first[3:].reshape(3, 3), original[0], atol=1e-7)
            self.assertAlmostEqual(np.linalg.norm(first[:3]), 1, delta=1e-7)
            self.assertEqual(json.loads(report.read_text())["skipped_zero_area_triangle_count"], 1)
            with self.assertRaises(FileExistsError):
                write_binary_stl(mesh, path)

    def test_native_export_protects_source_and_wrong_checksum_before_launch(self):
        mapping = LongitudinalMap(0, 5, 10)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.3dm"
            source.write_bytes(b"immutable test source")
            with self.assertRaises(ValueError):
                export_3dm(source, source, mapping)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export_3dm(source, Path(temporary) / "new.3dm", mapping, expected_source_sha256="0" * 64)
            self.assertEqual(source.read_bytes(), b"immutable test source")

    def test_sample_comparison_identity(self):
        geometry = geometry_from_hgeo(box_hgeo())
        report = compare_surface_samples(geometry, geometry)
        self.assertEqual(report["max_distance_m"], 0)
        self.assertGreater(report["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
