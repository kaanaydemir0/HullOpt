"""Smooth (C2) control-point deformation modes, version 2.

Eight modes bend a baseline hull without redrawing it. The rules (from the fairness fix on
the first test hull):
  * every weight is C2: quintic smootherstep S5 or the C2 bump 64 t^3 (1-t)^3, clamped only where the
    function is flat to second order (so a clamp never creates a kink);
  * vertical decays run all the way to a smooth "top" curve z_top(x) that lies just below the hull's
    upper edge (sheer, deck steps), so the six section-shape modes never move the upper edge and the
    transition uses the whole freeboard; the two Lackenby modes (lack_aft, lack_fwd) shift whole
    sections in x, upper edge included, and the upper-edge gate limits that motion;
  * x weights reach 1 with zero slope and curvature and stay constant beyond (stem overhang, transom).

Where each mode acts is held in a ``ModeFrameV2``. The frame has no built-in hull: derive it from the
hull's own dimensions with ``hullopt.optimize.mode_frame.derive_frame`` or give every value explicitly
(a study file does this to reproduce an earlier study exactly).

Displacement d(p) is applied to Cartesian CV positions (weights, knots, trims unchanged), so evaluated
surface points are linear in the amplitudes. Axes: x forward, y to starboard, z up, metres.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict, fields
from functools import lru_cache

import numpy as np

from ..geometry.hgeo import Geometry


def s5(t):
    """Quintic smootherstep: 0 -> 1, first and second derivatives zero at both ends (C2 when clamped)."""
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def bump3(t):
    """C2 bump, 0 outside (0,1), peak 1 at t=0.5."""
    t = np.clip(t, 0.0, 1.0)
    return 64.0 * t ** 3 * (1.0 - t) ** 3


@lru_cache(maxsize=32)
def _top_spline(xs: tuple, zs: tuple):
    from scipy.interpolate import CubicSpline
    return CubicSpline(np.asarray(xs, float), np.asarray(zs, float), bc_type="natural")


@dataclass(frozen=True)
class ModeFrameV2:
    """Where the modes act; lengths in metres, model axes.

    The top curve z_top(x) is either a natural cubic spline through (top_x, top_z), which is what
    ``derive_frame`` produces, or the explicit form used in the first test hull's study: a level aft part, an S5 blend,
    then a polynomial in u = (x - sheer_xc) / sheer_xs.
    """
    x_aft: float                 # aft end of the waterline
    x_mid: float                 # station of the largest section
    x_fwd: float                 # forward end of the waterline
    z_wl: float                  # reference waterline
    x_min: float                 # hull x range (transom plane)
    x_max: float                 # hull x range (stem top including overhang)
    x_bow0: float                # bow_fine starts here
    x_tip0: float                # bow_tip starts here
    x_stern0: float              # stern modes start here and act aft of it
    dz_bow_lo: float             # bow modes: full effect below z_wl + this, decays to 0 at z_top
    dz_stern_lo: float           # stern_narrow: full effect below z_wl + this
    z_lift_lo: float             # stern_lift: full lift below this z, decays to 0 at z_top
    x_mid_a: float               # midbody window rises over [x_mid_a, x_mid_a + mid_ramp]
    x_mid_b: float               #                falls over [x_mid_b - mid_ramp, x_mid_b]
    mid_ramp: float
    z_keel: float                # bilge/deadrise modes: full effect at keel, 0 at z_wl - mid_wl_gap
    mid_wl_gap: float
    y_ref: float                 # half-beam used to normalise deadrise
    deadrise_core_m: float       # smooth symmetric keel core; abs(y) has a cusp at y=0
    top_x: tuple = ()            # spline top curve
    top_z: tuple = ()
    z_top_aft: float | None = None   # explicit top curve (first test hull's study)
    x_top_b0: float | None = None
    x_top_b1: float | None = None
    sheer_xc: float | None = None
    sheer_xs: float | None = None
    sheer_c: tuple = ()              # np.polyval order

    def __post_init__(self):
        if self.top_x:
            if len(self.top_x) != len(self.top_z) or len(self.top_x) < 3:
                raise ValueError("top curve needs matching top_x/top_z with at least three points")
            if np.any(np.diff(np.asarray(self.top_x, float)) <= 0):
                raise ValueError("top_x must increase")
        elif not self.sheer_c or None in (self.z_top_aft, self.x_top_b0, self.x_top_b1,
                                          self.sheer_xc, self.sheer_xs):
            raise ValueError("mode frame needs a top curve: top_x/top_z or the explicit sheer form")
        values = [getattr(self, f.name) for f in fields(self)
                  if f.name not in ("top_x", "top_z", "sheer_c") and getattr(self, f.name) is not None]
        values += list(self.top_x) + list(self.top_z) + list(self.sheer_c)
        if not np.all(np.isfinite(np.asarray(values, float))):
            raise ValueError("mode frame values must be finite")
        if not (self.x_min < self.x_stern0 and self.x_bow0 < self.x_max and self.x_tip0 < self.x_max):
            raise ValueError("mode starts must lie inside the hull's x range")
        if self.mid_ramp <= 0 or self.y_ref <= 0 or self.deadrise_core_m < 0:
            raise ValueError("mid_ramp and y_ref must be positive, deadrise_core_m nonnegative")
        if not self.z_keel < self.z_wl - self.mid_wl_gap:
            raise ValueError("z_keel must lie below z_wl - mid_wl_gap")

    def z_top(self, x):
        if self.top_x:
            return _top_spline(tuple(self.top_x), tuple(self.top_z))(np.asarray(x, float))
        u = (np.asarray(x, float) - self.sheer_xc) / self.sheer_xs
        fwd = np.polyval(np.asarray(self.sheer_c), u)
        b = s5((np.asarray(x, float) - self.x_top_b0) / (self.x_top_b1 - self.x_top_b0))
        return self.z_top_aft + b * (fwd - self.z_top_aft)

    def decay(self, x, z, z_lo):
        """1 below z_lo, C2 decay to 0 at z_top(x)."""
        zt = self.z_top(x)
        return 1.0 - s5((z - z_lo) / np.maximum(zt - z_lo, 1e-3))

    def mid_window(self, x):
        return s5((x - self.x_mid_a) / self.mid_ramp) * s5((self.x_mid_b - x) / self.mid_ramp)

    def low_weight(self, z):
        """1 at the keel, C2 decay to 0 just below the waterline (BWL unchanged)."""
        return 1.0 - s5((z - self.z_keel) / (self.z_wl - self.mid_wl_gap - self.z_keel))

    def to_dict(self) -> dict:
        """JSON-ready values; unused top-curve fields are left out."""
        out = {}
        for key, value in asdict(self).items():
            if value is None or value == ():
                continue
            out[key] = list(value) if isinstance(value, tuple) else float(value)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "ModeFrameV2":
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError("unknown mode frame keys: " + ", ".join(unknown))
        values = {k: (tuple(float(x) for x in v) if isinstance(v, (list, tuple)) else float(v))
                  for k, v in data.items()}
        return cls(**values)


MODE_NAMES_V2 = ("lack_aft", "lack_fwd", "bow_fine", "bow_tip", "stern_narrow", "stern_lift",
                 "bilge_full", "deadrise")
MODE_INFO_V2 = {
    "lack_aft": "m; +ve moves aft-half sections forward: dx = a*64 xi^3(1-xi)^3 between x_aft and x_mid",
    "lack_fwd": "m; +ve moves forward-half sections forward, between x_mid and x_fwd",
    "bow_fine": "fraction; +ve narrows the forebody forward of x_bow0: dy = -a*y*S5_x*decay(z: z_wl+dz_bow_lo -> z_top)",
    "bow_tip": "fraction; as bow_fine, forward of x_tip0",
    "stern_narrow": "fraction; +ve narrows the afterbody aft of x_stern0 below z_wl+dz_stern_lo, decaying to z_top",
    "stern_lift": "m; +ve lifts the aft bottom aft of x_stern0 below z_lift_lo, decaying to z_top",
    "bilge_full": "fraction; +ve fills midbody sections outward, full at z_keel, 0 at z_wl-mid_wl_gap (BWL unchanged)",
    "deadrise": "m; +ve lowers the midbody bottom in proportion to y/y_ref (flatter floor), 0 at z_wl-mid_wl_gap",
}
MODE_UNITS_V2 = {"lack_aft": "m", "lack_fwd": "m", "bow_fine": "fraction", "bow_tip": "fraction",
                 "stern_narrow": "fraction", "stern_lift": "m", "bilge_full": "fraction", "deadrise": "m"}


def mode_displacement_v2(name: str, p: np.ndarray, f: ModeFrameV2) -> np.ndarray:
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    d = np.zeros_like(p, dtype=float)
    if name in ("lack_aft", "lack_fwd"):
        a, b = (f.x_aft, f.x_mid) if name == "lack_aft" else (f.x_mid, f.x_fwd)
        d[..., 0] = bump3((x - a) / (b - a))
    elif name in ("bow_fine", "bow_tip"):
        x0 = f.x_bow0 if name == "bow_fine" else f.x_tip0
        d[..., 1] = -y * s5((x - x0) / (f.x_max - x0)) * f.decay(x, z, f.z_wl + f.dz_bow_lo)
    elif name == "stern_narrow":
        d[..., 1] = -y * s5((f.x_stern0 - x) / (f.x_stern0 - f.x_min)) * f.decay(x, z, f.z_wl + f.dz_stern_lo)
    elif name == "stern_lift":
        d[..., 2] = s5((f.x_stern0 - x) / (f.x_stern0 - f.x_min)) * f.decay(x, z, f.z_lift_lo)
    elif name == "bilge_full":
        d[..., 1] = y * f.mid_window(x) * f.low_weight(z)
    elif name == "deadrise":
        radial = np.sqrt(y*y+f.deadrise_core_m**2)-f.deadrise_core_m
        d[..., 2] = -(radial / f.y_ref) * f.mid_window(x) * f.low_weight(z)
    else:
        raise KeyError(name)
    return d


def displacement_v2(p, f: ModeFrameV2, amplitudes: dict) -> np.ndarray:
    d = np.zeros_like(p, dtype=float)
    for k, a in amplitudes.items():
        if a:
            d += float(a) * mode_displacement_v2(k, p, f)
    return d


def apply_modes_v2(geometry: Geometry, frame: ModeFrameV2, amplitudes: dict, layers=("HULL",)) -> Geometry:
    """New Geometry with the v2 field applied to the CVs of the selected layers (CVs in metres)."""
    if any(k in geometry.metadata for k in ("shape_modes", "shape_modes_v2", "longitudinal_transform")):
        raise ValueError("Generate candidates from the untransformed baseline only")
    if geometry.metadata.get("coordinate_units", "m") != "m":
        raise ValueError("expected CVs in metres (load_hgeo output)")
    result = deepcopy(geometry)
    active = {k: float(v) for k, v in amplitudes.items() if float(v) != 0.0}
    for face in result.faces:
        if face.layer not in layers:
            continue
        cv = face.surface.cv
        w = cv[..., 3:4]
        p = cv[..., :3] / w
        cv[..., :3] = (p + displacement_v2(p, frame, active)) * w
    result.metadata["shape_modes_v2"] = {"frame": asdict(frame), "amplitudes": active, "info": MODE_INFO_V2}
    return result


def frame_params_text(frame: ModeFrameV2, amplitudes: dict) -> str:
    """key value lines (frame, then amplitudes) for external morph tools."""
    lines = []
    for k, v in asdict(frame).items():
        if v is None:
            continue
        if isinstance(v, tuple):
            stem = {"sheer_c": "sheer", "top_x": "top_x", "top_z": "top_z"}[k]
            if v:
                lines.append(f"{stem}_n {len(v)}")
            for i, c in enumerate(v):
                lines.append(f"{k}{i} {float(c)!r}")
        else:
            lines.append(f"{k} {float(v)!r}")
    for k in MODE_NAMES_V2:
        lines.append(f"{k} {float(amplitudes.get(k, 0.0))!r}")
    return "\n".join(lines) + "\n"
