"""Read neutral ``hullgeo-1`` geometry without a Rhino installation.

Surface coordinates are homogeneous, with their first three components in SI
metres. Knot vectors and trim coordinates retain their original parameter units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np


@dataclass
class NurbsSurface:
    order: tuple[int, int]
    cv: np.ndarray
    knots_u: np.ndarray
    knots_v: np.ndarray
    rational: bool = False

    def __post_init__(self) -> None:
        self.order = tuple(int(n) for n in self.order)
        self.cv = np.asarray(self.cv, dtype=float)
        self.knots_u = np.asarray(self.knots_u, dtype=float)
        self.knots_v = np.asarray(self.knots_v, dtype=float)
        if len(self.order) != 2 or self.cv.ndim != 3 or self.cv.shape[2] != 4:
            raise ValueError("NURBS requires two orders and homogeneous CVs shaped (nu, nv, 4)")
        if not np.all(np.isfinite(self.cv)):
            raise ValueError("NURBS control points must be finite")
        for axis, (order, knots) in enumerate(zip(self.order, (self.knots_u, self.knots_v))):
            count = self.cv.shape[axis]
            if order < 2 or count < order:
                raise ValueError("NURBS order must be >= 2 and <= the control-point count")
            if knots.ndim != 1 or len(knots) != count + order - 2:
                raise ValueError("Expected an OpenNURBS knot vector of length cv_count + order - 2")
            if not np.all(np.isfinite(knots)) or np.any(np.diff(knots) < 0):
                raise ValueError("NURBS knots must be finite and nondecreasing")
            if knots[order - 2] >= knots[count - 1]:
                raise ValueError("NURBS parameter domain must have positive length")

    @property
    def cv_count(self) -> tuple[int, int]:
        return self.cv.shape[:2]

    @property
    def domain_u(self) -> tuple[float, float]:
        return float(self.knots_u[self.order[0] - 2]), float(self.knots_u[self.cv_count[0] - 1])

    @property
    def domain_v(self) -> tuple[float, float]:
        return float(self.knots_v[self.order[1] - 2]), float(self.knots_v[self.cv_count[1] - 1])


@dataclass
class TrimLoop:
    type: str
    uv: np.ndarray

    def __post_init__(self) -> None:
        self.uv = np.asarray(self.uv, dtype=float)
        if self.type not in {"outer", "inner", "other"}:
            raise ValueError(f"Unknown trim-loop type: {self.type!r}")
        if self.uv.ndim != 2 or self.uv.shape[1] != 2 or len(self.uv) < 3:
            raise ValueError("A trim loop must contain at least three UV coordinates")
        if not np.all(np.isfinite(self.uv)):
            raise ValueError("Trim coordinates must be finite")


@dataclass
class Face:
    surface: NurbsSurface
    loops: tuple[TrimLoop, ...] = ()
    rev: bool = False
    object_index: int = 0
    face_index: int = 0
    layer: str = "HULL"
    object_name: str = ""
    nurbs_form_exact: bool = True


@dataclass
class Geometry:
    faces: tuple[Face, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


def load_hgeo(path: str | Path, layers: Iterable[str] | None = None) -> Geometry:
    """Load a dump and optionally select named layers (case-sensitive).

    Metadata includes the dump SHA-256, source units and original document
    metadata. A missing requested layer is an error, avoiding silent empty hulls.
    """
    path = Path(path)
    payload = path.read_bytes()
    raw = json.loads(payload)
    if raw.get("format") != "hullgeo-1":
        raise ValueError("Unsupported geometry format; expected 'hullgeo-1'")
    scale = float(raw.get("meters_per_unit", 0))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("meters_per_unit must be finite and positive")
    raw_faces = raw.get("faces", [])
    if raw.get("face_count", len(raw_faces)) != len(raw_faces):
        raise ValueError("Geometry face_count does not match its face records")
    selected = None if layers is None else ({layers} if isinstance(layers, str) else set(layers))
    available = {f["layer"] for f in raw_faces}
    if selected is not None and (not selected or selected - available):
        raise ValueError(f"Requested geometry layers unavailable: {sorted((selected - available) or selected)}")
    faces: list[Face] = []
    for record in raw_faces:
        if selected is not None and record["layer"] not in selected:
            continue
        ns = record["nurbs"]
        counts = tuple(int(n) for n in ns["cv_count"])
        cv = np.asarray(ns["cv"], dtype=float).reshape(*counts, 4).copy()
        cv[..., :3] *= scale  # homogeneous XYZ scales; the dimensionless weight does not
        surface = NurbsSurface(tuple(ns["order"]), cv, ns["knots_u"], ns["knots_v"], bool(ns["rational"]))
        faces.append(Face(
            surface=surface,
            loops=tuple(TrimLoop(loop["type"], loop["uv"]) for loop in record.get("loops", [])),
            rev=bool(record.get("rev", False)),
            object_index=int(record.get("object_index", 0)),
            face_index=int(record.get("face_index", 0)),
            layer=record["layer"], object_name=record.get("object_name", ""),
            nurbs_form_exact=bool(record.get("nurbs_form_exact", True)),
        ))
    if not faces:
        raise ValueError("Geometry contains no selected faces")
    metadata = {key: value for key, value in raw.items() if key != "faces"}
    metadata.update({"dump_path": str(path.resolve()), "dump_sha256": hashlib.sha256(payload).hexdigest(),
                     "coordinate_units": "m", "selected_layers": sorted({f.layer for f in faces}),
                     "selected_face_count": len(faces), "abs_tol_m": float(raw.get("abs_tol", 0)) * scale})
    return Geometry(tuple(faces), metadata)
