"""Measure a study, solve equilibrium, or generate checked shape candidates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import StudyConfig, file_sha256, load_study
from .evaluation.record import EvaluationRecord, candidate_hash, canonical_json, utc_now
from .resistance import Conditions, ITTC57Friction, MockBackend
from .store import ResultStore


def implementation_sha256() -> str:
    """Include numerical implementation changes even before a version bump."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _identity(config: StudyConfig, hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "baseline", "parameters": {}, "generator": "unchanged-baseline",
        "package_version": __version__, "implementation_sha256": implementation_sha256(),
        "input_hashes": hashes, "study": config.model_dump(mode="json"),
    }


def _provenance(config: StudyConfig, hashes: dict[str, str]) -> dict[str, Any]:
    versions = {}
    for name in ("numpy", "scipy", "matplotlib", "pydantic", "pyyaml"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unknown"
    return {
        "created_at_utc": utc_now(), "package_version": __version__,
        "implementation_sha256": implementation_sha256(),
        "python_version": sys.version, "platform": platform.platform(), "dependencies": versions,
        "input_paths": {"study_yaml": str(config.config_path),
                        "geometry_dump": str(config.baseline.geometry_dump),
                        "source_3dm": str(config.baseline.source_3dm)},
        "input_sha256": hashes, "selected_layers": config.selected_layers,
        "study_settings": config.model_dump(mode="json"),
        "assumptions": [
            "Zero static trim; x forward, z upward; SI units.",
            f"Reference waterline status: {config.immersion.status}; water-property status: {config.water.status}.",
            "The baseline target is the selected geometry's measured immersed volume at the reference waterline.",
            "appendage_volume_m3 is metadata for mapping a later total-volume target; it is not subtracted twice.",
            "ITTC57 is a friction component only; MOCK has no hydrodynamic interpretation.",
        ],
    }


def _build_mesh(config: StudyConfig) -> tuple[Any, dict[str, float]]:
    from .geometry.hgeo import load_hgeo
    from .geometry.tessellate import tessellate

    start = time.perf_counter()
    geometry = load_hgeo(config.baseline.geometry_dump, layers=config.selected_layers)
    loaded = time.perf_counter()
    mesh = tessellate(geometry, **config.tessellation.model_dump())
    end = time.perf_counter()
    return mesh, {"geometry_load_s": loaded - start, "tessellation_s": end - loaded}


def _measure(mesh: Any, config: StudyConfig, z_wl_m: float) -> Any:
    from .hydro.hydrostatics import measure

    return measure(mesh, z_wl_m, rho_kg_m3=config.water.rho_kg_m3,
                   section_count=config.section_count, include_sections=True)


def _write_json(path: Path, payload: Any) -> None:
    """Exclusive create prevents accidental replacement of a previous result."""
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text + "\n")


def _save_sac(path: Path, hydro: Any) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, axis = plt.subplots(figsize=(9, 4.5), layout="constrained")
    axis.plot(hydro.section_x_m, hydro.section_area_m2, linewidth=1.7)
    axis.set(xlabel="Longitudinal coordinate x [m] (+ forward)",
             ylabel="Immersed section area [m²]",
             title="Baseline sectional area curve — zero static trim")
    axis.grid(alpha=0.25)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _verified_reuse(config: StudyConfig, directory: Path, expected_hash: str, store: ResultStore,
                    candidate_id: str) -> dict[str, Any]:
    with (directory / "manifest.json").open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest["candidate_hash"] != expected_hash:
        raise ValueError("Existing baseline identity differs from the current inputs/settings/implementation")
    for filename in ("measurements.json", "SAC.png"):
        if file_sha256(directory / filename) != manifest["artifact_sha256"][filename]:
            raise ValueError(f"Existing baseline artifact changed: {filename}")
    with (directory / "measurements.json").open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if candidate_hash(payload["identity"]) != expected_hash or payload["record"]["status"] != "succeeded":
        raise ValueError("Existing measurements do not match a successful evaluation")
    row = store.find_candidate(config.study_id, candidate_id)
    if row is None or row["candidate_hash"] != expected_hash or row["status"] != "succeeded":
        raise ValueError("Existing baseline has no matching successful SQLite evaluation")
    expected_results = {"HYDROSTATICS": payload["hydrostatics"],
                        payload["friction_component"]["backend"]: payload["friction_component"],
                        "MOCK": payload["software_test"]}
    if canonical_json(store.get_results(row["evaluation_id"])) != canonical_json(expected_results):
        raise ValueError("Stored SQLite results differ from the baseline artifacts")
    return payload


