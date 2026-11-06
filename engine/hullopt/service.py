"""JSON-RPC 2.0 service for the Rhino plug-in (``hullopt serve``).

Framing: one JSON object per line on stdin; one reply or notification per line
on stdout, ASCII only. Diagnostics go to stderr, so stdout carries protocol
messages only. Units are SI: lengths in metres in the model's own coordinate
frame (the geometry dump records its unit scale), masses in kg, forces in N,
powers in W, speeds in knots. Parameters ending in ``_t`` are tonnes.

The service holds no hull-specific defaults: every hull value arrives with the
request. Long requests send ``progress`` notifications carrying the request id.
"""

from __future__ import annotations

from collections import defaultdict
import json
import math
import platform
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import __version__

PROTOCOL_VERSION = 1
KNOT_TO_M_S = 1852.0 / 3600.0
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, ENGINE_ERROR = (
    -32700, -32600, -32601, -32602, -32000)
WATERLINE_JOIN_TOLERANCE_M = 1e-6
EQUILIBRIUM_SCAN_LEVELS = 24

Notify = Callable[..., None]   # notify(stage, fraction[, data]): data is an optional JSON object
METHODS: dict[str, Callable[["Session", dict, Notify], dict]] = {}


class ParamsError(ValueError):
    """A request parameter is missing or has the wrong type or range."""


def method(name: str):
    def register(function):
        METHODS[name] = function
        return function
    return register


def _jsonable(value: Any) -> Any:
    """Plain JSON types; non-finite floats become null (JSON has no NaN)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _number(params: dict, name: str, default: Any = ..., *, positive: bool = False,
            minimum: float | None = None) -> float:
    if name not in params or params[name] is None:
        if default is ...:
            raise ParamsError(f"Missing parameter '{name}'")
        return default
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ParamsError(f"Parameter '{name}' must be a finite number")
    if positive and value <= 0:
        raise ParamsError(f"Parameter '{name}' must be positive")
    if minimum is not None and value < minimum:
        raise ParamsError(f"Parameter '{name}' must be at least {minimum}")
    return float(value)


def _string(params: dict, name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ParamsError(f"Parameter '{name}' must be a non-empty string")
    return value


def _layers(params: dict) -> list[str] | None:
    value = params.get("layers")
    if value is None:
        return None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ParamsError("Parameter 'layers' must be a non-empty list of layer names")
    return value


def _speeds(params: dict) -> tuple[float, ...]:
    value = params.get("speeds_kn")
    if not isinstance(value, list) or not value:
        raise ParamsError("Parameter 'speeds_kn' must be a non-empty list")
    speeds = tuple(_number({"v": v}, "v", positive=True) for v in value)
    if list(speeds) != sorted(set(speeds)):
        raise ParamsError("Parameter 'speeds_kn' must be strictly increasing")
    return speeds


class Session:
    """Keeps the latest tessellated hulls, keyed by file identity and layers."""

    def __init__(self, capacity: int = 2):
        self.capacity = capacity
        self._meshes: dict[tuple, tuple[Any, Any]] = {}
        self._morphs: dict[tuple, Any] = {}

    def mesh(self, params: dict, notify: Notify):
        """Geometry and mesh for the request's ``hgeo``, ``layers`` and ``tessellation``."""
        from .config import TessellationConfig
        from .geometry import load_hgeo, tessellate

        layers = _layers(params)
        try:
            grid = TessellationConfig.model_validate(params.get("tessellation") or {})
        except ValueError as exc:
            raise ParamsError(str(exc)) from exc
        source = Path(_string(params, "hgeo")).expanduser().resolve(strict=True)
        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns, tuple(sorted(layers)) if layers else None,
               grid.min_grid, grid.max_grid, grid.grid_factor)
        if key not in self._meshes:
            notify("Reading geometry", 0.05)
            geometry = load_hgeo(source, layers)
            notify("Tessellating surfaces", 0.15)
            mesh = tessellate(geometry, **grid.model_dump())
            while len(self._meshes) >= self.capacity:
                self._meshes.pop(next(iter(self._meshes)))
            self._meshes[key] = (geometry, mesh)
        return self._meshes[key]


    def morph(self, params: dict, notify: Notify):
        """Shape-mode model of the hull faces (layer HULL) at the request's floating condition."""
        from .config import TessellationConfig
        from .geometry import load_hgeo
        from .morph import MorphModel
        from .optimize.fairness import FairnessPlan, FairnessRules
        from .optimize.mode_frame import FrameRules
        from .optimize.shape_modes_v2 import ModeFrameV2

        rho = _number(params, "rho_kg_m3", 1025.0, positive=True)
        try:
            grid = TessellationConfig.model_validate(params.get("tessellation") or {})
            frame = ModeFrameV2.from_dict(params["frame"]) if params.get("frame") else None
            plan = FairnessPlan.from_dict(params["plan"]) if params.get("plan") else None
            rules = FrameRules(**(params.get("rules") or {}))
            fairness_rules = FairnessRules(**(params.get("fairness_rules") or {}))
        except (TypeError, ValueError) as exc:
            raise ParamsError(str(exc)) from exc
        if params.get("displacement_t") is not None:
            floating = ("volume", _number(params, "displacement_t", positive=True) * 1000.0 / rho)
        else:
            floating = ("z", _number(params, "z_wl_m"))
        source = Path(_string(params, "hgeo")).expanduser().resolve(strict=True)
        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns, rho, floating, grid.min_grid, grid.max_grid,
               grid.grid_factor, json.dumps([params.get(k) for k in ("frame", "plan", "rules", "fairness_rules")],
                                            sort_keys=True))
        if key not in self._morphs:
            notify("Reading geometry", 0.05)
            geometry = load_hgeo(source, ["HULL"])
            model = MorphModel(geometry, z_wl_m=floating[1] if floating[0] == "z" else None,
                               volume_m3=floating[1] if floating[0] == "volume" else None,
                               solve=lambda mesh, volume: _solve_waterline(mesh, volume, rho, notify).z_wl_m,
                               rho_kg_m3=rho, grid=grid.model_dump(), frame=frame, plan=plan, rules=rules,
                               fairness_rules=fairness_rules, notify=notify)
            while len(self._morphs) >= self.capacity:
                self._morphs.pop(next(iter(self._morphs)))
            self._morphs[key] = model
        return self._morphs[key]


