"""Mode frame derived from a hull's own dimensions.

``derive_frame`` measures the hull at a reference waterline (waterline ends, largest section, keel,
draft, largest half-beam, upper edge) and places every mode with ``FrameRules``: lengths as fractions
of LWL, heights as fractions of the draft T. The top curve is a natural cubic spline kept below the
measured upper edge (sheer, deck steps) by a margin, so the six section-shape modes never move the
upper edge; the two Lackenby modes shift whole sections in x, and the upper-edge gate limits that.

The default rules come from a hand-tuned frame of the first test hull, expressed relative to that
hull and rounded. They are starting values; check them on each hull.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..hydro.hydrostatics import measure
from ..hydro.sections import plane_segments
from .shape_modes_v2 import ModeFrameV2


@dataclass(frozen=True)
class FrameRules:
    bow_start: float = 0.43         # bow_fine acts forward of x_aft + bow_start * LWL
    tip_start: float = 0.76         # bow_tip acts forward of x_aft + tip_start * LWL
    stern_end: float = 0.39         # stern modes act aft of x_aft + stern_end * LWL
    bow_full_depth: float = 0.10    # bow modes: full effect below WL - bow_full_depth * T
    stern_full_depth: float = 0.67  # stern_narrow: full effect below WL - stern_full_depth * T
    lift_full_height: float = 0.55  # stern_lift: full effect below keel + lift_full_height * T
    mid_start: float = 0.035        # midbody window rises from x_aft + mid_start * LWL
    mid_end: float = 0.86           # and falls to zero at x_aft + mid_end * LWL
    mid_ramp: float = 0.235         # length of each midbody ramp / LWL
    mid_wl_gap: float = 0.024       # midbody modes vanish at WL - mid_wl_gap * T (BWL unchanged)
    deadrise_core: float = 0.023    # smooth keel core / largest half-beam
    top_margin: float = 0.03        # top curve at least top_margin * T below the upper edge
    top_window: float = 0.04        # running-minimum window / hull length (steps in the upper edge)
    top_knots: int = 121            # spline knots along the hull
    edge_stations: int = 961        # upper-edge samples along the hull

    def __post_init__(self):
        if not 0 < self.stern_end < self.bow_start < self.tip_start < 1:
            raise ValueError("need 0 < stern_end < bow_start < tip_start < 1 (fractions of LWL)")
        if not 0 <= self.mid_start < self.mid_end <= 1.2 or self.mid_ramp <= 0:
            raise ValueError("invalid midbody window")
        if min(self.bow_full_depth, self.stern_full_depth, self.lift_full_height, self.mid_wl_gap,
               self.deadrise_core, self.top_margin, self.top_window) < 0:
            raise ValueError("frame rules must be nonnegative")
        if self.top_knots < 4 or self.edge_stations < 2 * self.top_knots:
            raise ValueError("need at least 4 knots and twice as many edge stations")
        if not 0 < self.top_window < 0.5:
            raise ValueError("top_window must be a fraction of the hull length below 0.5")


def upper_edge(triangles, xs):
    """Highest point of the hull skin in each x section (nan where the plane misses the skin).

    Every triangle edge that crosses a station contributes its crossing height; edges lying in a
    station plane contribute their higher end. This equals the top of the plane cut at each station.
    """
    tri = np.asarray(triangles, float)
    xs = np.asarray(xs, float)
    a = tri.reshape(-1, 3)
    b = tri[:, [1, 2, 0]].reshape(-1, 3)
    lo, hi = np.minimum(a[:, 0], b[:, 0]), np.maximum(a[:, 0], b[:, 0])
    first = np.searchsorted(xs, lo, side="left")
    count = np.searchsorted(xs, hi, side="right") - first
    keep = count > 0
    edges = np.flatnonzero(keep)
    count = count[keep]
    total = int(count.sum())
    out = np.full(len(xs), -np.inf)
    if total:
        edge = np.repeat(edges, count)
        station = np.repeat(first[keep], count) + (np.arange(total) - np.repeat(np.cumsum(count) - count, count))
        xa, xb, za, zb = a[edge, 0], b[edge, 0], a[edge, 2], b[edge, 2]
        run = xb - xa
        flat = run == 0
        t = (xs[station] - xa) / np.where(flat, 1.0, run)
        z = np.where(flat, np.maximum(za, zb), za + t * (zb - za))
        np.maximum.at(out, station, z)
    out[~np.isfinite(out)] = np.nan
    return out


def _top_curve(triangles, x_min, x_max, draft, rules):
    """Knots and values of a C2 top curve at least the margin below the measured upper edge.

    The edge minus the margin is eroded (running minimum over the window), which moves each step of
    the edge onto its high side, then smoothed with a Gaussian whose support equals the window, so the
    curve rises after a deck step without dipping before it. A final shift keeps it below the edge.
    """
    from scipy.interpolate import CubicSpline
    from scipy.ndimage import gaussian_filter1d, minimum_filter1d
    length = x_max - x_min
    xs = np.linspace(x_min + 1e-3 * length, x_max - 1e-3 * length, rules.edge_stations)
    edge = upper_edge(triangles, xs)
    ok = np.isfinite(edge)
    if ok.sum() < 3:
        raise ValueError("could not measure the hull's upper edge")
    edge = np.interp(xs, xs[ok], edge[ok])
    limit = edge - rules.top_margin * draft
    half = max(1, int(round(rules.top_window * length / (xs[1] - xs[0]))))
    # Gaussian support (4 sigma) equals the erosion window, so nothing leaks to the low side of a step.
    smooth = gaussian_filter1d(minimum_filter1d(limit, 2 * half + 1, mode="nearest"), half / 4.0,
                               mode="nearest", truncate=4.0)
    knots = np.linspace(x_min, x_max, rules.top_knots)
    values = np.interp(knots, xs, smooth)
    excess = float(np.max(CubicSpline(knots, values, bc_type="natural")(xs) - limit))
    if excess > 0:
        values = values - excess
    return knots, values, edge, xs


def derive_frame(mesh, z_wl, rules: FrameRules = FrameRules(), hydrostatics=None) -> ModeFrameV2:
    """Mode frame of a hull floating at z_wl (zero trim), from its own dimensions."""
    h = hydrostatics if hydrostatics is not None else measure(mesh, z_wl, include_sections=True)
    triangles = mesh.triangles
    points = triangles.reshape(-1, 3)
    lo, hi = points.min(axis=0), points.max(axis=0)
    lwl, draft, keel = h.lwl_m, h.draft_m, h.keel_z_m
    x_aft, x_fwd = h.x_aft_m, h.x_fwd_m
    if h.section_x_m is None or not len(h.section_x_m):
        raise ValueError("hydrostatics must include sections")
    x_mid = float(h.section_x_m[int(np.argmax(h.section_area_m2))])
    y_ref = float(max(abs(lo[1]), abs(hi[1])))
    knots, values, _, _ = _top_curve(triangles, float(lo[0]), float(hi[0]), draft, rules)
    return ModeFrameV2(
        x_aft=float(x_aft), x_mid=x_mid, x_fwd=float(x_fwd), z_wl=float(z_wl),
        x_min=float(lo[0]), x_max=float(hi[0]),
        x_bow0=float(x_aft + rules.bow_start * lwl), x_tip0=float(x_aft + rules.tip_start * lwl),
        x_stern0=float(x_aft + rules.stern_end * lwl),
        dz_bow_lo=float(-rules.bow_full_depth * draft), dz_stern_lo=float(-rules.stern_full_depth * draft),
        z_lift_lo=float(keel + rules.lift_full_height * draft),
        x_mid_a=float(x_aft + rules.mid_start * lwl), x_mid_b=float(x_aft + rules.mid_end * lwl),
        mid_ramp=float(rules.mid_ramp * lwl), z_keel=float(keel), mid_wl_gap=float(rules.mid_wl_gap * draft),
        y_ref=y_ref, deadrise_core_m=float(rules.deadrise_core * y_ref),
        top_x=tuple(float(x) for x in knots), top_z=tuple(float(z) for z in values))


def check_frame(frame: ModeFrameV2, mesh) -> list[str]:
    """Plain warnings about a frame on this hull (empty when nothing looks wrong)."""
    notes = []
    triangles = mesh.triangles
    lo, hi = triangles.reshape(-1, 3).min(axis=0), triangles.reshape(-1, 3).max(axis=0)
    xs = np.linspace(lo[0] + 1e-3 * (hi[0] - lo[0]), hi[0] - 1e-3 * (hi[0] - lo[0]), 121)
    edge = upper_edge(triangles, xs)
    ok = np.isfinite(edge)
    top = frame.z_top(xs)
    if ok.any() and np.any(top[ok] > edge[ok] + 1e-9):
        worst = xs[ok][int(np.argmax(top[ok] - edge[ok]))]
        notes.append(f"The top curve rises above the hull's upper edge near x = {worst:.3f} m; "
                     "the upper edge would move there.")
    draft = frame.z_wl - frame.z_keel
    for name, z_lo, x_from, x_to in (("bow modes", frame.z_wl + frame.dz_bow_lo, frame.x_bow0, frame.x_max),
                                     ("stern_narrow", frame.z_wl + frame.dz_stern_lo, frame.x_min, frame.x_stern0),
                                     ("stern_lift", frame.z_lift_lo, frame.x_min, frame.x_stern0)):
        inside = (xs >= x_from) & (xs <= x_to)
        if inside.any():
            band = float(np.min(top[inside] - z_lo))
            if band < 0.1 * draft:
                notes.append(f"{name}: the fade-out band above the full-effect height is only {band:.3f} m; "
                             "the mode will be steep there.")
    if frame.x_stern0 >= frame.x_bow0:
        notes.append("Stern modes reach forward of where the bow modes start.")
    return notes
