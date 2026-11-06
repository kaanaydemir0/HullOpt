"""Immutable M4 model evaluations and per-speed baseline/candidate comparison.

Holtrop's empirical total and Michell's inviscid wave component remain distinct.
Adding ITTC friction to Michell is an explicitly partial sum, never a Maxsurf
result or a complete viscous/transom/correlation resistance model.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import time

import numpy as np

from ..config import StudyConfig, file_sha256
from ..geometry import load_hgeo, tessellate
from ..hydro import measure, solve_equilibrium
from ..resistance.base import Conditions, RawResult
from ..resistance.friction import ITTC57Friction
from ..store import ResultStore
from .record import EvaluationRecord, candidate_hash, canonical_json


class ResistanceGeometryRejected(ValueError):
    """The selected geometry/immersion violates engineering constraints."""


def _verified_candidate_geometry(config: StudyConfig, directory: Path, representation: str):
    """Accept historical geometry only after artifact/source integrity checks.

    Its measurements are recomputed with current code. A historical package
    hash is provenance, not a reason to silently reuse historical force values.
    """
    directory = directory.resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if "measurements.json" not in manifest["artifact_sha256"]:
        raise ValueError("Candidate manifest does not cover measurements.json.")
    for name, digest in manifest["artifact_sha256"].items():
        if Path(name).name != name or file_sha256(directory / name) != digest:
            raise ValueError(f"Candidate artifact integrity failure: {name}")
    payload = json.loads((directory / "measurements.json").read_text(encoding="utf-8"))
    if payload["record"]["status"] != "succeeded" or candidate_hash(payload["identity"]) != manifest["candidate_hash"]:
        raise ValueError("Candidate must have a matching successful generation record.")
    prior_inputs = payload["provenance"]["input_sha256"]
    if prior_inputs["source_3dm"] != config.baseline.source_sha256:
        raise ValueError("Candidate was generated from another baseline .3dm.")
    if prior_inputs["geometry_dump"] != file_sha256(config.baseline.geometry_dump):
        raise ValueError("Candidate baseline neutral dump differs from this study.")
    if representation == "native":
        if (payload.get("native_cad") or {}).get("status") != "succeeded":
            raise ValueError("Selected candidate has no verified native CAD export; use --representation control_points.")
        name = "candidate_RHINO.hgeo.json"
    else:
        name = "candidate.hgeo.json"
    if name not in manifest["artifact_sha256"]:
        raise ValueError("Candidate geometry is not covered by the generation manifest.")
    path = directory / name
    return path, {"candidate_hash": manifest["candidate_hash"], "geometry_path": str(path),
                  "geometry_sha256": file_sha256(path), "representation": representation,
                  "generation_implementation_sha256": payload["identity"]["implementation_sha256"]}


def evaluate_models(mesh, hydro, config: StudyConfig) -> dict:
    """Return labelled model forces with explicit settings and omissions."""
    from ..resistance.holtrop import Holtrop1984
    from ..resistance.michell import OffsetHull, MichellThinShip
    cfg = config.resistance
    conditions = Conditions(tuple(config.speeds_kn), config.water.rho_kg_m3, config.water.nu_m2_s)
    hc, mc = cfg.holtrop, cfg.michell
    holtrop = Holtrop1984(include_transom=config.friction_include_transom,
                         gravity_m_s2=cfg.gravity_m_s2, entrance_angle_policy=hc.entrance_angle_policy,
                         entrance_half_angle_deg=hc.entrance_half_angle_deg,
                         entrance_angle_source=hc.entrance_angle_source,
                         lcb_reference_x_m=hc.lcb_reference_x_m,
                         roughness_m=hc.roughness_m, stern_parameter=hc.stern_parameter,
                         bulb_area_m2=hc.bulb_area_m2, bulb_height_m=hc.bulb_height_m).evaluate(hydro, conditions)
    offsets = OffsetHull.from_mesh(mesh, hydro.z_wl_m, nx=mc.nx, nz=mc.nz,
                                   symmetry_tolerance=mc.symmetry_tolerance,
                                   multi_interval_policy=mc.multi_interval_policy,
                                   bow_padding_fraction_lwl=mc.bow_padding_fraction_lwl)
    closure_length = mc.transom_closure_fraction_lwl * hydro.lwl_m
    wave = MichellThinShip(offsets, include_friction=False, gravity_m_s2=cfg.gravity_m_s2,
                          lambda_max=mc.lambda_max, quadrature_order=mc.quadrature_order,
                          transom_closure_length_m=closure_length,
                          convergence_tolerance=mc.convergence_tolerance,
                          check_convergence=mc.check_convergence,
                          lambda_max_limit=mc.lambda_max_limit).evaluate(hydro, conditions)
    convergence = wave.metadata["numerical_convergence"]
    outer_verified = all(item["converged"] for item in convergence) if mc.check_convergence else None
    if mc.check_convergence and not outer_verified:
        raise ValueError("Michell outer-integral convergence failed; increase lambda_max/quadrature_order before accepting this evaluation.")
    friction = ITTC57Friction(include_transom=config.friction_include_transom).evaluate(hydro, conditions)
    partial = RawResult(backend="PY-MICHELL-PLUS-ITTC57-PARTIAL",
                        label="Michell wave + ITTC-57 friction; partial model, omissions explicit",
                        quantity="wave_plus_friction_force", is_physical_prediction=True,
                        speeds_kn=conditions.speeds_kn,
                        values=tuple(a + b for a, b in zip(wave.values, friction.values)), units="N",
                        warnings=("Partial sum excludes viscous form resistance, empirical immersed-transom pressure, correlation/roughness, air and appendages.",
                                  "Virtual transom closure is a numerical assumption, not a validated Maxsurf model."),
                        metadata={"components_N": {"RW": list(wave.values), "RF": list(friction.values)},
                                  "wave_backend": wave.backend, "friction_backend": friction.backend,
                                  "virtual_closure_length_m": closure_length})
    return {"hydrostatics": hydro.to_dict(),
            "forces": {item.backend: item.to_dict() for item in (holtrop, wave, friction, partial)},
            "offsets": {"xs_m": offsets.xs_m.tolist(), "zs_m": offsets.zs_m.tolist(),
                        "halfbreadths_m": offsets.halfbreadths_m.tolist(),
                        "diagnostics": dict(offsets.diagnostics)},
            "assumptions": {"holtrop": hc.model_dump(), "michell": mc.model_dump(),
                            "friction_include_transom": config.friction_include_transom,
                            "water_status": config.water.status, "waterline_status": config.immersion.status,
                            "maxsurf_validated": False},
            "numerical_status": {"michell_outer_verified": outer_verified,
                                 "offset_grid_convergence_checked_in_this_run": False,
                                 "physical_validation": "pending"}}


def _ensure_comparison_constraints(config, baseline, candidate, mesh):
    from ..geometry.checks import check_candidate
    report = check_candidate(baseline, candidate, target_volume_m3=baseline.volume_m3,
                             reference_z_wl_m=config.immersion.reference_z_wl_m,
                             draft_change_limit_m=config.immersion.draft_change_limit_m,
                             displacement_tolerance_rel=config.immersion.displacement_tolerance_rel,
                             length_tolerance_rel=config.transformation.length_tolerance_rel,
                             breadth_tolerance_rel=config.transformation.breadth_tolerance_rel,
                             closure_bottom_z_m=config.checks.closure_bottom_z_m,
                             side_recess_bottom_z_m=config.checks.side_recess_bottom_z_m,
                             min_clearance_m=config.checks.min_clearance_m, candidate_mesh=mesh)
    if not report.passed:
        failures = ", ".join(item.name for item in report.checks if not item.passed)
        raise ResistanceGeometryRejected("Resistance geometry/immersion constraints failed: " + failures)
    return report.to_dict()


def per_speed_comparison(baseline: dict, candidate: dict) -> dict:
    rows = []
    for backend, first in baseline["forces"].items():
        other = candidate["forces"][backend]
        if first["speeds_kn"] != other["speeds_kn"]:
            raise ValueError("Comparison speed arrays differ.")
        changes = []
        for speed, a, b in zip(first["speeds_kn"], first["values"], other["values"]):
            change = 100 * (b / a - 1) if a > 0 else None
            changes.append(change)
            rows.append({"backend": backend, "speed_kn": speed, "baseline_force_N": a,
                         "candidate_force_N": b, "change_percent": change})
        if any(change is None for change in changes):
            continue
    return {"rows": rows, "interpretation": "Per-speed changes within each stated model; no chosen operating-profile objective or validated improvement claim."}


def _write_tables_and_plot(directory, payload):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    rows = payload["comparison"]["rows"] if payload["comparison"] is not None else [
        {"backend": name, "speed_kn": speed, "baseline_force_N": value,
         "candidate_force_N": "", "change_percent": ""}
        for name, force in payload["baseline"]["forces"].items()
        for speed, value in zip(force["speeds_kn"], force["values"])]
    with (directory / "resistance.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["backend", "speed_kn", "baseline_force_N", "candidate_force_N", "change_percent"])
        writer.writeheader()
        writer.writerows(rows)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout="constrained")
    titles = ["Holtrop empirical total", "Michell wave + ITTC friction (partial)"]
    names = ["PY-HOLTROP-1984", "PY-MICHELL-PLUS-ITTC57-PARTIAL"]
    for axis, name, title in zip(axes, names, titles):
        for item, label in ((payload["baseline"], "Baseline"), (payload["candidate"], "Candidate")):
            if item is None:
                continue
            force = item["forces"][name]
            axis.plot(force["speeds_kn"], np.asarray(force["values"]) / 1000, marker="o", label=label)
        axis.set(xlabel="Speed [kn]", ylabel="Model force [kN]", title=title)
        axis.grid(alpha=.25)
        axis.legend()
    fig.savefig(directory / "resistance_comparison.png", dpi=160)
    plt.close(fig)


def run_resistance(config: StudyConfig, *, candidate_directory: Path | None = None,
                   representation: str = "native", evaluation_id: str = "R0001_M4",
                   reuse: bool = False):
    from ..cli import implementation_sha256, _provenance, _write_json
    if config.scope != "bare_hull":
        raise ValueError("M4 comparison currently supports bare_hull only; explicit appendage factors are not configured.")
    if representation not in {"native", "control_points"}:
        raise ValueError("representation must be native or control_points.")
    if not evaluation_id or evaluation_id in {".", ".."} or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in evaluation_id):
        raise ValueError("evaluation_id must be a simple directory name.")
    hashes = config.input_hashes()
    candidate_path = None
    candidate_provenance = None
    if candidate_directory is not None:
        candidate_path, candidate_provenance = _verified_candidate_geometry(config, Path(candidate_directory), representation)
    code_hash = implementation_sha256()
    identity = {"kind": "resistance_comparison", "input_hashes": hashes,
                "study": config.model_dump(mode="json"), "candidate": candidate_provenance,
                "implementation_sha256": code_hash}
    key = candidate_hash(identity)
    directory = config.run_root / config.study_id / evaluation_id
    with ResultStore(config.database) as store:
        if directory.exists() or store.find_candidate(config.study_id, evaluation_id):
            if not reuse:
                raise FileExistsError("Resistance evaluation exists; use --reuse or a new --evaluation-id.")
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            expected = {"resistance.json", "resistance.csv", "resistance_comparison.png"}
            if set(manifest["artifact_sha256"]) != expected:
                raise ValueError("Resistance manifest does not cover the expected artifact set.")
            if manifest["candidate_hash"] != key:
                raise ValueError("Resistance identity changed; use a new --evaluation-id.")
            for name, digest in manifest["artifact_sha256"].items():
                if Path(name).name != name or file_sha256(directory / name) != digest:
                    raise ValueError(f"Resistance artifact changed: {name}")
            payload = json.loads((directory / "resistance.json").read_text(encoding="utf-8"))
            row = store.find_candidate(config.study_id, evaluation_id)
            if (row is None or row["status"] != "succeeded" or row["candidate_hash"] != key or
                    candidate_hash(payload["identity"]) != key):
                raise ValueError("Resistance evaluation lacks a matching successful database record.")
            if canonical_json(store.get_results(row["evaluation_id"])) != canonical_json({"M4-RESISTANCE": payload}):
                raise ValueError("Resistance database results differ from artifacts.")
            return directory, payload
        record = EvaluationRecord(config.study_id, evaluation_id, key, identity)
        stored_id = store.create_evaluation(record)
        store.transition(stored_id, "running")
        record.transition("running")
        started = time.perf_counter()
        try:
            directory.mkdir(parents=True, exist_ok=False)
            geometry = load_hgeo(config.baseline.geometry_dump, layers=config.baseline.hull_layers)
            mesh = tessellate(geometry, **config.tessellation.model_dump())
            hydro = measure(mesh, config.immersion.reference_z_wl_m, rho_kg_m3=config.water.rho_kg_m3,
                            section_count=config.section_count)
            baseline_checks = _ensure_comparison_constraints(config, hydro, hydro, mesh)
            baseline_result = evaluate_models(mesh, hydro, config)
            baseline_result["geometry_checks"] = baseline_checks
            candidate_result = None
            if candidate_path is not None:
                candidate_geometry = load_hgeo(candidate_path, layers=config.baseline.hull_layers)
                candidate_mesh = tessellate(candidate_geometry, **config.tessellation.model_dump())
                balance = solve_equilibrium(candidate_mesh, hydro.volume_m3, config.immersion.reference_z_wl_m,
                                            draft_change_limit_m=config.immersion.draft_change_limit_m,
                                            rho_kg_m3=config.water.rho_kg_m3, section_count=config.section_count)
                candidate_checks = _ensure_comparison_constraints(config, hydro, balance.hydrostatics, candidate_mesh)
                candidate_result = evaluate_models(candidate_mesh, balance.hydrostatics, config)
                candidate_result["geometry_checks"] = candidate_checks
            comparison = per_speed_comparison(baseline_result, candidate_result) if candidate_result else None
            if hashes != config.input_hashes() or code_hash != implementation_sha256():
                raise RuntimeError("Study or implementation changed during resistance evaluation.")
            if candidate_path is not None and file_sha256(candidate_path) != candidate_provenance["geometry_sha256"]:
                raise RuntimeError("Candidate geometry changed during resistance evaluation.")
            record.transition("succeeded")
            payload = {"schema": "hullopt-resistance-1", "identity": identity, "record": record.to_dict(),
                       "provenance": _provenance(config, hashes), "baseline": baseline_result,
                       "candidate": candidate_result, "comparison": comparison,
                       "timing_total_s": time.perf_counter() - started,
                       "validation_status": "Model calculation with explicit numerical diagnostics; Maxsurf/experimental calibration is pending."}
            _write_json(directory / "resistance.json", payload)
            _write_tables_and_plot(directory, payload)
            _write_json(directory / "manifest.json", {"candidate_hash": key, "artifact_sha256": {
                path.name: file_sha256(path) for path in directory.iterdir() if path.is_file()}})
            store.transition(stored_id, "succeeded", results={"M4-RESISTANCE": payload})
            return directory, payload
        except Exception as exc:
            row = store.find_candidate(config.study_id, evaluation_id)
            if row and row["status"] == "running":
                reason = f"{type(exc).__name__}: {exc}"
                status = "rejected" if isinstance(exc, ResistanceGeometryRejected) else "failed"
                store.transition(stored_id, status, reason=reason)
                if directory.exists():
                    _write_json(directory / ("rejection.json" if status == "rejected" else "failure.json"),
                                {"status": status, "reason": reason, "identity": identity})
            raise