def _chain(segments: np.ndarray, tolerance: float) -> list[np.ndarray]:
    """Join unordered plane-cut segments into polylines (closed ones repeat the start)."""
    if not len(segments):
        return []
    keys = np.round(segments / tolerance).astype(np.int64)
    ends: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for index in range(len(segments)):
        ends[tuple(keys[index, 0])].append((index, 0))
        ends[tuple(keys[index, 1])].append((index, 1))
    used = np.zeros(len(segments), dtype=bool)
    lines = []
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        points = [segments[start, 0], segments[start, 1]]
        point_keys = [tuple(keys[start, 0]), tuple(keys[start, 1])]
        for forward in (True, False):
            while True:
                key = point_keys[-1] if forward else point_keys[0]
                following = next(((j, e) for j, e in ends[key] if not used[j]), None)
                if following is None:
                    break
                j, e = following
                used[j] = True
                if forward:
                    points.append(segments[j, 1 - e])
                    point_keys.append(tuple(keys[j, 1 - e]))
                else:
                    points.insert(0, segments[j, 1 - e])
                    point_keys.insert(0, tuple(keys[j, 1 - e]))
        lines.append(np.asarray(points))
    return lines


def _waterline_polylines(mesh, z_wl_m: float) -> list[list[list[float]]]:
    from .hydro.hydrostatics import waterplane_bounds
    from .hydro.sections import triangles_array

    points, *_ = waterplane_bounds(triangles_array(mesh), z_wl_m)
    return [line.tolist() for line in _chain(points.reshape(-1, 2, 3), WATERLINE_JOIN_TOLERANCE_M)]


def _geometry_summary(geometry, mesh) -> dict:
    triangles = mesh.triangles
    meta = geometry.metadata
    return {"faces": len(geometry.faces), "triangles": len(triangles),
            "layers": meta.get("selected_layers", []), "unit_system": meta.get("unit_system"),
            "meters_per_unit": meta.get("meters_per_unit"), "area_m2": mesh.area_m2,
            "bbox_m": [triangles.reshape(-1, 3).min(axis=0), triangles.reshape(-1, 3).max(axis=0)],
            "dump_sha256": meta.get("dump_sha256")}


def _hydrostatics(mesh, z_wl_m: float, rho_kg_m3: float, section_count: int = 161) -> dict:
    from .hydro import measure

    return measure(mesh, z_wl_m, rho_kg_m3=rho_kg_m3, section_count=section_count).to_dict()


@method("engine.info")
def engine_info(session: Session, params: dict, notify: Notify) -> dict:
    return {"name": "hullopt", "version": __version__, "protocol": PROTOCOL_VERSION,
            "python": platform.python_version(), "numpy": np.__version__,
            "executable": sys.executable, "methods": sorted(METHODS)}


