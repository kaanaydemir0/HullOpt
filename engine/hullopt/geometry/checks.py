"""Explicit candidate constraints and sampled geometry diagnostics.

These checks do not prove absence of self intersections. Positive derivative of
the design x-map proves monotonicity of that point map; control-point mapping
and trimmed NURBS export need their own measurement and CAD seam checks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np


@dataclass
class CheckResult:
    name: str
    passed: bool
    value: Any
    limit: Any
    message: str

    def to_dict(self) -> dict:
        result = asdict(self)
        result["passed"] = bool(self.passed)
        return result


@dataclass
class CheckReport:
    checks: list[CheckResult] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: [
        "Sampled sections and normal flux do not prove watertightness or absence of self intersections.",
        "A monotone design x-map does not by itself prove validity of the control-point-mapped NURBS model.",
        "Native CAD export, seam consistency and Maxsurf confirmation remain separate verification steps.",
    ])

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "checks": [check.to_dict() for check in self.checks],
                "limitations": list(self.limitations)}


def _number(record, name: str) -> float:
    value = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _positive_option(value: float, name: str, *, allow_zero: bool = False) -> None:
    if not np.isfinite(value) or (value < 0 if allow_zero else value <= 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}")


def sampled_section_parity(mesh, z_wl_m: float, *, station_count: int = 41,
                           level_count: int = 31) -> dict:
    """Count odd y-ray intersections and integrate widths at midpoint x,z cells.

    The computation uses no surface orientations or divergence volume. Sampling
    deliberately avoids end planes; arbitrary holes smaller than the sample
    spacing can still be missed. Open decks above WL are allowed.
    """
    from hullopt.hydro.sections import triangles_array
    triangles = triangles_array(mesh)
    for value, name in ((station_count, "station_count"), (level_count, "level_count")):
        if not isinstance(value, (int, np.integer)) or value < 3:
            raise ValueError(f"{name} must be an integer >= 3")
    if not np.isfinite(z_wl_m):
        raise ValueError("Waterline must be finite")
    lo = triangles.min(axis=(0, 1))
    hi = triangles.max(axis=(0, 1))
    if z_wl_m <= lo[2] or hi[0] <= lo[0]:
        raise ValueError("Sampling requires positive immersion and longitudinal extent")
    dx, dz = (hi[0] - lo[0]) / station_count, (z_wl_m - lo[2]) / level_count
    xs = lo[0] + (np.arange(station_count) + 0.5) * dx
    zs = lo[2] + (np.arange(level_count) + 0.5) * dz
    # The final ray independently estimates the missing waterplane cap area.
    wl_step = 64 * np.finfo(float).eps * max(float(hi[2] - lo[2]), abs(z_wl_m), 1.0)
    scan_levels = np.append(zs, z_wl_m - wl_step)
    xmin, xmax = triangles[:, :, 0].min(axis=1), triangles[:, :, 0].max(axis=1)
    odd = nonempty = 0
    volume = waterplane_area = 0.0
    examples = []
    for x in xs:
        selected = triangles[(xmin <= x) & (xmax > x)]
        points, valid = [], []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            first, second = selected[:, a], selected[:, b]
            crossing = (first[:, 0] <= x) != (second[:, 0] <= x)
            fraction = np.divide(x - first[:, 0], second[:, 0] - first[:, 0],
                                 out=np.zeros(len(selected)), where=crossing)
            points.append(first + fraction[:, None] * (second - first))
            valid.append(crossing)
        points = np.stack(points, axis=1)
        valid = np.stack(valid, axis=1)
        keep = valid.sum(axis=1) == 2
        segments = points[keep][valid[keep]].reshape(-1, 2, 3)
        first, second = segments[:, 0], segments[:, 1]
        for level_index, z in enumerate(scan_levels):
            crossing = (first[:, 2] <= z) != (second[:, 2] <= z)
            if not crossing.any():
                continue
            a, b = first[crossing], second[crossing]
            ys = np.sort(a[:, 1] + (b[:, 1] - a[:, 1]) * (z - a[:, 2]) / (b[:, 2] - a[:, 2]))
            nonempty += 1
            if len(ys) % 2:
                odd += 1
                if len(examples) < 5:
                    examples.append({"x_m": float(x), "z_m": float(z), "intersections": len(ys)})
                continue
            width = float((ys[1::2] - ys[0::2]).sum())
            if level_index == level_count:
                waterplane_area += width * dx
            else:
                volume += width * dx * dz
    return {"odd_scanlines": odd, "nonempty_scanlines": nonempty,
            "sampled_volume_m3": float(volume), "sampled_waterplane_area_m2": float(waterplane_area),
            "station_count": int(station_count),
            "level_count": int(level_count), "odd_examples": examples}


def check_mesh_geometry(candidate_mesh, z_wl_m: float, *, baseline_mesh=None,
                        same_connectivity: bool = False, check_parity: bool = True,
                        parity_station_count: int = 41, parity_level_count: int = 31,
                        measured_volume_m3: float | None = None,
                        parity_volume_tolerance_rel: float = 0.005,
                        horizontal_flux_tolerance_rel: float = 5e-4) -> CheckReport:
    """Check mesh diagnostics; corresponding triangle indices must be explicit.

    ``same_connectivity=True`` means each candidate triangle corresponds to the
    baseline triangle with the same index. A remesh must leave this false.
    """
    from hullopt.hydro.hydrostatics import clip_triangles_below
    from hullopt.hydro.sections import triangles_array
    _positive_option(parity_volume_tolerance_rel, "parity_volume_tolerance_rel")
    _positive_option(horizontal_flux_tolerance_rel, "horizontal_flux_tolerance_rel")
    triangles = triangles_array(candidate_mesh)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    twice_area = np.linalg.norm(normals, axis=1)
    report = CheckReport()
    collapsed = int(np.count_nonzero(twice_area == 0))
    area_check = CheckResult("nonzero_triangle_area", collapsed == 0, collapsed, 0,
                             "Counts exactly collapsed candidate triangles.")
    report.checks.append(area_check)
    if same_connectivity:
        if baseline_mesh is None:
            raise ValueError("same_connectivity requires baseline_mesh")
        original = triangles_array(baseline_mesh)
        if original.shape != triangles.shape:
            raise ValueError("Meshes declared to share connectivity must have matching triangle shapes")
        base_normals = np.cross(original[:, 1] - original[:, 0], original[:, 2] - original[:, 0])
        base_area = np.linalg.norm(base_normals, axis=1)
        # Cross products at a surface pole can be ~1e-16 m² from subtracting
        # coordinates near x=42 m, with their sign controlled by roundoff.
        # Only compare orientations above a coordinate/edge-scaled error bound;
        # this is a floating-point resolution test, not a physical area cutoff.
        edge_scale = np.maximum(np.linalg.norm(original[:, 1] - original[:, 0], axis=1),
                                np.linalg.norm(original[:, 2] - original[:, 0], axis=1))
        coordinate_scale = np.maximum(1.0, np.max(np.abs(original), axis=(1, 2)))
        area_roundoff_bound = 64 * np.finfo(float).eps * coordinate_scale * edge_scale
        usable = base_area > area_roundoff_bound
        persistent = int(np.count_nonzero((~usable) & (twice_area == 0)))
        unexplained = int(np.count_nonzero(usable & (twice_area == 0)))
        area_check.passed = unexplained == 0
        area_check.value = unexplained
        area_check.message = ("Counts new exact collapses relative to usable baseline triangles; "
                              "persistent negligible-area parametric pole facets are allowed.")
        new_degenerate = int(np.count_nonzero(usable & (twice_area <= base_area * 1e-10)))
        dot = np.einsum("ij,ij->i", base_normals, normals)
        changed = int(np.count_nonzero(usable & (dot <= 0)))
        report.checks.extend([
            CheckResult("baseline_area_resolution", True,
                        {"unresolved_triangle_count": int(np.count_nonzero(~usable)),
                         "unresolved_twice_area_sum_m2": float(base_area[~usable].sum())},
                        "64*machine_epsilon*coordinate_scale*edge_length",
                        "Orientation is tested only where a baseline area vector exceeds its floating-point roundoff bound."),
            CheckResult("persistent_parametric_degenerates", True, persistent, "informational",
                        "Existing negligible-area baseline pole facets may remain collapsed in a fixed UV template; omit zero-area facets from STL export."),
            CheckResult("same_connectivity_new_degenerates", new_degenerate == 0, new_degenerate, 0,
                        "New candidate area collapses relative to corresponding baseline triangles."),
            CheckResult("same_connectivity_orientation", changed == 0, changed, 0,
                        "Corresponding triangle normals must retain a positive dot product."),
        ])
    wet = clip_triangles_below(triangles, z_wl_m)
    wet_normals = np.cross(wet[:, 1] - wet[:, 0], wet[:, 2] - wet[:, 0]) / 2
    wet_area = float(np.linalg.norm(wet_normals, axis=1).sum())
    horizontal_flux = float(np.linalg.norm(wet_normals[:, :2].sum(axis=0)))
    flux_limit = max(1e-6, wet_area * horizontal_flux_tolerance_rel)
    report.checks.append(CheckResult("immersed_horizontal_normal_flux", horizontal_flux <= flux_limit,
                                    horizontal_flux, flux_limit,
                                    "A closed wetted surface plus its horizontal WL cap has zero x/y normal flux (m²)."))
    if check_parity:
        scan = sampled_section_parity(triangles, z_wl_m, station_count=parity_station_count,
                                      level_count=parity_level_count)
        parity_ok = scan["odd_scanlines"] == 0 and scan["nonempty_scanlines"] > 0
        report.checks.append(CheckResult("sampled_section_parity", parity_ok, scan, 0,
                                        "Every nonempty sampled y-ray must have an even number of intersections."))
        vertical_flux = abs(float(wet_normals[:, 2].sum()) + scan["sampled_waterplane_area_m2"])
        vertical_flux_limit = max(1e-6, scan["sampled_waterplane_area_m2"] * parity_volume_tolerance_rel)
        report.checks.append(CheckResult("sampled_vertical_normal_flux",
                                        bool(parity_ok and vertical_flux <= vertical_flux_limit),
                                        vertical_flux, vertical_flux_limit,
                                        "Wetted z-normal flux plus independently sampled WL cap area should vanish (m²)."))
        if measured_volume_m3 is not None:
            _positive_option(measured_volume_m3, "measured_volume_m3")
            relative_error = abs(scan["sampled_volume_m3"] / measured_volume_m3 - 1)
            report.checks.append(CheckResult("sampled_volume_crosscheck",
                                            bool(parity_ok and relative_error <= parity_volume_tolerance_rel),
                                            relative_error, parity_volume_tolerance_rel,
                                            "Independent midpoint-width volume is a coarse diagnostic, not a CAD reference."))
    return report


def check_candidate(baseline_measurements, candidate_measurements, *, target_volume_m3: float,
                    reference_z_wl_m: float, draft_change_limit_m: float = 0.05,
                    displacement_tolerance_rel: float = 0.001,
                    length_tolerance_rel: float = 0.002, breadth_tolerance_rel: float = 0.002,
                    closure_bottom_z_m: float | None = None,
                    side_recess_bottom_z_m: float | None = None, min_clearance_m: float = 0.10,
                    target_cp: float | None = None, cp_tolerance_abs: float = 1e-4,
                    target_lcb_m: float | None = None, lcb_tolerance_m: float | None = None,
                    xmap_result=None, baseline_mesh=None, candidate_mesh=None,
                    same_connectivity: bool = False, check_parity: bool = True,
                    parity_station_count: int = 41, parity_level_count: int = 31,
                    parity_volume_tolerance_rel: float = 0.005) -> CheckReport:
    """Apply explicit zero-trim candidate constraints in SI units.

    Cp and LCB targets are absolute; LCB is model x in metres. The default LCB
    tolerance is 0.01% of baseline LWL. Length and breadth are compared with the
    baseline measurements. The draft limit is checked against both WL and the
    actual baseline keel-to-WL draft, detecting accidental vertical changes.
    """
    for value, name in ((target_volume_m3, "target_volume_m3"),
                        (draft_change_limit_m, "draft_change_limit_m"),
                        (displacement_tolerance_rel, "displacement_tolerance_rel"),
                        (length_tolerance_rel, "length_tolerance_rel"),
                        (breadth_tolerance_rel, "breadth_tolerance_rel"),
                        (cp_tolerance_abs, "cp_tolerance_abs")):
        _positive_option(value, name)
    _positive_option(min_clearance_m, "min_clearance_m", allow_zero=True)
    if not np.isfinite(reference_z_wl_m):
        raise ValueError("reference_z_wl_m must be finite")
    baseline_length = _number(baseline_measurements, "lwl_m")
    baseline_breadth = _number(baseline_measurements, "bwl_m")
    baseline_draft = _number(baseline_measurements, "draft_m")
    for value, name in ((baseline_length, "baseline LWL"), (baseline_breadth, "baseline BWL"),
                        (baseline_draft, "baseline draft")):
        _positive_option(value, name)
    report = CheckReport()

    def bounded(name, value, limit, message):
        report.checks.append(CheckResult(name, bool(np.isfinite(value) and value <= limit + 1e-12),
                                        value if np.isfinite(value) else None, limit, message))

    volume = _number(candidate_measurements, "volume_m3")
    wl = _number(candidate_measurements, "z_wl_m")
    draft = _number(candidate_measurements, "draft_m")
    bounded("fixed_displacement", abs(volume / target_volume_m3 - 1), displacement_tolerance_rel,
            "Relative candidate volume error against the fixed target.")
    bounded("waterline_change", abs(wl - reference_z_wl_m), draft_change_limit_m,
            "Waterline change from the unchanged baseline reference (m).")
    bounded("draft_change", abs(draft - baseline_draft), draft_change_limit_m,
            "Actual keel-to-WL draft change from baseline (m).")
    bounded("waterline_length", abs(_number(candidate_measurements, "lwl_m") / baseline_length - 1),
            length_tolerance_rel, "Relative LWL change from baseline.")
    bounded("waterline_breadth", abs(_number(candidate_measurements, "bwl_m") / baseline_breadth - 1),
            breadth_tolerance_rel, "Relative BWL change from baseline.")
    for plane, name in ((closure_bottom_z_m, "closure_clearance"),
                        (side_recess_bottom_z_m, "side_recess_clearance")):
        if plane is not None:
            if not np.isfinite(plane):
                raise ValueError("Clearance elevations must be finite")
            clearance = plane - wl
            report.checks.append(CheckResult(name, bool(np.isfinite(clearance) and
                                                        clearance + 1e-12 >= min_clearance_m),
                                            clearance if np.isfinite(clearance) else None,
                                            {"minimum_m": min_clearance_m},
                                            "Dry feature elevation minus candidate WL (m)."))
    if target_cp is not None:
        _positive_option(target_cp, "target_cp")
        bounded("target_cp", abs(_number(candidate_measurements, "cp") - target_cp), cp_tolerance_abs,
                "Absolute Cp target error; Cp is recomputed at the equilibrium WL.")
    if target_lcb_m is not None:
        if not np.isfinite(target_lcb_m):
            raise ValueError("target_lcb_m must be finite")
        tolerance = baseline_length * 1e-4 if lcb_tolerance_m is None else lcb_tolerance_m
        _positive_option(tolerance, "lcb_tolerance_m")
        bounded("target_lcb", abs(_number(candidate_measurements, "lcb_m") - target_lcb_m), tolerance,
                "Absolute LCB target error in model x metres; positive x is forward.")
    if xmap_result is not None:
        derivative = _number(xmap_result, "minimum_derivative")
        report.checks.append(CheckResult("design_xmap_monotonicity",
                                        bool(np.isfinite(derivative) and derivative > 0),
                                        derivative if np.isfinite(derivative) else None, "> 0",
                                        "Analytic minimum point-map derivative; control-point-mapped NURBS validity is checked separately."))
    if candidate_mesh is not None:
        mesh_report = check_mesh_geometry(candidate_mesh, wl, baseline_mesh=baseline_mesh,
                                          same_connectivity=same_connectivity, check_parity=check_parity,
                                          parity_station_count=parity_station_count,
                                          parity_level_count=parity_level_count,
                                          measured_volume_m3=volume,
                                          parity_volume_tolerance_rel=parity_volume_tolerance_rel)
        report.checks.extend(mesh_report.checks)
    return report
