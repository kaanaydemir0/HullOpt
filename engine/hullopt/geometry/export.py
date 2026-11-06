"""Immutable neutral/mesh exports and a separately labelled native CAD morph.

The native route uses RhinoCommon SpaceMorph with PreserveStructure=False.
It approximates a pointwise longitudinal morph and is deliberately distinguished
from the Python NURBS-control-point map; its neutral dump must be remeasured.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import Iterable

import numpy as np

from .hgeo import Geometry, load_hgeo
from .nurbs import evaluate_surface
from .transform import LongitudinalMap


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exclusive_json(path: Path, payload: dict) -> None:
    # ASCII escapes also preserve Turkish paths across CPython -> IronPython 2
    # json.load, whose binary-stream decoding otherwise depends on its locale.
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encoded + b"\n")


def write_hgeo(geometry: Geometry, path: str | Path, *, units: str = "source") -> Path:
    """Write homogeneous CVs in original model units or metres, preserving UV.

    ``units='source'`` uses the input dump's meters_per_unit (default 1), while
    ``units='m'`` writes SI coordinates. The in-memory Geometry remains in SI.
    Existing output files are never replaced.
    """
    if units not in {"source", "m"}:
        raise ValueError("units must be 'source' or 'm'")
    if not geometry.faces:
        raise ValueError("Cannot export empty geometry")
    scale = float(geometry.metadata.get("meters_per_unit", 1.0)) if units == "source" else 1.0
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("meters_per_unit must be finite and positive")
    payload = deepcopy(geometry.metadata)
    for key in ("dump_path", "dump_sha256", "coordinate_units", "selected_layers", "selected_face_count", "abs_tol_m"):
        payload.pop(key, None)
    original_scale = float(geometry.metadata.get("meters_per_unit", 1.0))
    tolerance_m = float(geometry.metadata.get("abs_tol_m", float(geometry.metadata.get("abs_tol", 0.0)) * original_scale))
    payload.update({"format": "hullgeo-1", "meters_per_unit": scale,
                    "unit_system": geometry.metadata.get("unit_system", "meters") if units == "source" else "meters",
                    "abs_tol": tolerance_m / scale, "face_count": len(geometry.faces),
                    "faces": [], "warnings": deepcopy(geometry.metadata.get("warnings", 0))})
    payload.setdefault("source", "in-memory-geometry")
    payload.setdefault("document_strings", {})
    payload.setdefault("layers", {str(i): name for i, name in enumerate(sorted({face.layer for face in geometry.faces}))})
    payload["export_provenance"] = {"coordinate_units_in_memory": "m", "units_policy": units,
                                    "source_dump_sha256": geometry.metadata.get("dump_sha256")}
    for face in geometry.faces:
        surface = face.surface
        cv = surface.cv.copy()
        cv[..., :3] /= scale
        payload["faces"].append({
            "object_index": face.object_index, "object_name": face.object_name, "layer": face.layer,
            "face_index": face.face_index, "rev": int(face.rev), "nurbs_form_exact": int(face.nurbs_form_exact),
            "nurbs": {"rational": int(surface.rational), "order": list(surface.order),
                      "cv_count": list(surface.cv_count), "knots_u": surface.knots_u.tolist(),
                      "knots_v": surface.knots_v.tolist(), "cv": cv.reshape(-1, 4).tolist()},
            "loops": [{"type": loop.type, "uv": loop.uv.tolist()} for loop in face.loops],
        })
    result = Path(path)
    _exclusive_json(result, payload)
    return result


def write_binary_stl(mesh, path: str | Path, *, report_path: str | Path | None = None) -> Path:
    """Write outward-oriented binary STL; all vertex coordinates are metres."""
    triangles = np.asarray(getattr(mesh, "triangles", mesh), dtype=float)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not triangles.size:
        raise ValueError("Expected nonempty triangles shaped (n,3,3), in metres")
    if not np.isfinite(triangles).all() or len(triangles) > 0xFFFFFFFF:
        raise ValueError("STL triangles must be finite and count must fit uint32")
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    length = np.linalg.norm(normals, axis=1)
    input_count = len(triangles)
    nonzero = length > 0
    triangles, normals, length = triangles[nonzero], normals[nonzero], length[nonzero]
    if not len(triangles):
        raise ValueError("Cannot export an STL consisting only of zero-area triangles")
    normals /= length[:, None]
    records = np.zeros(len(triangles), dtype=[("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    with np.errstate(over="ignore"):
        records["normal"] = normals
        records["vertices"] = triangles
    if not np.isfinite(records["vertices"]).all():
        raise ValueError("STL coordinates exceed float32 range")
    result = Path(path)
    if report_path is not None and Path(report_path).exists():
        raise FileExistsError(f"STL report already exists: {report_path}")
    result.parent.mkdir(parents=True, exist_ok=True)
    with result.open("xb") as stream:
        header = f"HULLOPT SI m; input={input_count}; written={len(triangles)}; zero_skip={input_count-len(triangles)}"
        stream.write(header.encode("ascii")[:80].ljust(80, b" "))
        stream.write(struct.pack("<I", len(triangles)))
        stream.write(records.tobytes())
    if report_path is not None:
        _exclusive_json(Path(report_path), {"format": "binary-STL", "units": "m", "input_triangle_count": input_count,
                                           "written_triangle_count": len(triangles),
                                           "skipped_zero_area_triangle_count": input_count - len(triangles),
                                           "sha256": _sha256(result)})
    return result


def compare_surface_samples(first: Geometry, second: Geometry, *, grid_count: int = 11) -> dict:
    """Compare corresponding underlying NURBS at common UVs; not a hull-distance proof."""
    if grid_count < 3:
        raise ValueError("grid_count must be at least three")
    key = lambda face: (face.object_index, face.face_index, face.layer)
    reference = {key(face): face for face in first.faces}
    errors = []
    face_rows = []
    for other in second.faces:
        if key(other) not in reference:
            raise ValueError("Native export and Python candidate face identities differ")
        original = reference[key(other)]
        for domain in ("domain_u", "domain_v"):
            if not np.allclose(getattr(original.surface, domain), getattr(other.surface, domain), rtol=1e-12, atol=1e-10):
                raise ValueError("Native export changed surface parameter domains; direct UV comparison is invalid")
        u = np.linspace(*original.surface.domain_u, grid_count)
        v = np.linspace(*original.surface.domain_v, grid_count)
        a = evaluate_surface(original.surface, u, v, grid=True)
        b = evaluate_surface(other.surface, u, v, grid=True)
        distance = np.linalg.norm(a - b, axis=-1).ravel()
        errors.extend(distance.tolist())
        face_rows.append({"object_index": other.object_index, "face_index": other.face_index,
                          "layer": other.layer, "max_distance_m": float(distance.max())})
    if not errors:
        raise ValueError("No corresponding surfaces to compare")
    values = np.asarray(errors)
    return {"method": "common-UV underlying-surface sampling; includes points outside trims",
            "grid_count_per_direction": grid_count, "sample_count": len(values),
            "max_distance_m": float(values.max()), "rms_distance_m": float(np.sqrt(np.mean(values**2))),
            "face_maxima": face_rows,
            "warning": "Pointwise native morph and mapped NURBS control points are different representations; remeasure native geometry independently."}


def export_3dm(source_path: str | Path, dest_path: str | Path, xmap: LongitudinalMap,
               layers: Iterable[str] = ("HULL",), *, expected_source_sha256: str | None = None,
               tolerance_m: float = 0.0005, cv_geometry: Geometry | None = None,
               host_path: str | Path | None = None, timeout_s: float = 300.0) -> dict:
    """Export a selected-layer native point morph using the Rhino 7 kernel.

    Requires Rhino 7 and the project's NativePythonHost.exe on Windows. Current
    sandbox sessions may require running the invoking Python process with host
    access; initialization failure is reported, never treated as working export.
    Creates .3dm, .hgeo.json, .export.json, with no overwrite. A successful CAD
    validity report does not certify target Cp/LCB/volume or inter-object joins.
    """
    source = Path(source_path).resolve(strict=True)
    destination = Path(dest_path).resolve()
    if source == destination or destination.suffix.lower() != ".3dm":
        raise ValueError("Destination must be a new .3dm path distinct from the source")
    if not np.isfinite(tolerance_m) or tolerance_m <= 0 or not np.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Native tolerance and timeout must be finite and positive")
    selected = sorted({layers} if isinstance(layers, str) else set(layers))
    if not selected:
        raise ValueError("Native export requires selected layers")
    mapping = xmap.to_dict()
    # Revalidate serialized parameters; subclasses may provide arbitrary maps.
    validated = LongitudinalMap(**{name: mapping[name] for name in (
        "x_aft_m", "x_mid_m", "x_fwd_m", "aft_shift_m", "fwd_shift_m")})
    mapping = validated.to_dict()
    native_dump = destination.with_suffix(".hgeo.json")
    report_path = destination.with_suffix(".export.json")
    for path in (destination, native_dump, report_path):
        if path.exists():
            raise FileExistsError(f"Native export output already exists: {path}")
    digest = _sha256(source)
    if expected_source_sha256 is not None and digest != expected_source_sha256.lower():
        raise ValueError("Source .3dm SHA-256 mismatch")
    project = Path(__file__).resolve().parents[3]
    host = Path(host_path) if host_path is not None else project / "04_TEKNIK_ARACLAR" / "kernel_probe" / "NativePythonHost.exe"
    helper = Path(__file__).resolve().parents[2] / "tools" / "rhino_export_candidate.py"
    if not host.is_file() or not helper.is_file():
        raise FileNotFoundError("Native Rhino export host/helper is inaccessible in this execution environment")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hullopt-native-", dir=destination.parent) as temporary:
        request_path = Path(temporary) / "request.json"
        response_path = Path(temporary) / "response.json"
        request = {"source": str(source), "destination": str(destination), "dump": str(native_dump),
                   "response": str(response_path), "source_sha256": digest,
                   "xmap": mapping, "layers": selected, "tolerance_m": float(tolerance_m),
                   "temporary_3dm": str(Path(temporary) / "candidate.3dm")}
        _exclusive_json(request_path, request)
        try:
            process = subprocess.run([str(host), str(helper), str(request_path)], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
                                     check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if response_path.is_file():
                report = json.loads(response_path.read_text(encoding="utf-8-sig"))
            else:
                report = {"status": "failed", "error": "Native host produced no response (initialization or process failure)"}
            report.update({"native_returncode": process.returncode, "native_stdout": process.stdout[-8000:],
                           "native_stderr": process.stderr[-8000:], "report_path": str(report_path)})
            if process.returncode != 0:
                report["status"] = "failed"
        except subprocess.TimeoutExpired as exc:
            report = {"status": "failed", "error": f"Native export exceeded {timeout_s:g} seconds", "report_path": str(report_path)}
        report["source_sha256_after_python"] = _sha256(source)
        if report["source_sha256_after_python"] != digest:
            report.update(status="failed", error="Source changed during export")
        if report.get("status") == "succeeded":
            if not destination.is_file() or not native_dump.is_file():
                report.update(status="failed", error="Native output or neutral dump is missing")
            else:
                native_geometry = load_hgeo(native_dump, layers=selected)
                if cv_geometry is not None:
                    report["native_vs_python_cv"] = compare_surface_samples(cv_geometry, native_geometry)
                report["artifact_sha256"] = {destination.name: _sha256(destination), native_dump.name: _sha256(native_dump)}
                report["native_geometry_path"] = str(native_dump)
                report["hgeo_path"] = str(native_dump)
                report["cad_path"] = str(destination)
                report["requires_native_hydrostatic_remeasurement"] = True
        _exclusive_json(report_path, report)
        if report.get("status") != "succeeded":
            raise RuntimeError(f"Native CAD export failed: {report.get('error', 'see report')}; {report_path}")
        return report