@method("geometry.summary")
def geometry_summary(session: Session, params: dict, notify: Notify) -> dict:
    geometry, mesh = session.mesh(params, notify)
    return _geometry_summary(geometry, mesh)


@method("hydro.measure")
def hydro_measure(session: Session, params: dict, notify: Notify) -> dict:
    """Zero-trim hydrostatics at a given waterline height (model z, metres)."""
    z_wl_m = _number(params, "z_wl_m")
    rho = _number(params, "rho_kg_m3", 1025.0, positive=True)
    geometry, mesh = session.mesh(params, notify)
    notify("Hydrostatics", 0.6)
    return {"hydrostatics": _hydrostatics(mesh, z_wl_m, rho), "waterline_m": _waterline_polylines(mesh, z_wl_m),
            "geometry": _geometry_summary(geometry, mesh)}


def _volume_at(mesh, z: float, rho: float) -> float | None:
    from .hydro import measure

    try:
        return measure(mesh, z, rho_kg_m3=rho, include_sections=False).volume_m3
    except ValueError:
        return None


def _solve_waterline(mesh, target: float, rho: float, notify: Notify):
    """Zero-trim equilibrium for an immersed volume, bracketed by a scan over the hull's own height."""
    from .hydro import solve_equilibrium

    z = mesh.triangles[:, :, 2]
    bottom, top = float(z.min()), float(z.max())
    levels = np.linspace(bottom, top, EQUILIBRIUM_SCAN_LEVELS + 2)[1:-1]
    notify("Bracketing the waterline", 0.3)
    previous = None
    bracket = None
    reachable = []
    for level in levels:
        volume = _volume_at(mesh, float(level), rho)
        if volume is None:
            continue
        reachable.append((float(level), volume))
        if volume >= target:
            bracket = (previous[0] if previous else float(level), float(level))
            break
        previous = (float(level), volume)
    if bracket is None:
        largest = max((v for _, v in reachable), default=0.0)
        raise ValueError(f"Target volume {target:.6g} m³ exceeds the largest immersed volume "
                         f"{largest:.6g} m³ found below z = {levels[-1]:.6g} m.")
    low, high = bracket
    if low == high:
        low = bottom + 0.25 * (high - bottom)
    margin = 1e-6 * max(top - bottom, 1.0)
    notify("Solving equilibrium", 0.6)
    return solve_equilibrium(mesh, target, 0.5 * (low + high), draft_change_limit_m=0.5 * (high - low) + margin,
                             relative_tolerance=1e-7, rho_kg_m3=rho)


@method("hydro.equilibrium")
def hydro_equilibrium(session: Session, params: dict, notify: Notify) -> dict:
    """Zero-trim waterline for a displacement mass (tonnes) or an immersed volume.

    The bracket comes from a scan over the hull's own height, so no draft guess
    is needed. A target beyond the scanned range is an error naming that range.
    """
    rho = _number(params, "rho_kg_m3", 1025.0, positive=True)
    if params.get("displacement_t") is not None:
        target = _number(params, "displacement_t", positive=True) * 1000.0 / rho
    else:
        target = _number(params, "volume_m3", positive=True)
    geometry, mesh = session.mesh(params, notify)
    result = _solve_waterline(mesh, target, rho, notify)
    return {"z_wl_m": result.z_wl_m, "iterations": result.iterations, "target_volume_m3": target,
            "relative_error": result.relative_error, "hydrostatics": result.hydrostatics.to_dict(),
            "waterline_m": _waterline_polylines(mesh, result.z_wl_m), "geometry": _geometry_summary(geometry, mesh)}


def _with_power(result) -> dict:
    data = result.to_dict()
    if result.units == "N":
        data["power_W"] = [value * speed * KNOT_TO_M_S for value, speed in zip(result.values, result.speeds_kn)]
    return data


