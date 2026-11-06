"""Persist an independently generated and checked M3 shape candidate."""

from __future__ import annotations

import json
from pathlib import Path
import time

from ..config import StudyConfig, file_sha256
from .record import EvaluationRecord, candidate_hash, canonical_json
from ..store import ResultStore


def _comparison_plot(path, baseline, candidate):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), layout="constrained")
    for hydro, label in ((baseline, "Baseline"), (candidate, "Candidate")):
        axes[0].plot(hydro.section_x_m, hydro.section_area_m2, label=label)
    axes[0].set(xlabel="Longitudinal coordinate x [m] (+ forward)", ylabel="Immersed sectional area [m²]",
                title="SAC at each equilibrium waterline")
    axes[0].legend()
    axes[1].plot(baseline.section_x_m, baseline.section_area_m2 / baseline.max_section_area_m2,
                 label="Baseline")
    axes[1].plot(candidate.section_x_m, candidate.section_area_m2 / candidate.max_section_area_m2,
                 label="Candidate")
    axes[1].set(xlabel="Longitudinal coordinate x [m] (+ forward)", ylabel="A(x) / maximum A [-]",
                title="Normalized sectional area")
    axes[1].legend()
    for axis in axes:
        axis.grid(alpha=.25)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _section_plot(path, baseline_mesh, candidate_mesh, baseline, candidate):
    from matplotlib import pyplot as plt
    from matplotlib.collections import LineCollection
    from ..hydro.sections import plane_segments
    fractions = (.1, .3, .5, .7, .9)
    fig, axes = plt.subplots(1, 5, figsize=(15, 4), layout="constrained", sharex=True, sharey=True)
    for fraction, axis in zip(fractions, axes):
        x = baseline.x_aft_m + fraction * baseline.lwl_m
        for mesh, color, label in ((baseline_mesh, "C0", "Baseline"), (candidate_mesh, "C1", "Candidate")):
            segments, _ = plane_segments(mesh.triangles, x, 0)
            axis.add_collection(LineCollection(segments[:, :, 1:], colors=color, linewidths=.9, label=label))
        axis.axhline(baseline.z_wl_m, color="C0", linestyle="--", linewidth=.8)
        axis.axhline(candidate.z_wl_m, color="C1", linestyle="--", linewidth=.8)
        axis.autoscale()
        axis.set(xlabel="y [m]", title=f"x = {x:.2f} m")
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=.2)
    axes[0].set_ylabel("Model z [m]")
    axes[0].legend(fontsize=8)
    fig.suptitle("Section comparison at common model x; dashed lines = equilibrium waterlines")
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run_candidate(config: StudyConfig, delta_cp: float, delta_lcb_fraction_lwl: float, *,
                  candidate_id="C0001_lackenby", reuse=False, export_3dm=False):
    from ..cli import implementation_sha256, _provenance, _write_json
    from ..geometry import load_hgeo
    from ..geometry.checks import check_candidate
    from ..geometry.export import write_hgeo, write_binary_stl
    from ..optimize.lackenby import prepare_lackenby, TargetInfeasibleError
    from ..resistance import Conditions, ITTC57Friction

    if not candidate_id or candidate_id in {".", ".."} or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in candidate_id):
        raise ValueError("candidate_id must be a simple directory name")
    if config.scope != "bare_hull":
        raise ValueError("M3 transformation currently requires scope: bare_hull; appendage fit is a later verification step.")
    parameters = {"dCp": delta_cp, "dLCB_frac_LWL": delta_lcb_fraction_lwl}
    # YAML's adopted study variable bounds govern every entry point.
    for variable in config.variables:
        if variable.name in parameters and not variable.min <= parameters[variable.name] <= variable.max:
            raise TargetInfeasibleError(f"{variable.name} outside configured bounds [{variable.min}, {variable.max}].")
    hashes = config.input_hashes()
    source_hash = implementation_sha256()
    identity = dict(kind="candidate", parameters=parameters, generator="PY-LACKENBY-CONTROL-POINTS-1",
                    package_version=_provenance(config, hashes)["package_version"],
                    implementation_sha256=source_hash, input_hashes=hashes,
                    study=config.model_dump(mode="json"), export_3dm=bool(export_3dm))
    if export_3dm:
        helper = Path(__file__).resolve().parents[2] / "tools" / "rhino_export_candidate.py"
        identity["native_helper_sha256"] = file_sha256(helper)
    key = candidate_hash(identity)
    directory = config.run_root / config.study_id / candidate_id
    with ResultStore(config.database) as store:
        if directory.exists() or store.find_candidate(config.study_id, candidate_id):
            if not reuse:
                raise FileExistsError(f"Candidate already exists: {directory}. Use --reuse or a new --candidate-id.")
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if manifest["candidate_hash"] != key:
                raise ValueError("Candidate identity changed; use a new --candidate-id.")
            for filename, digest in manifest["artifact_sha256"].items():
                if file_sha256(directory / filename) != digest:
                    raise ValueError(f"Candidate artifact changed: {filename}")
            payload = json.loads((directory / "measurements.json").read_text(encoding="utf-8"))
            row = store.find_candidate(config.study_id, candidate_id)
            if (row is None or row["status"] != "succeeded" or row["candidate_hash"] != key or
                    candidate_hash(payload["identity"]) != key):
                raise ValueError("No matching successful candidate evaluation for reuse.")
            expected = {"PY-LACKENBY": payload["transformation"], "GEOMETRY-CHECKS": payload["checks"],
                        payload["friction_component"]["backend"]: payload["friction_component"]}
            if canonical_json(store.get_results(row["evaluation_id"])) != canonical_json(expected):
                raise ValueError("Candidate database results differ from artifacts.")
            return directory, payload
        record = EvaluationRecord(config.study_id, candidate_id, key, identity)
        evaluation_id = store.create_evaluation(record)
        store.transition(evaluation_id, "running")
        record.transition("running")
        started = time.perf_counter()
        try:
            directory.mkdir(parents=True, exist_ok=False)
            geometry = load_hgeo(config.baseline.geometry_dump, layers=config.baseline.hull_layers)
            prepared = prepare_lackenby(geometry, config.immersion.reference_z_wl_m,
                                        tessellation_options=config.tessellation.model_dump(),
                                        rho_kg_m3=config.water.rho_kg_m3, section_count=config.section_count,
                                        layers=tuple(config.baseline.hull_layers))
            timings = {"prepare_geometry_and_nurbs_modes_s": time.perf_counter() - started}
            solve_started = time.perf_counter()
            transform = config.transformation
            ceiling = config.checks.waterline_ceiling_m
            result = prepared.solve(delta_cp, delta_lcb_fraction_lwl,
                                    draft_change_limit_m=config.immersion.draft_change_limit_m,
                                    volume_tolerance_rel=transform.volume_tolerance_rel,
                                    cp_tolerance_abs=transform.cp_tolerance_abs,
                                    lcb_tolerance_fraction_lwl=transform.lcb_tolerance_fraction_lwl,
                                    maximum_z_wl_m=ceiling, max_function_evaluations=transform.max_function_evaluations,
                                    minimum_map_derivative=transform.minimum_map_derivative)
            timings["target_solve_and_final_remesh_s"] = time.perf_counter() - solve_started
            checks = check_candidate(result.baseline, result.hydrostatics,
                                     target_volume_m3=result.targets["volume_m3"],
                                     reference_z_wl_m=config.immersion.reference_z_wl_m,
                                     draft_change_limit_m=config.immersion.draft_change_limit_m,
                                     displacement_tolerance_rel=config.immersion.displacement_tolerance_rel,
                                     length_tolerance_rel=transform.length_tolerance_rel,
                                     breadth_tolerance_rel=transform.breadth_tolerance_rel,
                                     closure_bottom_z_m=config.checks.closure_bottom_z_m,
                                     side_recess_bottom_z_m=config.checks.side_recess_bottom_z_m,
                                     min_clearance_m=config.checks.min_clearance_m,
                                     target_cp=result.targets["cp"], cp_tolerance_abs=transform.cp_tolerance_abs,
                                     target_lcb_m=result.targets["lcb_m"],
                                     lcb_tolerance_m=transform.lcb_tolerance_fraction_lwl * result.baseline.lwl_m,
                                     xmap_result=result.xmap, baseline_mesh=prepared.mesh,
                                     candidate_mesh=result.mesh, same_connectivity=True)
            write_hgeo(result.geometry, directory / "candidate.hgeo.json")
            write_binary_stl(result.mesh, directory / "candidate_m.stl")
            _comparison_plot(directory / "SAC_comparison.png", result.baseline, result.hydrostatics)
            _section_plot(directory / "sections_comparison.png", prepared.mesh, result.mesh,
                          result.baseline, result.hydrostatics)
            if not checks.passed:
                reason = ", ".join(check.name for check in checks.checks if not check.passed)
                raise TargetInfeasibleError(f"Candidate failed engineering checks: {reason}")
            cad_report = None
            if export_3dm:
                from ..geometry.export import export_3dm as export_native
                cad_report = export_native(config.baseline.source_3dm, directory / "candidate_RHINO.3dm", result.xmap,
                                           layers=tuple(config.baseline.hull_layers), cv_geometry=result.geometry,
                                           expected_source_sha256=config.baseline.source_sha256)
                from ..hydro import solve_equilibrium
                from ..geometry import tessellate
                native_geometry = load_hgeo(cad_report["hgeo_path"], layers=config.baseline.hull_layers)
                native_mesh = tessellate(native_geometry, **config.tessellation.model_dump())
                native_balance = solve_equilibrium(native_mesh, result.targets["volume_m3"],
                                                   config.immersion.reference_z_wl_m,
                                                   draft_change_limit_m=config.immersion.draft_change_limit_m,
                                                   rho_kg_m3=config.water.rho_kg_m3,
                                                   section_count=config.section_count)
                native_checks = check_candidate(result.baseline, native_balance.hydrostatics,
                    target_volume_m3=result.targets["volume_m3"], reference_z_wl_m=config.immersion.reference_z_wl_m,
                    draft_change_limit_m=config.immersion.draft_change_limit_m,
                    displacement_tolerance_rel=config.immersion.displacement_tolerance_rel,
                    length_tolerance_rel=transform.length_tolerance_rel, breadth_tolerance_rel=transform.breadth_tolerance_rel,
                    closure_bottom_z_m=config.checks.closure_bottom_z_m,
                    side_recess_bottom_z_m=config.checks.side_recess_bottom_z_m, min_clearance_m=config.checks.min_clearance_m,
                    target_cp=result.targets["cp"], cp_tolerance_abs=result.targets["cp"] * .002,
                    target_lcb_m=result.targets["lcb_m"], lcb_tolerance_m=result.baseline.lwl_m * .002,
                    candidate_mesh=native_mesh)
                cad_report["equilibrium_remeasurement"] = native_balance.to_dict()
                cad_report["engineering_checks"] = native_checks.to_dict()
                cad_report["maxsurf_import_verified"] = False
                native_wl = native_balance.z_wl_m
                underwater_naked = [edge for edge in cad_report.get("joined_naked_edges", [])
                                    if edge["minimum_z_m"] < native_wl - cad_report["tolerance_m"]]
                cad_report["underwater_naked_edge_count"] = len(underwater_naked)
                if underwater_naked:
                    _write_json(directory / "native_validation_failure.json", cad_report)
                    raise TargetInfeasibleError("Native CAD has unmatched edges below its equilibrium waterline.")
                if not native_checks.passed:
                    _write_json(directory / "native_validation_failure.json", cad_report)
                    raise TargetInfeasibleError("Native CAD candidate differs from requested targets or fails geometry checks.")
            friction = ITTC57Friction(include_transom=config.friction_include_transom).evaluate(
                result.hydrostatics, Conditions(tuple(config.speeds_kn), config.water.rho_kg_m3, config.water.nu_m2_s))
            if config.input_hashes() != hashes or implementation_sha256() != source_hash:
                raise RuntimeError("Inputs or implementation changed during generation; retry with stable files.")
            if export_3dm and file_sha256(helper) != identity["native_helper_sha256"]:
                raise RuntimeError("Native helper changed during generation; retry with stable files.")
            record.transition("succeeded")
            timings["total_s"] = time.perf_counter() - started
            payload = dict(schema="hullopt-candidate-1", record=record.to_dict(), identity=identity,
                           provenance=_provenance(config, hashes), transformation=result.to_dict(),
                           checks=checks.to_dict(), native_cad=cad_report, timings=timings,
                           friction_component=friction.to_dict(),
                           interpretation="Geometric candidate at fixed volume. No total-resistance improvement has been assessed.")
            _write_json(directory / "measurements.json", payload)
            files = [path for path in directory.iterdir() if path.is_file()]
            _write_json(directory / "manifest.json", dict(candidate_hash=key, artifact_sha256={
                path.name: file_sha256(path) for path in files}))
            store.transition(evaluation_id, "succeeded", results={"PY-LACKENBY": result.to_dict(),
                             "GEOMETRY-CHECKS": checks.to_dict(), friction.backend: friction.to_dict()})
            return directory, payload
        except Exception as exc:
            physical_rejection = isinstance(exc, TargetInfeasibleError)
            status = "rejected" if physical_rejection else "failed"
            reason = f"{type(exc).__name__}: {exc}"
            row = store.find_candidate(config.study_id, candidate_id)
            if row and row["status"] == "running":
                store.transition(evaluation_id, status, reason=reason)
                if directory.exists():
                    _write_json(directory / ("rejection.json" if physical_rejection else "failure.json"),
                                dict(status=status, reason=reason, identity=identity,
                                     checks=checks.to_dict() if "checks" in locals() else None))
            raise
