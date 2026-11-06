"""Shape-mode candidates of one hull: build, float at the baseline displacement, measure, check.

A ``MorphModel`` holds what every candidate shares: the baseline NURBS geometry, a fixed-topology UV
tessellation, the baseline floating condition, the mode frame and fairness plan, and each mode's
exact effect on the tessellation vertices. Weights, knots and trims never change, so a candidate's
vertices are the baseline vertices plus the amplitude-weighted mode effects (exact for the CV morph),
and a candidate is evaluated without re-tessellating.

Every number is a Python result. Gate limits are passed in by the caller; none belongs to a hull.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .geometry import tessellate
from .geometry.hgeo import Geometry
from .geometry.nurbs import basis_matrix
from .hydro import measure
from .optimize.fairness import FairnessPlan, FairnessRules
from .optimize.material_lines import MaterialLineSet
from .optimize.mode_frame import FrameRules, check_frame, derive_frame
from .optimize.shape_modes_v2 import (MODE_INFO_V2, MODE_NAMES_V2, MODE_UNITS_V2, ModeFrameV2, apply_modes_v2,
                                      mode_displacement_v2)

MORPH_LAYERS = ("HULL",)
LIMIT_UNITS = {
    "draft_change_m": "m", "lcb_shift_percent_lwl": "% LWL", "lwl_change_percent": "%",
    "bwl_change_percent": "%", "normal_deviation_m": "m", "upper_edge_m": "m",
    "slope_dy_dx": "-", "slope_dy_dz": "-",
}


def rational_basis(surface, u, v):
    """Nonzero rational basis R of a NURBS surface at paired (u, v): S(u, v) = sum R[n, a, b] P[iu+a, iv+b].

    Weights do not change in a control-point morph, so a CV displacement field d moves the surface
    point by exactly sum R d; this is the fast, sparse form of evaluating the morphed surface.
    """
    ou, ov = surface.order
    nu, nv = surface.cv_count
    bu = basis_matrix(u, surface.knots_u, ou, nu)
    bv = basis_matrix(v, surface.knots_v, ov, nv)
    iu = np.minimum(np.argmax(bu > 0, axis=1), nu - ou)
    iv = np.minimum(np.argmax(bv > 0, axis=1), nv - ov)
    rows = np.arange(len(u))[:, None]
    lu = bu[rows, iu[:, None] + np.arange(ou)]
    lv = bv[rows, iv[:, None] + np.arange(ov)]
    w = surface.cv[..., 3][iu[:, None, None] + np.arange(ou)[None, :, None], iv[:, None, None] + np.arange(ov)[None, None, :]]
    r = lu[:, :, None] * lv[:, None, :] * w
    return iu, iv, r / r.sum(axis=(1, 2))[:, None, None]


def _gather(field, iu, iv, r):
    """sum R * field over the nonzero control points (field: nu x nv x 3)."""
    ou, ov = r.shape[1:]
    values = field[iu[:, None, None] + np.arange(ou)[None, :, None], iv[:, None, None] + np.arange(ov)[None, None, :]]
    return np.einsum("nab,nabk->nk", r, values)


@dataclass
class Candidate:
    amplitudes: dict
    z_wl_m: float
    iterations: int
    hydrostatics: object
    triangles: np.ndarray
    metrics: dict = field(default_factory=dict)


class MorphModel:
    def __init__(self, geometry: Geometry, *, z_wl_m: float | None = None, volume_m3: float | None = None,
                 solve=None, rho_kg_m3: float, grid: dict, frame: ModeFrameV2 | None = None,
                 plan: FairnessPlan | None = None, rules: FrameRules = FrameRules(),
                 fairness_rules: FairnessRules = FairnessRules(), notify=lambda stage, fraction: None):
        """Baseline hull floating at z_wl_m, or at the waterline that solve(mesh, volume_m3) returns."""
        if not any(face.layer in MORPH_LAYERS for face in geometry.faces):
            raise ValueError("No hull faces to morph (layer HULL)")
        if (z_wl_m is None) == (volume_m3 is None) or (volume_m3 is not None and solve is None):
            raise ValueError("Give either the waterline height or a volume and a solver")
        self.geometry = Geometry(tuple(f for f in geometry.faces if f.layer in MORPH_LAYERS), dict(geometry.metadata))
        self.rho = rho_kg_m3
        notify("Tessellating the hull for shape modes", 0.1)
        self.mesh = tessellate(self.geometry, keep_degenerate=True, include_uv=True, **grid)
        self.z0 = float(z_wl_m) if z_wl_m is not None else float(solve(self.mesh, volume_m3))
        self.hydro0 = measure(self.mesh, self.z0, rho_kg_m3=rho_kg_m3, include_sections=True)
        self.frame = frame if frame is not None else derive_frame(self.mesh, self.z0, rules, self.hydro0)
        self.plan = plan if plan is not None else FairnessPlan.from_frame(self.frame, fairness_rules)
        self.notes = check_frame(self.frame, self.mesh)
        notify("Mode effects on the hull", 0.3)
        self.units = self._unit_modes(self.mesh.face_ids, self.mesh.uv_triangles.reshape(-1, 3, 2))
        t = self.mesh.triangles
        normal = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(normal, axis=1)
        self.valid = length > 1e-12
        self.normal = normal / np.maximum(length, 1e-300)[:, None]
        top = self.frame.z_top(t[:, :, 0])
        self.form = self.valid[:, None] & (t[:, :, 2] <= top)
        self.upper = self.valid[:, None] & (t[:, :, 2] >= top)
        self.lines = None

    def _unit_modes(self, face_ids, uv):
        """Exact motion per unit amplitude of surface points given by face and (u, v) (rows of three)."""
        out = {name: np.zeros(uv.shape[:2] + (3,)) for name in MODE_NAMES_V2}
        for index in np.unique(face_ids):
            mask = face_ids == index
            surface = self.geometry.faces[int(index)].surface
            iu, iv, r = rational_basis(surface, uv[mask, :, 0].reshape(-1), uv[mask, :, 1].reshape(-1))
            cv = surface.cv[..., :3] / surface.cv[..., 3:4]
            for name in MODE_NAMES_V2:
                motion = _gather(mode_displacement_v2(name, cv, self.frame), iu, iv, r)
                out[name][mask] = motion.reshape(-1, uv.shape[1], 3)
        return out

    def fairness_lines(self, notify=lambda stage, fraction: None):
        if self.lines is None:
            notify("Fairness lines on the NURBS surfaces", 0.4)
            lines = MaterialLineSet.from_reference(self.geometry, self.mesh, self.frame, plan=self.plan)
            # Same quantity as MaterialLineSet.precompute (morphed NURBS minus baseline at each material
            # address), computed in the sparse rational form.
            lines.names = MODE_NAMES_V2
            lines.D = []
            for c in lines.curves:
                motion = self._unit_modes(c.face_ids, c.uv[:, None, :])
                lines.D.append(np.stack([motion[n][:, 0] for n in MODE_NAMES_V2]))
            self.lines = lines
            points = np.concatenate([c.points[::10] for c in self.lines.curves])
            gradients = []
            for name in MODE_NAMES_V2:
                rows = []
                for axis in (0, 2):
                    step = np.zeros(3)
                    step[axis] = 1e-4
                    rows.append((mode_displacement_v2(name, points + step, self.frame)[:, 1]
                                 - mode_displacement_v2(name, points - step, self.frame)[:, 1]) / 2e-4)
                gradients.append(np.stack(rows))
            self.gradients = np.stack(gradients)
        return self.lines

    @staticmethod
    def amplitudes(values: dict) -> dict:
        unknown = sorted(set(values) - set(MODE_NAMES_V2))
        if unknown:
            raise ValueError("Unknown shape modes: " + ", ".join(unknown))
        out = {}
        for name, value in values.items():
            value = float(value)
            if not np.isfinite(value):
                raise ValueError(f"Amplitude of {name} must be finite")
            if value:
                out[name] = value
        return out

    def displacement(self, amplitudes: dict) -> np.ndarray:
        d = np.zeros_like(self.mesh.triangles)
        for name, value in amplitudes.items():
            d += value * self.units[name]
        return d

    def candidate(self, amplitudes: dict) -> Candidate:
        """Candidate floated at the baseline volume (zero trim)."""
        from .geometry import TriangleMesh
        amplitudes = self.amplitudes(amplitudes)
        triangles = self.mesh.triangles + self.displacement(amplitudes)
        mesh = TriangleMesh(triangles, self.mesh.face_ids, self.mesh.layers, {})
        target = self.hydro0.volume_m3
        z = self.z0
        iterations = 0
        for iterations in range(1, 41):
            h = measure(mesh, z, rho_kg_m3=self.rho, include_sections=False)
            step = (target - h.volume_m3) / h.waterplane_area_m2
            z += step
            if abs(step) < 1e-9 * max(1.0, self.hydro0.draft_m):
                break
        else:
            raise ValueError("Candidate equilibrium did not converge")
        hydro = measure(mesh, z, rho_kg_m3=self.rho, include_sections=True)
        if abs(hydro.volume_m3 / target - 1) > 1e-6:
            raise ValueError("Candidate equilibrium did not reach the baseline volume")
        return Candidate(amplitudes, z, iterations, hydro, triangles)

    def evaluate(self, amplitudes: dict, *, limits: dict | None = None, fairness: bool = True,
                 notify=lambda stage, fraction: None) -> dict:
        notify("Floating the candidate", 0.5)
        c = self.candidate(amplitudes)
        h0, h1 = self.hydro0, c.hydrostatics
        changes = {
            "z_wl_m": c.z_wl_m - self.z0,
            "draft_m": h1.draft_m - h0.draft_m,
            "lcb_percent_lwl": 100.0 * (h1.lcb_m - h0.lcb_m) / h0.lwl_m,
            "lwl_percent": 100.0 * (h1.lwl_m / h0.lwl_m - 1),
            "bwl_percent": 100.0 * (h1.bwl_m / h0.bwl_m - 1),
            "wetted_area_percent": 100.0 * (h1.wetted_area_m2 / h0.wetted_area_m2 - 1),
            "cp": h1.cp - h0.cp,
            "transom_area_m2": h1.transom_area_m2 - h0.transom_area_m2,
            "entrance_half_angle_deg": ((h1.entrance_half_angle_deg - h0.entrance_half_angle_deg)
                                        if h1.entrance_half_angle_deg is not None and h0.entrance_half_angle_deg is not None
                                        else None),
        }
        d = self.displacement(c.amplitudes)
        normal = np.abs(np.einsum("tvk,tk->tv", d, self.normal))
        size = np.linalg.norm(d, axis=2)
        deviation = {
            "normal_deviation_m": float(normal[self.form].max(initial=0.0)),
            "largest_motion_m": float(size[self.form].max(initial=0.0)),
            "upper_edge_m": float(size[self.upper].max(initial=0.0)),
        }
        result = {"amplitudes": c.amplitudes, "z_wl_m": c.z_wl_m, "iterations": c.iterations,
                  "hydrostatics": h1.to_dict(), "changes": changes, "deviation": deviation}
        if fairness:
            lines = self.fairness_lines(notify)
            notify("Fairness gates", 0.7)
            result["fairness"] = lines.evaluate(lines.deformed_points(c.amplitudes))
            a = np.array([c.amplitudes.get(n, 0.0) for n in MODE_NAMES_V2])
            gradient = np.tensordot(a, self.gradients, axes=1)
            deviation["slope_dy_dx"] = float(np.max(np.abs(gradient[0])))
            deviation["slope_dy_dz"] = float(np.max(np.abs(gradient[1])))
        result["gates"] = self.gates(changes, deviation, result.get("fairness"), limits or {})
        result["feasible"] = all(g["pass"] for g in result["gates"])
        result["candidate"] = c
        return result

    def gates(self, changes: dict, deviation: dict, fairness: dict | None, limits: dict) -> list:
        unknown = sorted(set(limits) - set(LIMIT_UNITS))
        if unknown:
            raise ValueError("Unknown limits: " + ", ".join(unknown))
        values = {"draft_change_m": changes["draft_m"], "lcb_shift_percent_lwl": changes["lcb_percent_lwl"],
                  "lwl_change_percent": changes["lwl_percent"], "bwl_change_percent": changes["bwl_percent"],
                  "normal_deviation_m": deviation["normal_deviation_m"], "upper_edge_m": deviation["upper_edge_m"],
                  "slope_dy_dx": deviation.get("slope_dy_dx"), "slope_dy_dz": deviation.get("slope_dy_dz")}
        gates = []
        for name, limit in limits.items():
            limit = float(limit)
            if not limit > 0:
                raise ValueError(f"Limit {name} must be positive")
            value = values[name]
            if value is None:
                continue
            gates.append({"name": name, "value": value, "limit": limit, "unit": LIMIT_UNITS[name],
                          "pass": abs(value) <= limit + 1e-12, "excess": max(0.0, abs(value) / limit - 1)})
        if fairness is not None:
            # "excess" is each gate's normalised violation (0 when it passes), as in STUDY-002's
            # constrained search: counts as counts, the rest relative to their thresholds.
            p = self.plan
            energy = fairness["energy_ratio"].values()
            band = p.ratio[1] - p.ratio[0]
            for name, ok, value, limit, unit, excess in (
                    ("G1 new inflections", fairness["pass_G1"], fairness["new_infl"], 0, "count",
                     float(fairness["new_infl"])),
                    ("G2 curvature change", fairness["dk_max"] <= p.dk_max, fairness["dk_max"], p.dk_max, "1/m",
                     max(0.0, fairness["dk_max"] / p.dk_max - 1)),
                    ("G2 curvature ratio", fairness["ratio_excess"] <= 0, fairness["ratio_excess"], 0, "excess",
                     max(0.0, fairness["ratio_excess"]) / band),
                    ("G3 fairness energy", fairness["pass_G3"], max(energy), p.energy_max, "ratio",
                     sum(max(0.0, v / p.energy_max - 1) for v in energy)),
                    ("G5 bilge radius", fairness["pass_G5"], fairness["bilge_radius_ratio_min"], p.bilge_radius_min,
                     "ratio", max(0.0, 1 - fairness["bilge_radius_ratio_min"] / p.bilge_radius_min))):
                gates.append({"name": name, "value": value, "limit": limit, "unit": unit, "pass": bool(ok),
                              "excess": float(excess)})
        return gates

    def export_faces(self, amplitudes: dict) -> dict:
        """Morphed control points of every hull face, homogeneous, in model units."""
        moved = apply_modes_v2(self.geometry, self.frame, self.amplitudes(amplitudes), layers=MORPH_LAYERS)
        scale = float(self.geometry.metadata.get("meters_per_unit", 1.0))
        faces = []
        for face in moved.faces:
            cv = face.surface.cv.copy()
            cv[..., :3] /= scale
            faces.append({"object_index": face.object_index, "face_index": face.face_index,
                          "cv_count": list(cv.shape[:2]), "cv": cv.reshape(-1, 4)})
        return {"meters_per_unit": scale, "faces": faces}

    def describe(self) -> dict:
        return {"z_wl_m": self.z0, "frame": self.frame.to_dict(), "plan": self.plan.to_dict(), "notes": self.notes,
                "modes": [{"name": n, "unit": MODE_UNITS_V2[n], "info": MODE_INFO_V2[n]} for n in MODE_NAMES_V2],
                "hydrostatics": self.hydro0.to_dict()}