def _resistance_settings(params: dict) -> dict:
    from .config import HoltropConfig, MichellConfig, PropulsionConfig
    from .resistance import Conditions

    rho = _number(params, "rho_kg_m3", 1025.0, positive=True)
    nu = _number(params, "nu_m2_s", 1.18831e-6, positive=True)
    gravity = _number(params, "gravity_m_s2", 9.80665, positive=True)
    include_transom = bool(params.get("friction_include_transom", True))
    methods = params.get("methods", ["holtrop", "friction"])
    if not isinstance(methods, list) or not methods or not set(methods) <= {"holtrop", "michell", "friction"}:
        raise ParamsError("Parameter 'methods' must list some of: holtrop, michell, friction")
    try:
        holtrop_cfg = HoltropConfig.model_validate(params.get("holtrop") or {})
        michell_cfg = MichellConfig.model_validate(params.get("michell") or {})
        propulsion_cfg = (PropulsionConfig.model_validate(params["propulsion"])
                          if params.get("propulsion") is not None else None)
    except ValueError as exc:
        raise ParamsError(str(exc)) from exc
    speeds, added = _speeds(params), ()
    if propulsion_cfg is not None:
        if "holtrop" not in methods:
            raise ParamsError("Brake power needs Holtrop 1984, the only total-resistance method; "
                              "add 'holtrop' to the methods")
        if not any(abs(v - propulsion_cfg.design_speed_kn) <= 1e-9 for v in speeds):
            added = (propulsion_cfg.design_speed_kn,)
            speeds = tuple(sorted(speeds + added))
    return {"rho": rho, "nu": nu, "gravity": gravity, "include_transom": include_transom, "methods": methods,
            "holtrop": holtrop_cfg, "michell": michell_cfg, "propulsion": propulsion_cfg, "added_speeds": added,
            "conditions": Conditions(speeds, rho, nu)}


def _resistance_results(mesh, z_wl_m: float, hydro, settings: dict, notify: Notify,
                        span: tuple[float, float] = (0.4, 0.95)) -> list:
    """Labelled results of the chosen methods at one waterline (hydro measured there).

    Every requested speed is computed. A speed where a method leaves its published range, or
    where Michell's checked integral misses its tolerance after adaptive refinement, keeps its
    value and is named in the method's warnings and in ``_speed_notes``.
    """
    from .resistance import Holtrop1984, ITTC57Friction, MichellThinShip, OffsetHull, RawResult

    conditions, methods = settings["conditions"], settings["methods"]
    gravity, include_transom = settings["gravity"], settings["include_transom"]
    holtrop_cfg = settings["holtrop"]
    low, high = span
    results = []
    if "holtrop" in methods:
        notify("Holtrop 1984", low)
        results.append(Holtrop1984(
            include_transom=include_transom, gravity_m_s2=gravity,
            entrance_angle_policy=holtrop_cfg.entrance_angle_policy,
            entrance_half_angle_deg=holtrop_cfg.entrance_half_angle_deg,
            entrance_angle_source=holtrop_cfg.entrance_angle_source,
            lcb_reference_x_m=holtrop_cfg.lcb_reference_x_m, roughness_m=holtrop_cfg.roughness_m,
            stern_parameter=holtrop_cfg.stern_parameter, bulb_area_m2=holtrop_cfg.bulb_area_m2,
            bulb_height_m=holtrop_cfg.bulb_height_m).evaluate(hydro, conditions))
    friction = None
    if "friction" in methods or "michell" in methods:
        friction = ITTC57Friction(include_transom=include_transom).evaluate(hydro, conditions)
        if "friction" in methods:
            results.append(friction)
    if "michell" in methods:
        notify("Michell thin-ship wave resistance: offsets", low + 0.2 * (high - low))
        mc = settings["michell"]
        offsets = OffsetHull.from_mesh(mesh, z_wl_m, nx=mc.nx, nz=mc.nz, symmetry_tolerance=mc.symmetry_tolerance,
                                       multi_interval_policy=mc.multi_interval_policy,
                                       bow_padding_fraction_lwl=mc.bow_padding_fraction_lwl)
        closure = mc.transom_closure_fraction_lwl * hydro.lwl_m

        def progress(index: int, count: int, speed: float) -> None:
            notify(f"Michell wave resistance at {speed:g} kn ({index + 1} of {count})",
                   low + (0.25 + 0.75 * index / count) * (high - low))

        wave = MichellThinShip(offsets, include_friction=False, gravity_m_s2=gravity, lambda_max=mc.lambda_max,
                               quadrature_order=mc.quadrature_order, transom_closure_length_m=closure,
                               convergence_tolerance=mc.convergence_tolerance,
                               check_convergence=mc.check_convergence,
                               lambda_max_limit=mc.lambda_max_limit).evaluate(hydro, conditions, progress=progress)
        results.append(wave)
        unconverged = [item["speed_kn"] for item in wave.metadata.get("numerical_convergence", [])
                       if item.get("converged") is False]
        partial_warnings = ["Partial sum excludes viscous form resistance, empirical immersed-transom pressure, "
                            "correlation/roughness, air and appendages.",
                            "Virtual transom closure is a numerical assumption, not a validated Maxsurf model."]
        if unconverged:
            partial_warnings.append("Michell outer integral not converged at " +
                                    ", ".join(f"{v:g}" for v in unconverged) + " kn; see the wave component.")
        results.append(RawResult(
            backend="PY-MICHELL-PLUS-ITTC57-PARTIAL",
            label="Michell wave + ITTC-57 friction; partial model, omissions explicit",
            quantity="wave_plus_friction_force", is_physical_prediction=True, speeds_kn=conditions.speeds_kn,
            values=tuple(a + b for a, b in zip(wave.values, friction.values)), units="N",
            warnings=tuple(partial_warnings),
            metadata={"components_N": {"RW": list(wave.values), "RF": list(friction.values)},
                      "virtual_closure_length_m": closure}))
    return results