def run_baseline(config: StudyConfig, *, reuse: bool = False,
                 candidate_id: str = "C0000_baseline") -> tuple[Path, dict[str, Any]]:
    """One immutable baseline directory, with explicit verified reuse support."""
    if not candidate_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in candidate_id):
        raise ValueError("candidate_id must be a simple directory name")
    if candidate_id in {".", ".."}:
        raise ValueError("candidate_id must not refer to a parent directory")
    total_start = time.perf_counter()
    hashes = config.input_hashes()
    identity = _identity(config, hashes)
    key = candidate_hash(identity)
    directory = config.run_root / config.study_id / candidate_id
    with ResultStore(config.database) as store:
        if directory.exists():
            if not reuse:
                raise FileExistsError(f"Baseline directory already exists: {directory}. Use --reuse to verify and reuse it, or a new --candidate-id.")
            return directory, _verified_reuse(config, directory, key, store, candidate_id)
        if store.find_candidate(config.study_id, candidate_id):
            raise FileExistsError("Candidate already exists in SQLite; use a new --candidate-id")
        record = EvaluationRecord(config.study_id, candidate_id, key, identity)
        evaluation_id = store.create_evaluation(record)
        directory.mkdir(parents=True, exist_ok=False)
        store.transition(evaluation_id, "running")
        record.transition("running")
        try:
            mesh, timings = _build_mesh(config)
            start = time.perf_counter()
            hydro = _measure(mesh, config, config.immersion.reference_z_wl_m)
            timings["hydrostatics_with_sections_s"] = time.perf_counter() - start
            z_wl = config.immersion.reference_z_wl_m
            clearances = {
                "closure_clearance_m": (config.checks.closure_bottom_z_m - z_wl
                                        if config.checks.closure_bottom_z_m is not None else None),
                "side_recess_clearance_m": (config.checks.side_recess_bottom_z_m - z_wl
                                            if config.checks.side_recess_bottom_z_m is not None else None),
                "minimum_required_m": config.checks.min_clearance_m,
            }
            violations = [name for name in ("closure_clearance_m", "side_recess_clearance_m")
                          if clearances[name] is not None and clearances[name] < config.checks.min_clearance_m]
            if violations:
                reason = "Physical clearance constraint violated: " + ", ".join(violations)
                store.transition(evaluation_id, "rejected", reason=reason)
                record.transition("rejected", reason=reason)
                _write_json(directory / "rejection.json", {"record": record.to_dict(), "clearances": clearances})
                raise ValueError(reason)
            conditions = Conditions(tuple(config.speeds_kn), config.water.rho_kg_m3, config.water.nu_m2_s)
            friction = ITTC57Friction(include_transom=config.friction_include_transom).evaluate(hydro, conditions)
            mock = MockBackend().evaluate(hydro, conditions)
            plot_start = time.perf_counter()
            _save_sac(directory / "SAC.png", hydro)
            timings["sac_plot_s"] = time.perf_counter() - plot_start
            timings["total_before_persistence_s"] = time.perf_counter() - total_start
            if config.input_hashes() != hashes or implementation_sha256() != identity["implementation_sha256"]:
                raise ValueError("Study input or implementation changed while the baseline was running; rerun with stable inputs")
            record.transition("succeeded")
            payload = {
                "schema": "hullopt-baseline-1", "record": record.to_dict(), "identity": identity,
                "provenance": _provenance(config, hashes), "hydrostatics": hydro.to_dict(),
                "mesh": {"triangle_count": len(mesh.triangles), "surface_area_m2": mesh.area_m2,
                         "metadata": getattr(mesh, "metadata", {})},
                "clearance_checks": clearances, "timings": timings,
                "friction_component": friction.to_dict(), "software_test": mock.to_dict(),
            }
            _write_json(directory / "measurements.json", payload)
            manifest = {"candidate_hash": key, "artifact_sha256": {
                filename: file_sha256(directory / filename) for filename in ("measurements.json", "SAC.png")}}
            _write_json(directory / "manifest.json", manifest)
            store.transition(evaluation_id, "succeeded", results={
                "HYDROSTATICS": hydro.to_dict(), friction.backend: friction.to_dict(), "MOCK": mock.to_dict()})
            return directory, payload
        except Exception as exc:
            row = store.find_candidate(config.study_id, candidate_id)
            if row and row["status"] == "running":
                reason = f"{type(exc).__name__}: {exc}"
                store.transition(evaluation_id, "failed", reason=reason)
                # A successful local record may have been serialized before a persistence failure.
                failure = {"evaluation_id": evaluation_id, "candidate_hash": key, "status": "failed", "reason": reason}
                _write_json(directory / "failure.json", failure)
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hullopt", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    measure_parser = subparsers.add_parser("measure", help="Hydrostatics at the configured or supplied waterline")
    measure_parser.add_argument("study")
    measure_parser.add_argument("--z", type=float)
    measure_parser.add_argument("--output", type=Path)
    equilibrium_parser = subparsers.add_parser("equilibrium", help="Solve waterline at a target immersed volume")
    equilibrium_parser.add_argument("study")
    equilibrium_parser.add_argument("--volume", type=float, required=True, help="Target volume of selected layers [m³]")
    equilibrium_parser.add_argument("--output", type=Path)
    baseline_parser = subparsers.add_parser("baseline", help="Create an immutable baseline report, SAC, and SQLite record")
    baseline_parser.add_argument("study")
    baseline_parser.add_argument("--reuse", action="store_true", help="Verify and reuse a matching completed baseline")
    baseline_parser.add_argument("--candidate-id", default="C0000_baseline")
    transform_parser = subparsers.add_parser("transform", help="Generate a fixed-volume Cp/LCB geometric candidate")
    transform_parser.add_argument("study")
    transform_parser.add_argument("--dcp", type=float, default=0.01, help="Absolute Cp change from baseline")
    transform_parser.add_argument("--dlcb-frac", type=float, default=0.0,
                                  help="LCB shift / baseline LWL, positive forward")
    transform_parser.add_argument("--candidate-id", default="C0001_lackenby")
    transform_parser.add_argument("--reuse", action="store_true")
    transform_parser.add_argument("--export-3dm", action="store_true",
                                  help="Also export and independently remeasure a native Rhino point morph")
    resistance_parser = subparsers.add_parser("resistance", help="Evaluate labelled Holtrop/Michell models and optionally compare a candidate")
    resistance_parser.add_argument("study")
    resistance_parser.add_argument("--candidate-dir", type=Path)
    resistance_parser.add_argument("--representation", choices=("native", "control_points"), default="native")
    resistance_parser.add_argument("--evaluation-id", default="R0001_M4")
    resistance_parser.add_argument("--reuse", action="store_true")
    subparsers.add_parser("serve", help="JSON-RPC 2.0 service on stdin/stdout (used by the Rhino plug-in)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from .service import serve
        return serve()
    try:
        config = load_study(args.study)
        if args.command == "baseline":
            directory, payload = run_baseline(config, reuse=args.reuse, candidate_id=args.candidate_id)
            print(json.dumps({"directory": str(directory), "candidate_hash": payload["record"]["candidate_hash"],
                              "status": payload["record"]["status"], "hydrostatics": payload["hydrostatics"],
                              "timings": payload["timings"]}, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == "transform":
            from .evaluation.candidate import run_candidate
            directory, payload = run_candidate(config, args.dcp, args.dlcb_frac,
                                               candidate_id=args.candidate_id, reuse=args.reuse,
                                               export_3dm=args.export_3dm)
            print(json.dumps({"directory": str(directory), "status": payload["record"]["status"],
                              "candidate_hash": payload["record"]["candidate_hash"],
                              "targets": payload["transformation"]["targets"],
                              "residuals": payload["transformation"]["residuals"],
                              "z_wl_m": payload["transformation"]["hydrostatics"]["z_wl_m"],
                              "checks_passed": payload["checks"]["passed"], "timings": payload["timings"]},
                             indent=2, ensure_ascii=False, allow_nan=False))
            return 0
        if args.command == "resistance":
            from .evaluation.resistance import run_resistance
            directory, payload = run_resistance(config, candidate_directory=args.candidate_dir,
                                                representation=args.representation, evaluation_id=args.evaluation_id,
                                                reuse=args.reuse)
            print(json.dumps({"directory": str(directory), "status": payload["record"]["status"],
                              "backends": list(payload["baseline"]["forces"]),
                              "comparison": payload["comparison"],
                              "validation_status": payload["validation_status"],
                              "timing_total_s": payload["timing_total_s"]},
                             indent=2, ensure_ascii=False, allow_nan=False))
            return 0
        hashes = config.input_hashes()
        mesh, timings = _build_mesh(config)
        start = time.perf_counter()
        if args.command == "measure":
            z_wl_m = config.immersion.reference_z_wl_m if args.z is None else args.z
            result = _measure(mesh, config, z_wl_m)
        else:
            from .hydro.equilibrium import solve_equilibrium
            result = solve_equilibrium(mesh, args.volume, config.immersion.reference_z_wl_m,
                                       draft_change_limit_m=config.immersion.draft_change_limit_m,
                                       relative_tolerance=min(config.immersion.displacement_tolerance_rel, 1e-6),
                                       rho_kg_m3=config.water.rho_kg_m3, section_count=config.section_count)
        timings[args.command + "_s"] = time.perf_counter() - start
        payload = {"result": result.to_dict(), "provenance": _provenance(config, hashes), "timings": timings}
        if args.output:
            _write_json(args.output, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"hullopt: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
