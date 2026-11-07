"""Constrained search over shape-mode amplitudes of one hull.

Every candidate is floated at the baseline volume, measured and checked by a ``MorphModel``. A
candidate that fails a gate is ranked by its normalised violation (Deb's rule) and its resistance is
not computed; feasible candidates are ranked by J, the objective as a ratio to the baseline hull, so
J = 1 for the baseline and J < 1 is an improvement in the chosen quantity.

Objective weightings (Q is the chosen quantity at each objective speed, Q0 the baseline's):
  equal    J = mean(Q / Q0)               every speed's change counts the same
  profile  J = sum(w Q) / sum(w Q0)       w = time shares; with brake power, the propulsion energy ratio
  service  J = Q / Q0 at the service speed

Every number is a Python result; nothing here belongs to a particular hull.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

import numpy as np

from .constrained_de import deb_key, differential_evolution
from .shape_modes_v2 import MODE_INFO_V2, MODE_NAMES_V2, MODE_UNITS_V2

# A candidate that could not be floated or measured ranks behind every gate failure; one odd
# candidate is recorded with its error instead of ending a long search.
FAILED_VIOLATION = 1e6
CANDIDATE_ERRORS = (ValueError, ArithmeticError, IndexError, np.linalg.LinAlgError)
SAME_CANDIDATE = 1e-12
# Candidate progress goes out at most this often (generation ends always do): the plug-in redraws its
# page for each message, and one per candidate floods its UI thread.
PROGRESS_INTERVAL_S = 1.0

# Default bounds: fraction modes as fractions, metre modes as fractions of the baseline LWL or draft.
# Starting values from the first test hull's constrained search (its upper bounds), with a
# smaller reverse range; they are not validated limits.
DEFAULT_BOUNDS = {
    "lack_aft": (-0.0005, 0.0005, "lwl"),
    "lack_fwd": (-0.0005, 0.0005, "lwl"),
    "bow_fine": (-0.06, 0.12, None),
    "bow_tip": (-0.05, 0.10, None),
    "stern_narrow": (-0.03, 0.06, None),
    "stern_lift": (-0.036, 0.072, "draft"),
    "bilge_full": (-0.125, 0.25, None),
    "deadrise": (-0.06, 0.12, "draft"),
}

QUANTITIES = {
    "holtrop": ("R_T", "N", "Holtrop 1984 total resistance"),
    "michell": ("R_W+R_F", "N", "Michell wave + ITTC-57 friction (partial; relative comparison only)"),
    "brake_power": ("P_B", "W", "Brake power (Holtrop 1984 + propulsion)"),
}


class StopSearch(Exception):
    """The caller asked the search to stop; the best result so far is returned."""


@dataclass(frozen=True)
class Variable:
    name: str
    low: float
    high: float
    unit: str
    automatic: tuple[str, ...]   # which ends ("min", "max") came from DEFAULT_BOUNDS

    def to_dict(self) -> dict:
        return {"name": self.name, "min": self.low, "max": self.high, "unit": self.unit,
                "automatic": list(self.automatic), "info": MODE_INFO_V2[self.name]}


def variables_from(spec: dict, lwl_m: float, draft_m: float) -> list[Variable]:
    """Search variables in mode order; a missing min or max takes the hull-relative default."""
    if not isinstance(spec, dict) or not spec:
        raise ValueError("Choose at least one shape mode to vary")
    unknown = sorted(set(spec) - set(MODE_NAMES_V2))
    if unknown:
        raise ValueError("Unknown shape modes: " + ", ".join(unknown))
    out = []
    for name in MODE_NAMES_V2:
        if name not in spec:
            continue
        item = spec[name] if spec[name] is not None else {}
        if not isinstance(item, dict) or set(item) - {"min", "max"}:
            raise ValueError(f"Bounds of {name} must be an object with 'min' and/or 'max'")
        low_default, high_default, scale = DEFAULT_BOUNDS[name]
        factor = {"lwl": lwl_m, "draft": draft_m, None: 1.0}[scale]
        values, automatic = {}, []
        for end, default in (("min", low_default), ("max", high_default)):
            value = item.get(end)
            if value is None:
                values[end] = default * factor
                automatic.append(end)
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Bound '{end}' of {name} must be a finite number")
            else:
                values[end] = float(value)
        if not values["max"] > values["min"]:
            raise ValueError(f"{name}: the upper bound must be above the lower bound")
        out.append(Variable(name, values["min"], values["max"], MODE_UNITS_V2[name], tuple(automatic)))
    return out


class Objective:
    """The objective speeds, their weights and J as a function of candidate and baseline values."""

    def __init__(self, quantity: str, weighting: str, condition_speeds=(), profile=None, service_speed_kn=None):
        if quantity not in QUANTITIES:
            raise ValueError(f"Unknown objective quantity '{quantity}'")
        self.quantity, self.weighting = quantity, weighting
        if weighting == "equal":
            speeds = tuple(float(v) for v in condition_speeds)
            weights = (1.0 / len(speeds),) * len(speeds) if speeds else ()
        elif weighting == "profile":
            pairs = sorted((float(s), float(w)) for s, w in profile)
            total = sum(w for _, w in pairs)
            speeds, weights = tuple(s for s, _ in pairs), tuple(w / total for _, w in pairs)
        elif weighting == "service":
            speeds, weights = (float(service_speed_kn),), (1.0,)
        else:
            raise ValueError(f"Unknown weighting '{weighting}'")
        if not speeds:
            raise ValueError("The objective needs at least one speed")
        self.speeds_kn, self.weights = speeds, weights

    def value(self, values, baseline) -> tuple[float, list[float]]:
        q, q0 = np.asarray(values, float), np.asarray(baseline, float)
        ratios = q / q0
        if self.weighting == "equal":
            j = float(np.mean(ratios))
        elif self.weighting == "profile":
            w = np.asarray(self.weights)
            j = float(np.dot(w, q) / np.dot(w, q0))
        else:
            j = float(ratios[0])
        return j, ratios.tolist()

    def describe(self) -> dict:
        symbol, units, label = QUANTITIES[self.quantity]
        if self.weighting == "equal":
            formula = f"J = mean({symbol}/{symbol}0) over {len(self.speeds_kn)} speed(s)"
        elif self.weighting == "profile":
            formula = f"J = sum(w*{symbol}) / sum(w*{symbol}0), w = time shares at {len(self.speeds_kn)} speed(s)"
        else:
            formula = f"J = {symbol}/{symbol}0 at {self.speeds_kn[0]:g} kn"
        return {"quantity": self.quantity, "label": label, "symbol": symbol, "units": units,
                "weighting": self.weighting, "speeds_kn": list(self.speeds_kn), "weights": list(self.weights),
                "formula": formula}


def _finite(value):
    return value if value is not None and math.isfinite(value) else None


def _log_record(entry: dict) -> dict:
    return {"index": entry["index"], "generation": entry["generation"], "amplitudes": entry["amplitudes"],
            "feasible": entry["feasible"], "violation": _finite(entry["violation"]), "J": _finite(entry["J"]),
            "ratios": entry.get("ratios"), "failed_gates": entry.get("failed_gates", []),
            "error": entry.get("error")}


def _same(a: dict, b: dict) -> bool:
    return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) <= SAME_CANDIDATE for k in set(a) | set(b))


def run_search(model, variables: list[Variable], objective: Objective, *, measure, limits: dict,
               fairness: bool = True, population: int = 30, max_evaluations: int = 600, seed: int = 170026,
               start: str = "uniform", keep: int = 10, notify=lambda stage, fraction, data=None: None,
               stop_requested=lambda: False, log_path=None, span=(0.35, 0.98)) -> dict:
    """Differential evolution over ``variables`` with Deb's rule; returns the best feasible candidates.

    ``measure(mesh, z_wl_m, hydro, baseline)`` returns {"values": Q at the objective speeds, "notes":
    one list per objective speed, ...}; ``baseline`` is None for the baseline itself, else its record.
    ``stop_requested()`` is polled before every candidate; a stop ends the search with what it has.
    """
    from ..geometry import TriangleMesh

    started = time.perf_counter()
    names = [v.name for v in variables]
    bounds = [(v.low, v.high) for v in variables]
    low, high = span
    notify("Baseline objective", low - 0.03)
    baseline = measure(model.mesh, model.z0, model.hydro0, None)
    q0 = baseline["values"]
    if any(not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0) for v in q0):
        raise ValueError("The baseline objective is not positive and finite at every objective speed")
    if fairness:
        model.fairness_lines(lambda stage, fraction: notify(stage, low - 0.01))
    archive: list[dict] = []
    best_trace: list[float | None] = []
    history: list[dict] = []
    state = {"generation": 0, "best": None, "feasible": 0, "notified": -math.inf}
    log = open(log_path, "w", encoding="utf-8") if log_path else None

    def progress_data() -> dict:
        best = state["best"]
        return {"evaluations": len(archive), "max_evaluations": max_evaluations,
                "generation": state["generation"], "feasible": state["feasible"],
                "best_J": best["J"] if best else None, "best_amplitudes": best["amplitudes"] if best else None,
                "elapsed_s": time.perf_counter() - started}

    def evaluate(x) -> dict:
        if stop_requested():
            raise StopSearch
        amplitudes = {n: float(a) for n, a in zip(names, x)}
        entry = {"index": len(archive), "generation": state["generation"], "amplitudes": amplitudes,
                 "feasible": False, "violation": FAILED_VIOLATION, "J": math.inf}
        try:
            result = model.evaluate(amplitudes, limits=limits, fairness=fairness)
        except CANDIDATE_ERRORS as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        else:
            candidate = result.pop("candidate")
            failed = [g for g in result["gates"] if not g["pass"]]
            entry.update(gates=result["gates"], failed_gates=[g["name"] for g in failed],
                         changes=result["changes"], deviation=result["deviation"], z_wl_m=candidate.z_wl_m)
            if failed:
                entry["violation"] = max(sum(g.get("excess", 0.0) for g in failed), 1e-12)
            else:
                mesh = TriangleMesh(candidate.triangles, model.mesh.face_ids, model.mesh.layers, {})
                try:
                    measured = measure(mesh, candidate.z_wl_m, candidate.hydrostatics, baseline)
                except CANDIDATE_ERRORS as exc:
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                else:
                    j, ratios = objective.value(measured["values"], q0)
                    entry.update(feasible=True, violation=0.0, J=j, ratios=ratios, values=measured["values"],
                                 notes=measured.get("notes"), hydrostatics=candidate.hydrostatics.to_dict())
                    if measured.get("design") is not None:
                        entry["propulsion_design"] = measured["design"]
        archive.append(entry)
        if entry["feasible"]:
            state["feasible"] += 1
            if state["best"] is None or entry["J"] < state["best"]["J"]:
                state["best"] = entry
        best_trace.append(state["best"]["J"] if state["best"] else None)
        if log is not None:
            log.write(json.dumps(_log_record(entry), ensure_ascii=True, allow_nan=False) + "\n")
            log.flush()
        now = time.perf_counter()
        if now - state["notified"] >= PROGRESS_INTERVAL_S:
            state["notified"] = now
            notify(f"Candidate {len(archive)} of {max_evaluations}",
                   low + (high - low) * len(archive) / max_evaluations, progress_data())
        return entry

    def generation_done(generation, count, results):
        feasible = [r for r in results if r["feasible"]]
        history.append({"generation": generation, "evaluations": count,
                        "best_J": state["best"]["J"] if state["best"] else None,
                        "population_feasible": len(feasible),
                        "population_best_J": min((r["J"] for r in feasible), default=None),
                        "population_median_J": float(np.median([r["J"] for r in feasible])) if feasible else None})
        state["generation"] = generation + 1
        notify(f"Generation {generation} done", low + (high - low) * count / max_evaluations, progress_data())

    zero = np.zeros(len(variables))
    seeds = [zero] if all(v.low <= 0 <= v.high for v in variables) else []
    stopped = False
    try:
        differential_evolution(evaluate, bounds, seeds, population_size=population, max_evaluations=max_evaluations,
                               rng_seed=seed, callback=generation_done, spread=0.07 if start == "baseline" else None)
    except StopSearch:
        stopped = True
    finally:
        if log is not None:
            log.close()
    notify("Ranking candidates", high, progress_data())
    ranked = sorted((e for e in archive if e["feasible"]), key=deb_key)
    chosen: list[dict] = []
    for entry in ranked:
        if not any(_same(entry["amplitudes"], c["amplitudes"]) for c in chosen):
            chosen.append(entry)
        if len(chosen) >= keep:
            break
    failures = [e for e in archive if not e["feasible"]]
    gate_failures: dict[str, int] = {}
    for entry in failures:
        for name in entry.get("failed_gates", []):
            gate_failures[name] = gate_failures.get(name, 0) + 1
    baseline_record = {"z_wl_m": model.z0, "speeds_kn": list(objective.speeds_kn), "values": list(q0),
                       "notes": baseline.get("notes"), "hydrostatics": model.hydro0.to_dict()}
    if baseline.get("design") is not None:
        baseline_record["propulsion_design"] = baseline["design"]
    warnings = list(baseline.get("warnings", []))
    if not chosen:
        warnings.append("No feasible candidate was found; widen the bounds or relax the limits.")
    elif chosen[0]["J"] >= 1.0 - 1e-12:
        warnings.append("No feasible candidate improved on the baseline.")
    if stopped:
        warnings.append(f"Stopped by the user after {len(archive)} of {max_evaluations} evaluations.")
    return {"objective": objective.describe(),
            "variables": [v.to_dict() for v in variables],
            "search": {"method": "DE current-to-pbest/1 + rand/1/bin; Deb feasibility-first",
                       "population": population, "max_evaluations": max_evaluations, "seed": seed,
                       "start": start, "evaluations": len(archive), "generations": len(history),
                       "stopped": stopped, "elapsed_s": time.perf_counter() - started, "fairness_gates": fairness,
                       "limits": dict(limits)},
            "baseline": baseline_record,
            "best": chosen[0] if chosen else None,
            "candidates": [{k: v for k, v in e.items() if k != "hydrostatics"} for e in chosen],
            "counts": {"evaluated": len(archive), "feasible": state["feasible"],
                       "failed_gates": len(failures) - sum(1 for e in failures if "error" in e),
                       "errors": sum(1 for e in failures if "error" in e), "gate_failures": gate_failures},
            "best_by_evaluation": best_trace,
            "history": history,
            "log_path": str(log_path) if log_path else None,
            "warnings": warnings}