# The Maxsurf manual's published Holtrop speed screening limit (also in the Holtrop warnings).
HOLTROP_FN_SCREENING = 0.8


def _speed_notes(results: list) -> list[list[list[str]]]:
    """For each result, one list of notes per speed: outside a published range, or unconverged."""
    wave = next((r for r in results if r.backend == "PY-MICHELL"), None)
    out = []
    for result in results:
        notes: list[list[str]] = [[] for _ in result.speeds_kn]
        if result.backend == "PY-HOLTROP-1984":
            for i, fn in enumerate(result.metadata.get("froude_number", [])):
                if fn > HOLTROP_FN_SCREENING:
                    notes[i].append(f"Fn {fn:.3f} above {HOLTROP_FN_SCREENING:g}: Holtrop extrapolated")
        if wave is not None and result.backend in ("PY-MICHELL", "PY-MICHELL-PLUS-ITTC57-PARTIAL"):
            tolerance = wave.metadata.get("convergence_tolerance", 0.0)
            for i, item in enumerate(wave.metadata.get("numerical_convergence", [])):
                if item.get("converged") is False:
                    change = max(item.get("quadrature_relative_change", 0.0), item.get("cutoff_relative_change", 0.0))
                    notes[i].append(f"Michell integral not converged ({change:.2%} > {tolerance:.2%})")
        out.append(notes)
    return out


@method("resistance.evaluate")
def resistance_evaluate(session: Session, params: dict, notify: Notify) -> dict:
    """Labelled resistance models at one waterline; no model is called a total unless it is one."""
    from .hydro import measure

    z_wl_m = _number(params, "z_wl_m")
    settings = _resistance_settings(params)
    geometry, mesh = session.mesh(params, notify)
    notify("Hydrostatics", 0.3)
    hydro = measure(mesh, z_wl_m, rho_kg_m3=settings["rho"])
    results = _resistance_results(mesh, z_wl_m, hydro, settings, notify)
    notify("Collecting results", 0.95)
    conditions = settings["conditions"]
    notes = _speed_notes(results)
    out = {"hydrostatics": hydro.to_dict(),
           "results": [{**_with_power(r), "speed_notes": n} for r, n in zip(results, notes)],
           "conditions": {"speeds_kn": list(conditions.speeds_kn), "rho_kg_m3": settings["rho"],
                          "nu_m2_s": settings["nu"], "gravity_m_s2": settings["gravity"],
                          "friction_include_transom": settings["include_transom"],
                          "added_speeds_kn": list(settings["added_speeds"])},
           "assumptions": {"holtrop": settings["holtrop"].model_dump(), "michell": settings["michell"].model_dump(),
                           "maxsurf_validated": False},
           "geometry": _geometry_summary(geometry, mesh)}
    if settings["propulsion"] is not None:
        from .propulsion import HullPropulsionInputs, evaluate_propulsion

        notify("Propulsion and brake power", 0.97)
        holtrop = next(r for r in results if r.backend == "PY-HOLTROP-1984")
        out["propulsion"] = evaluate_propulsion(conditions.speeds_kn, holtrop.values,
                                                HullPropulsionInputs.from_holtrop(holtrop), settings["propulsion"],
                                                rho_kg_m3=settings["rho"], gravity_m_s2=settings["gravity"],
                                                z_wl_m=z_wl_m)
        out["propulsion"]["resistance_backend"] = holtrop.backend
    return out


@method("morph.frame")
def morph_frame(session: Session, params: dict, notify: Notify) -> dict:
    """Mode frame and fairness plan of the hull at its floating condition (derived unless given)."""
    model = session.morph(params, notify)
    return model.describe()


