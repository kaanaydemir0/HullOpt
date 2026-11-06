"""Exact divergence integrals over piecewise planar, waterline-clipped skin.

Required: outward oriented surface, closed below the level waterline. An open
deck above WL is allowed. This computes mesh hydrostatics, not CAD accuracy;
the latter is controlled by tessellation convergence and reference tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .sections import plane_segments, sectional_areas, triangles_array


@dataclass
class Hydrostatics:
    z_wl_m: float
    volume_m3: float
    displacement_kg: float
    waterplane_area_m2: float
    lwl_m: float
    bwl_m: float
    draft_m: float
    lcb_m: float
    kb_m: float
    lcf_m: float
    wetted_area_m2: float
    transom_area_m2: float
    transom_wetted_area_m2: float
    max_section_area_m2: float | None
    cb: float
    cp: float | None
    cx: float | None
    cwp: float
    lcb_percent_lwl: float
    entrance_half_angle_deg: float | None
    x_aft_m: float
    x_fwd_m: float
    keel_z_m: float
    kb_above_keel_m: float
    section_x_m: np.ndarray = field(default_factory=lambda: np.empty(0))
    section_area_m2: np.ndarray = field(default_factory=lambda: np.empty(0))
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        values = asdict(self)
        values["section_x_m"] = self.section_x_m.tolist()
        values["section_area_m2"] = self.section_area_m2.tolist()
        values["definitions"] = {
            "coordinates": "metres; +x forward, +z upward; KB is absolute model z",
            "draft": "WL minus lowest immersed vertex of selected layers",
            "wetted_area": "includes immersed transom; transom_wetted_area_m2 is separately reported",
            "transom": "heuristic: outward unit normal nx < -0.7 and centroid x < aft WL + 2 m; projected area abs(Nx)",
            "lcb_percent_lwl": "100*(LCB - midpoint of LWL)/LWL, positive forward",
            "entrance_half_angle": "straight-line fit to the exact waterline half-breadth at 41 stations over the forward 10% LWL",
            "precondition": "outward oriented surface closed below WL; no automatic topology repair",
        }
        return values


def clip_triangles_below(mesh, z_wl_m: float) -> np.ndarray:
    """Clip while preserving vertex order; exclude facets coplanar with WL."""
    tri = triangles_array(mesh)
    if not np.isfinite(z_wl_m):
        raise ValueError("Waterline must be finite (metres).")
    below = tri[:, :, 2] < z_wl_m
    counts = below.sum(axis=1)
    chunks = [tri[counts == 3]]
    for count in (1, 2):
        selected = tri[counts == count]
        if not len(selected):
            continue
        # Rotate each triangle so the lone inside/outside vertex is first.
        flags = below[counts == count]
        special = (flags if count == 1 else ~flags).argmax(axis=1)
        order = (special[:, None] + np.arange(3)) % 3
        rotated = selected[np.arange(len(selected))[:, None], order]
        a, b, c = rotated[:, 0], rotated[:, 1], rotated[:, 2]
        ab = a + ((z_wl_m - a[:, 2]) / (b[:, 2] - a[:, 2]))[:, None] * (b - a)
        ac = a + ((z_wl_m - a[:, 2]) / (c[:, 2] - a[:, 2]))[:, None] * (c - a)
        if count == 1:
            chunks.append(np.stack((a, ab, ac), axis=1))
        else:
            chunks.append(np.stack((ab, b, c), axis=1))
            chunks.append(np.stack((ab, c, ac), axis=1))
    return np.concatenate(chunks, axis=0)


def waterplane_bounds(triangles, z_wl_m):
    # A scale-aware inward offset exceeds roundoff in NURBS endpoints at z=0
    # (nextafter(0) alone is only a subnormal) while remaining negligible in SI.
    scale = max(float(np.ptp(triangles[:, :, 2])), abs(float(z_wl_m)), 1.0)
    coordinate = float(z_wl_m) - 64 * np.finfo(float).eps * scale
    segments, _ = plane_segments(triangles, coordinate, 2)
    if not len(segments):
        raise ValueError("No waterplane intersection: choose a surface-piercing waterline.")
    points = segments.reshape(-1, 3)
    return points, float(points[:, 0].min()), float(points[:, 0].max()), float(np.ptp(points[:, 1]))


ENTRANCE_FIT_FRACTION_LWL = 0.10
ENTRANCE_FIT_STATIONS = 41


def _waterline_half_breadth(segments, x):
    """Half of the waterline breadth at x from the exact waterline segments."""
    x0, x1 = segments[:, 0, 0], segments[:, 1, 0]
    crossing = (np.minimum(x0, x1) <= x) & (np.maximum(x0, x1) >= x) & (x0 != x1)
    if not crossing.any():
        return None
    s = segments[crossing]
    t = (x - s[:, 0, 0]) / (s[:, 1, 0] - s[:, 0, 0])
    y = s[:, 0, 1] + t * (s[:, 1, 1] - s[:, 0, 1])
    return 0.5 * float(np.ptp(y))


def _entrance_angle(points, x_aft, x_fwd):
    """Half angle of entrance: straight-line fit to the waterline half-breadth.

    The half-breadth is read from the mesh waterline at evenly spaced stations
    over the forward ENTRANCE_FIT_FRACTION_LWL of LWL. Fixed stations keep the
    result from following where the mesh vertices fall; a small dependence on
    tessellation density remains, because the mesh waterline is a polyline.
    The averaging over the span neglects the local stem rounding, as Holtrop's
    definition asks. Over 10 % LWL it agreed with Maxsurf within 0.2 deg on
    one test hull; more hulls are to be checked in the validation set.
    """
    length = x_fwd - x_aft
    segments = points.reshape(-1, 2, 3)
    stations = np.linspace(x_fwd - ENTRANCE_FIT_FRACTION_LWL * length, x_fwd, ENTRANCE_FIT_STATIONS)
    xx, yy = [], []
    for x in stations:
        half_breadth = _waterline_half_breadth(segments, x)
        if half_breadth is not None:
            xx.append(x)
            yy.append(half_breadth)
    if len(xx) < 3 or np.ptp(xx) == 0:
        return None
    return float(np.degrees(np.arctan(abs(np.polyfit(xx, yy, 1)[0]))))


def measure(mesh, z_wl_m: float, *, rho_kg_m3: float = 1025.0,
            section_count: int = 161, include_sections: bool = True) -> Hydrostatics:
    """Compute zero-trim hydrostatics, with SI inputs and outputs.

    Second moments use the exact degree-two midpoint quadrature on every
    clipped triangle. Displacement is mass in kg; volume is always m³.
    """
    tri = triangles_array(mesh)
    if not np.isfinite(rho_kg_m3) or rho_kg_m3 <= 0:
        raise ValueError("Water density must be finite and positive (kg/m³).")
    if not isinstance(section_count, (int, np.integer)) or section_count < 3:
        raise ValueError("section_count must be an integer >= 3.")
    wet = clip_triangles_below(tri, z_wl_m)
    if not len(wet):
        raise ValueError("Waterline has no immersed surface.")
    normal = 0.5 * np.cross(wet[:, 1] - wet[:, 0], wet[:, 2] - wet[:, 0])
    areas = np.linalg.norm(normal, axis=1)
    centres = wet.mean(axis=1)
    nz = normal[:, 2]
    volume = float(np.sum((centres[:, 2] - z_wl_m) * nz))
    awp = float(-nz.sum())
    if volume <= 0 or awp <= np.finfo(float).eps:
        raise ValueError("Nonpositive volume/waterplane area: check immersion, closure and outward normals.")
    midpoints = 0.5 * (wet + np.roll(wet, -1, axis=1))
    mx = np.sum(nz * np.mean(midpoints[:, :, 0] * (midpoints[:, :, 2] - z_wl_m), axis=1))
    mz = np.sum(nz * np.mean((midpoints[:, :, 2] ** 2 - z_wl_m ** 2) * 0.5, axis=1))
    lcb, kb = float(mx / volume), float(mz / volume)
    lcf = float(-np.sum(nz * centres[:, 0]) / awp)
    points, aft, fwd, breadth = waterplane_bounds(tri, z_wl_m)
    length = fwd - aft
    keel = float(wet[:, :, 2].min())
    draft = float(z_wl_m - keel)
    if min(length, breadth, draft) <= 0:
        raise ValueError("Degenerate immersed dimensions.")
    transom = (normal[:, 0] < -0.7 * areas) & (centres[:, 0] < aft + 2.0)
    warnings = ["transom area uses an aft-normal heuristic; entrance angle is approximate"]
    # A closed immersed skin plus horizontal cap has zero x/y normal flux.
    horizontal_flux = np.linalg.norm(normal[:, :2].sum(axis=0))
    if horizontal_flux > max(1e-6, areas.sum() * 5e-4):
        warnings.append("horizontal normal flux suggests a closure/orientation or tessellation mismatch")
    xs = np.linspace(aft, fwd, section_count) if include_sections else np.empty(0)
    section_area = sectional_areas(tri, xs, z_wl_m) if include_sections else np.empty(0)
    maximum = float(section_area.max()) if len(section_area) else None
    if maximum is not None and maximum <= 0:
        raise ValueError("Nonpositive section area: check outward normals and closure.")
    if len(section_area) and section_area.min() < -1e-7:
        raise ValueError("Negative sectional areas: check closure and outward normals.")
    return Hydrostatics(
        z_wl_m=float(z_wl_m), volume_m3=volume, displacement_kg=volume * rho_kg_m3,
        waterplane_area_m2=awp, lwl_m=length, bwl_m=breadth, draft_m=draft,
        lcb_m=lcb, kb_m=kb, lcf_m=lcf, wetted_area_m2=float(areas.sum()),
        transom_area_m2=float(np.abs(normal[transom, 0]).sum()),
        transom_wetted_area_m2=float(areas[transom].sum()),
        max_section_area_m2=maximum, cb=volume / (length * breadth * draft),
        cp=volume / (maximum * length) if maximum else None,
        cx=maximum / (breadth * draft) if maximum else None,
        cwp=awp / (length * breadth), lcb_percent_lwl=100 * (lcb - 0.5 * (aft + fwd)) / length,
        entrance_half_angle_deg=_entrance_angle(points, aft, fwd),
        x_aft_m=aft, x_fwd_m=fwd, keel_z_m=keel, kb_above_keel_m=kb - keel,
        section_x_m=xs, section_area_m2=section_area, warnings=warnings)