def _amplitudes(params: dict) -> dict:
    from .optimize.shape_modes_v2 import MODE_NAMES_V2

    value = params.get("amplitudes") or {}
    if not isinstance(value, dict):
        raise ParamsError("Parameter 'amplitudes' must map mode names to numbers")
    unknown = sorted(set(value) - set(MODE_NAMES_V2))
    if unknown:
        raise ParamsError("Unknown shape modes: " + ", ".join(unknown))
    return {name: _number(value, name) for name in value}


def _limits(params: dict) -> dict:
    from .morph import LIMIT_UNITS

    value = params.get("limits") or {}
    if not isinstance(value, dict):
        raise ParamsError("Parameter 'limits' must map limit names to numbers")
    unknown = sorted(set(value) - set(LIMIT_UNITS))
    if unknown:
        raise ParamsError("Unknown limits: " + ", ".join(unknown))
    return {name: _number(value, name, positive=True) for name in value}


@method("morph.evaluate")
def morph_evaluate(session: Session, params: dict, notify: Notify) -> dict:
    """One candidate: floated at the baseline volume, measured, checked; resistance when speeds are given."""
    from .geometry import TriangleMesh

    amplitudes = _amplitudes(params)
    limits = _limits(params)
    # Brake power is not compared here yet; its design speed must not change the compared speeds.
    settings = (_resistance_settings({k: v for k, v in params.items() if k != "propulsion"})
                if params.get("speeds_kn") else None)
    model = session.morph(params, notify)
    result = model.evaluate(amplitudes, limits=limits, fairness=bool(params.get("fairness", True)), notify=notify)
    candidate = result.pop("candidate")
    out = {"baseline": {"z_wl_m": model.z0, "hydrostatics": model.hydro0.to_dict()}, **result,
           "frame": model.frame.to_dict(), "notes": model.notes}
    if settings is not None:
        cache = getattr(model, "resistance_cache", None)
        if cache is None:
            cache = model.resistance_cache = {}
        key = json.dumps({k: params.get(k) for k in ("speeds_kn", "methods", "holtrop", "michell", "nu_m2_s",
                                                     "gravity_m_s2", "friction_include_transom")}, sort_keys=True)
        if key not in cache:
            notify("Baseline resistance", 0.8)
            cache[key] = _resistance_results(model.mesh, model.z0, model.hydro0, settings, notify, span=(0.8, 0.9))
        notify("Candidate resistance", 0.9)
        mesh = TriangleMesh(candidate.triangles, model.mesh.face_ids, model.mesh.layers, {})
        baseline = cache[key]
        candidates = _resistance_results(mesh, candidate.z_wl_m, candidate.hydrostatics, settings, notify,
                                         span=(0.9, 0.98))
        rows = []
        for base, cand, base_notes, cand_notes in zip(baseline, candidates, _speed_notes(baseline),
                                                      _speed_notes(candidates)):
            ratio = [c / b if b else None for b, c in zip(base.values, cand.values)]
            finite = [r for r in ratio if r is not None]
            rows.append({"backend": cand.backend, "label": cand.label, "units": cand.units,
                         "speeds_kn": list(cand.speeds_kn), "baseline": list(base.values),
                         "candidate": list(cand.values), "ratio": ratio,
                         "mean_ratio": float(np.mean(finite)) if finite else None,
                         "warnings": list(cand.warnings),
                         "speed_notes": [[*("baseline: " + n for n in b), *("candidate: " + n for n in c)]
                                         for b, c in zip(base_notes, cand_notes)]})
        out["resistance"] = rows
    return out


@method("morph.export")
def morph_export(session: Session, params: dict, notify: Notify) -> dict:
    """Morphed control points of every hull face (homogeneous, model units) to rebuild the surfaces."""
    model = session.morph(params, notify)
    notify("Moving control points", 0.6)
    return {**model.export_faces(_amplitudes(params)), "frame": model.frame.to_dict()}


def _file_path(params: dict, name: str) -> Path | None:
    """An optional file path parameter whose folder must exist."""
    value = params.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ParamsError(f"Parameter '{name}' must be a file path")
    path = Path(value).expanduser()
    if not path.parent.is_dir():
        raise ParamsError(f"Folder of '{name}' does not exist: {path.parent}")
    return path


OPTIMIZE_BACKENDS = {"holtrop": "PY-HOLTROP-1984", "michell": "PY-MICHELL-PLUS-ITTC57-PARTIAL",
                     "brake_power": "PY-HOLTROP-1984"}


@method("optimize.run")
def optimize_run(session: Session, params: dict, notify: Notify) -> dict:
    """Constrained search over shape-mode amplitudes for the smallest objective ratio J.

    Gates are the request's limits plus (unless ``fairness`` is false) the fairness gates; a
    candidate that fails one never wins on resistance. The slope limits are measured on the
    fairness lines, so with ``fairness`` false they are skipped too. ``objective`` chooses the quantity
    (holtrop, michell, brake_power) and the weighting over speeds: equal (the request's
    ``speeds_kn``), profile (time shares) or service (one speed). When the file ``stop_path``
    appears, the search ends after the current candidate with the best candidates found so far
    (the service reads its requests one at a time, so a stop cannot arrive as a message).
    """
    from .config import ObjectiveConfig, SearchConfig
    from .optimize.search import Objective, run_search, variables_from

    try:
        objective_cfg = ObjectiveConfig.model_validate(params.get("objective") or {})
        search_cfg = SearchConfig.model_validate(params.get("search") or {})
    except ValueError as exc:
        raise ParamsError(str(exc)) from exc
    limits = _limits(params)
    spec = params.get("variables")
    if not isinstance(spec, dict) or not spec:
        raise ParamsError("Parameter 'variables' must map the shape modes to vary to their bounds")
    fairness = bool(params.get("fairness", True))
    log_path, stop_path = (_file_path(params, name) for name in ("log_path", "stop_path"))
    if stop_path is not None and stop_path.exists():
        raise ParamsError(f"The stop file already exists, so the search would stop at once: {stop_path}")
    quantity = objective_cfg.quantity
    if objective_cfg.weighting == "equal":
        speeds = _speeds(params)
    elif objective_cfg.weighting == "profile":
        speeds = tuple(sorted(s for s, _ in objective_cfg.profile))
    else:
        speeds = (objective_cfg.service_speed_kn,)
    resistance_params = {k: v for k, v in params.items() if k not in ("propulsion", "speeds_kn", "methods")}
    resistance_params.update(speeds_kn=list(speeds), methods=["michell"] if quantity == "michell" else ["holtrop"])
    if quantity == "brake_power":
        if params.get("propulsion") is None:
            raise ParamsError("A brake-power objective needs the 'propulsion' settings")
        resistance_params["propulsion"] = params["propulsion"]
    settings = _resistance_settings(resistance_params)
    evaluated = settings["conditions"].speeds_kn
    index = [next(i for i, v in enumerate(evaluated) if abs(v - s) <= 1e-9) for s in speeds]
    try:
        objective = Objective(quantity, objective_cfg.weighting, speeds, objective_cfg.profile,
                              objective_cfg.service_speed_kn)
    except ValueError as exc:
        raise ParamsError(str(exc)) from exc
    model = session.morph(params, notify)
    try:
        variables = variables_from(spec, model.hydro0.lwl_m, model.hydro0.draft_m)
    except ValueError as exc:
        raise ParamsError(str(exc)) from exc
    backend = OPTIMIZE_BACKENDS[quantity]

    def measure(mesh, z_wl_m, hydro, baseline):
        results = _resistance_results(mesh, z_wl_m, hydro, settings, lambda *args: None)
        k = next(i for i, r in enumerate(results) if r.backend == backend)
        notes = [_speed_notes(results)[k][i] for i in index]
        if quantity != "brake_power":
            # Holtrop's general modelling notes belong to the resistance results; Michell's partial-sum
            # notes say what the objective leaves out, so they stay with the optimisation result.
            return {"values": [results[k].values[i] for i in index], "notes": notes,
                    "warnings": list(results[k].warnings) if quantity == "michell" else []}
        from .propulsion import HullPropulsionInputs, evaluate_propulsion

        cfg = settings["propulsion"]
        if baseline is not None and objective_cfg.propeller == "baseline":
            design = baseline["design"]
            cfg = cfg.model_copy(update={"pitch_ratio": design["pitch_ratio"], "area_ratio": design["area_ratio"]})
        holtrop = results[k]
        prop = evaluate_propulsion(evaluated, holtrop.values, HullPropulsionInputs.from_holtrop(holtrop), cfg,
                                   rho_kg_m3=settings["rho"], gravity_m_s2=settings["gravity"], z_wl_m=z_wl_m)
        values = [prop["per_speed"]["pb_W"][i] for i in index]
        if any(v is None for v in values):
            raise ValueError("Brake power could not be found at every objective speed")
        design = {k2: prop["design"].get(k2) for k2 in ("speed_kn", "pitch_ratio", "pitch_ratio_source", "area_ratio",
                                                       "area_ratio_source", "keller_area_ratio_min", "eta0",
                                                       "eta_r", "pb_W", "max_speed_calm_kn")}
        return {"values": values, "notes": [n + prop["speed_notes"][i] for n, i in zip(notes, index)],
                "design": design,
                # The general estimate note and the maximum-speed search (it needs a speed range, not the
                # objective speeds) belong to the Propulsion results, not to the search.
                "warnings": [w for w in prop["warnings"]
                             if not w.startswith(("Brake power is", "Installed power is not reached"))]}

    result = run_search(model, variables, objective, measure=measure, limits=limits, fairness=fairness,
                        population=search_cfg.population, max_evaluations=search_cfg.max_evaluations,
                        seed=search_cfg.seed, start=search_cfg.start, keep=search_cfg.keep, notify=notify,
                        stop_requested=(lambda: stop_path.exists()) if stop_path is not None else (lambda: False),
                        log_path=log_path)
    result["conditions"] = {"evaluated_speeds_kn": list(evaluated), "added_speeds_kn": list(settings["added_speeds"]),
                            "rho_kg_m3": settings["rho"], "nu_m2_s": settings["nu"],
                            "gravity_m_s2": settings["gravity"]}
    if quantity == "brake_power":
        result["objective"]["propeller"] = objective_cfg.propeller
    result["frame"] = model.frame.to_dict()
    result["notes"] = model.notes
    result["warnings"].append("Every value is a Python estimate; the best candidate is a proposal to check "
                              "(all speeds, a fairness review, Maxsurf or CFD), not a validated gain.")
    notify("Done", 1.0)
    return result


@method("engine.shutdown")
def engine_shutdown(session: Session, params: dict, notify: Notify) -> dict:
    return {"stopping": True}


def _error(request_id: Any, code: int, message: str, data: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle(session: Session, request: Any, send: Callable[[dict], None]) -> tuple[dict | None, bool]:
    """Return (response or None for notifications, stop flag)."""
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return _error(request.get("id") if isinstance(request, dict) else None, INVALID_REQUEST,
                      "Expected a JSON-RPC 2.0 request object"), False
    request_id = request.get("id")
    is_notification = "id" not in request
    params = request.get("params", {})
    if params is None:
        params = {}
    function = METHODS.get(request["method"])
    if function is None:
        return (None if is_notification else _error(request_id, METHOD_NOT_FOUND,
                                                     f"Unknown method '{request['method']}'")), False
    if not isinstance(params, dict):
        return (None if is_notification else _error(request_id, INVALID_PARAMS, "Parameters must be an object")), False

    def notify(stage: str, fraction: float, data: dict | None = None) -> None:
        if not is_notification:
            message = {"request_id": request_id, "stage": stage, "fraction": fraction}
            if data is not None:
                message["data"] = data
            send({"jsonrpc": "2.0", "method": "progress", "params": message})

    try:
        result = function(session, params, notify)
    except ParamsError as exc:
        response = _error(request_id, INVALID_PARAMS, str(exc))
    except (ValueError, OSError, KeyError) as exc:
        response = _error(request_id, ENGINE_ERROR, str(exc) or type(exc).__name__, {"type": type(exc).__name__})
    except Exception as exc:  # report, keep serving
        traceback.print_exc(file=sys.stderr)
        response = _error(request_id, ENGINE_ERROR, f"Internal error: {type(exc).__name__}: {exc}",
                          {"type": type(exc).__name__, "traceback": traceback.format_exc()[-4000:]})
    else:
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
    return (None if is_notification else response), request["method"] == "engine.shutdown"


def serve(stdin=None, stdout=None) -> int:
    """Serve requests until end of input or ``engine.shutdown``."""
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    session = Session()

    def send(message: dict) -> None:
        text = json.dumps(_jsonable(message), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        stdout.write(text.encode("ascii") + b"\n")
        stdout.flush()

    saved_stdout = sys.stdout
    sys.stdout = sys.stderr  # a stray print() must never corrupt the protocol stream
    try:
        send({"jsonrpc": "2.0", "method": "ready", "params": engine_info(session, {}, lambda *_: None)})
        for raw in iter(stdin.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                send(_error(None, PARSE_ERROR, f"Parse error: {exc}"))
                continue
            response, stop = handle(session, request, send)
            if response is not None:
                send(response)
            if stop:
                break
    finally:
        sys.stdout = saved_stdout
    return 0
