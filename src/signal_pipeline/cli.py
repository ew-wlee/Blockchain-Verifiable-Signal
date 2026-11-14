"""Command-line orchestration for the frozen Step 1 and Step 2 pipelines."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .config import Step1Config, frozen_config_payload, load_config
from .data import (
    DataError,
    clean_records,
    prepare_raw_cache,
    read_cached_records,
    write_dataset_and_quality,
)
from .hashing import (
    read_json,
    sha256_file,
    write_csv_frame,
    write_json,
)
from .inference import (
    PREDICTION_COLUMNS,
    build_prediction_windows,
    load_dataset,
    run_inference,
)
from .model import (
    ModelError,
    resolve_snapshot,
    verify_snapshot,
    write_model_reference,
)
from .runtime import (
    block_network,
    code_provenance,
    dependency_versions,
    runtime_metadata,
    utc_now_iso,
)
from .signals import (
    SIGNAL_COLUMNS,
    SignalError,
    freeze_tau,
    generate_signals,
    signal_counts,
)
from .step2 import (
    build_step2,
    load_step2_config,
    reproduce_step2,
    status_step2,
    verify_step2,
)
from .validation import (
    DETERMINISTIC_ARTIFACTS,
    ValidationError,
    build_validation_report,
    verify_artifact_manifest,
    write_artifact_manifest,
    write_validation_report,
)

LOGGER = logging.getLogger("signal_pipeline")
RUN_RECORD_SCHEMA_VERSION = "step1-run-record-v1"
REPRODUCIBILITY_SCHEMA_VERSION = "step1-reproducibility-v1"


class PipelineError(RuntimeError):
    """Raised for orchestration-level failures."""


def _artifact_root(config: Step1Config) -> Path:
    return config.project_root / "artifacts" / "step1"


def _replay_root(config: Step1Config) -> Path:
    return config.project_root / "artifacts" / "replay" / "step1"


def _prepare_empty_output(root: Path) -> None:
    if root.exists() and any(root.iterdir()):
        raise PipelineError(
            f"formal output already exists; refusing to overwrite it: {root}"
        )
    (root / "reports").mkdir(parents=True, exist_ok=True)


def _expected_prediction_count(config: Step1Config) -> int:
    return (
        config.validation.hours + config.test.hours
    ) * len(config.symbols)


def _write_failure_record(
    root: Path,
    record: dict[str, Any],
    *,
    stage: str,
    error: BaseException,
    started_at: str,
    started_perf: float,
) -> None:
    record.update(
        {
            "status": "failed",
            "step1_completed": False,
            "failed_stage": stage,
            "failure": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "finished_at_utc": utc_now_iso(),
            "total_elapsed_seconds": round(time.perf_counter() - started_perf, 3),
            "blockers": [f"{stage}: {type(error).__name__}: {error}"],
        }
    )
    record["started_at_utc"] = started_at
    write_json(
        root / "reports" / "run_record.json",
        record,
        overwrite=True,
    )


def generate_pipeline(
    config: Step1Config,
    output_root: Path,
    *,
    offline: bool,
    http_timeout: float = 30.0,
) -> dict[str, Any]:
    """Generate the formal Step 1 artifacts exactly once into an empty directory."""
    provenance = code_provenance(config.project_root)
    _prepare_empty_output(output_root)
    started_at = utc_now_iso()
    started_perf = time.perf_counter()
    timings: dict[str, float] = {}
    record: dict[str, Any] = {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "status": "running",
        "step1_completed": False,
        "started_at_utc": started_at,
        "code_commit": provenance.commit,
        "tracked_tree_clean": provenance.tracked_tree_clean,
        "offline_requested": offline,
        "runtime": runtime_metadata(),
        "dependencies": dependency_versions(),
        "config_path": config.config_path.relative_to(config.project_root).as_posix(),
        "output_dir": output_root.relative_to(config.project_root).as_posix(),
        "stages": {},
        "blockers": [],
    }
    stage = "startup"

    def run_stage(name: str) -> float:
        nonlocal stage
        stage = name
        LOGGER.info("stage=%s", name)
        return time.perf_counter()

    try:
        guard = block_network() if offline else nullcontext()
        with guard:
            started = run_stage("data_cache")
            cache_status = prepare_raw_cache(
                config,
                offline=offline,
                timeout=http_timeout,
            )
            timings["data_cache_seconds"] = round(time.perf_counter() - started, 3)
            record["cache"] = {
                "cache_hit": cache_status.cache_hit,
                "network_requests": cache_status.network_requests,
                "chunk_count": len(cache_status.chunks),
                "cached_rows": cache_status.expected_rows,
                "endpoints": list(cache_status.endpoints_used),
            }

            started = run_stage("dataset")
            _, dataset_info = write_dataset_and_quality(config, output_root, cache_status)
            timings["dataset_seconds"] = round(time.perf_counter() - started, 3)
            record["dataset"] = {"rows": dataset_info["rows"], "symbols": dataset_info["symbols"]}

            started = run_stage("model_resolution")
            snapshot = resolve_snapshot(config, offline=offline)
            before = verify_snapshot(snapshot)
            timings["model_resolution_seconds"] = round(
                time.perf_counter() - started, 3
            )
            record["model"] = {
                "model_id": snapshot.model_id,
                "resolved_revision": snapshot.resolved_revision,
                "snapshot_path": snapshot.relative_path,
                "checkpoint_commitment_sha256": snapshot.commitment_sha256,
                "files_verified_before_load": before["files_match"],
            }

            started = run_stage("inference")
            dataset = load_dataset(output_root / "dataset.csv")
            windows = build_prediction_windows(dataset, config)
            expected_predictions = _expected_prediction_count(config)
            if len(windows) != expected_predictions:
                raise PipelineError(
                    f"prediction plan has {len(windows)} windows, "
                    f"expected {expected_predictions}"
                )
            inference = run_inference(windows, config, snapshot)
            write_csv_frame(
                inference.predictions,
                output_root / "predictions.csv",
                PREDICTION_COLUMNS,
            )
            after = verify_snapshot(snapshot)
            if after["checkpoint_commitment_sha256"] != snapshot.commitment_sha256:
                raise ModelError("checkpoint commitment changed during inference")
            write_model_reference(output_root, snapshot, config)
            timings["inference_seconds"] = round(time.perf_counter() - started, 3)
            record["inference"] = {
                "rows": len(inference.predictions),
                "batch_count": inference.batch_count,
                "batch_size": config.batch_size,
                "pipeline_class": inference.pipeline_class,
                "files_verified_after_load": after["files_match"],
            }

            started = run_stage("tau_freeze")
            test_rows = int((inference.predictions["split"] == "test").sum())
            expected_test_rows = config.test.hours * len(config.symbols)
            if test_rows != expected_test_rows:
                raise SignalError(
                    f"test prediction count mismatch: {test_rows} != {expected_test_rows}"
                )
            validation_only = inference.predictions.loc[
                inference.predictions["split"] == "validation"
            ].copy()
            tau, tau_report = freeze_tau(validation_only, config=config)
            write_json(output_root / "reports" / "tau_freeze.json", tau_report)
            timings["tau_freeze_seconds"] = round(time.perf_counter() - started, 3)

            started = run_stage("signals")
            frozen_config = frozen_config_payload(
                config,
                code_commit=provenance.commit,
                resolved_revision=snapshot.resolved_revision,
                snapshot_relative_path=snapshot.relative_path,
                tau=f"{tau:.{config.tau_decimals}f}",
            )
            write_json(output_root / "config.json", frozen_config)
            signals = generate_signals(inference.predictions, tau=tau, config=config)
            write_csv_frame(signals, output_root / "signals.csv", SIGNAL_COLUMNS)
            timings["signals_seconds"] = round(time.perf_counter() - started, 3)
            record["tau"] = f"{tau:.{config.tau_decimals}f}"
            record["signals"] = {
                "rows": len(signals),
                "counts": signal_counts(signals),
            }

            started = run_stage("validation")
            validation = build_validation_report(output_root, config)
            write_validation_report(output_root, validation)
            if not validation["passed"]:
                failures = [
                    item["name"]
                    for item in validation["checks"]
                    if not item["passed"]
                ]
                raise ValidationError(
                    f"{len(failures)} validation checks failed: {failures}"
                )
            timings["validation_seconds"] = round(time.perf_counter() - started, 3)

            started = run_stage("artifact_hashes")
            manifest_path = write_artifact_manifest(output_root)
            artifact_check = verify_artifact_manifest(output_root)
            if not artifact_check["passed"]:
                raise ValidationError(
                    "artifact hash manifest did not verify: "
                    f"{artifact_check['failures']}"
                )
            timings["artifact_hashes_seconds"] = round(
                time.perf_counter() - started, 3
            )
            manifest = read_json(manifest_path)
            record["artifacts"] = {
                "count": len(manifest["files"]),
                "combined_sha256": manifest["combined_sha256"],
            }

        record.update(
            {
                "status": "completed",
                "step1_completed": False,
                "finished_at_utc": utc_now_iso(),
                "total_elapsed_seconds": round(time.perf_counter() - started_perf, 3),
                "stages": timings,
                "blockers": [],
                "next_required": "run offline isolated reproduction before marking Step 1 complete",
            }
        )
        write_json(output_root / "reports" / "run_record.json", record)
        return record
    except Exception as error:
        _write_failure_record(
            output_root,
            record,
            stage=stage,
            error=error,
            started_at=started_at,
            started_perf=started_perf,
        )
        raise


def reproduce(config: Step1Config, *, http_timeout: float = 30.0) -> dict[str, Any]:
    """Re-run in a separate directory with sockets blocked, then compare bytes."""
    formal_root = _artifact_root(config)
    replay_root = _replay_root(config)
    run_record_path = formal_root / "reports" / "run_record.json"
    if not run_record_path.is_file():
        raise PipelineError(f"formal run record not found: {run_record_path}")
    formal_check = verify_artifact_manifest(formal_root)
    if not formal_check["passed"]:
        raise ValidationError(
            f"formal artifact manifest is invalid: {formal_check['failures']}"
        )
    formal_manifest = read_json(formal_root / "artifact_hashes.json")
    formal_files = {
        item["path"]: item["sha256"] for item in formal_manifest["files"]
    }

    LOGGER.info("starting isolated offline replay")
    replay_record = generate_pipeline(
        config,
        replay_root,
        offline=True,
        http_timeout=http_timeout,
    )
    replay_check = verify_artifact_manifest(replay_root)
    replay_manifest = read_json(replay_root / "artifact_hashes.json")
    replay_files = {
        item["path"]: item["sha256"] for item in replay_manifest["files"]
    }
    all_paths = sorted(set(DETERMINISTIC_ARTIFACTS))
    mismatches: list[dict[str, str | None]] = []
    for path in all_paths:
        formal_hash = formal_files.get(path)
        replay_hash = replay_files.get(path)
        if formal_hash != replay_hash:
            mismatches.append(
                {
                    "path": path,
                    "formal_sha256": formal_hash,
                    "replay_sha256": replay_hash,
                }
            )
    passed = (
        not mismatches
        and formal_check["passed"]
        and replay_check["passed"]
        and formal_manifest["combined_sha256"]
        == replay_manifest["combined_sha256"]
    )
    report = {
        "schema_version": REPRODUCIBILITY_SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "offline": True,
        "network_guard_enabled": True,
        "formal_dir": formal_root.relative_to(config.project_root).as_posix(),
        "replay_dir": replay_root.relative_to(config.project_root).as_posix(),
        "formal_run_status": replay_record["status"],
        "files_compared": len(all_paths),
        "formal_combined_sha256": formal_manifest["combined_sha256"],
        "replay_combined_sha256": replay_manifest["combined_sha256"],
        "mismatches": mismatches,
        "passed": passed,
    }
    report_path = formal_root / "reports" / "reproducibility.json"
    if report_path.exists():
        raise PipelineError(f"reproducibility report already exists: {report_path}")
    write_json(report_path, report)

    run_record = read_json(run_record_path)
    run_record["reproducibility"] = {
        "passed": passed,
        "report": "reports/reproducibility.json",
        "files_compared": len(all_paths),
        "offline": True,
        "network_guard_enabled": True,
        "mismatches": mismatches,
    }
    run_record["step1_completed"] = passed
    run_record["updated_at_utc"] = utc_now_iso()
    if not passed:
        run_record["blockers"] = ["reproducibility: isolated offline outputs differ"]
    write_json(run_record_path, run_record, overwrite=True)
    if not passed:
        raise ValidationError(
            f"isolated replay differs in {len(mismatches)} deterministic files"
        )
    return report


def prepare_only(
    config: Step1Config, *, offline: bool, http_timeout: float
) -> dict[str, Any]:
    guard = block_network() if offline else nullcontext()
    with guard:
        status = prepare_raw_cache(config, offline=offline, timeout=http_timeout)
        records = read_cached_records(config, status)
        _, quality = clean_records(
            records,
            symbols=config.symbols,
            window_start=config.window_start,
            window_end=config.window_end,
        )
    return {
        "cache_hit": status.cache_hit,
        "network_requests": status.network_requests,
        "chunk_count": len(status.chunks),
        "raw_rows": quality["totals"]["raw_rows"],
        "cleaned_rows": quality["totals"]["cleaned_rows"],
        "quality_passed": quality["passed"],
        "endpoints": list(status.endpoints_used),
    }


def verify(config: Step1Config) -> dict[str, Any]:
    root = _artifact_root(config)
    artifact_check = verify_artifact_manifest(root)
    validation_report = read_json(root / "reports" / "validation.json")
    run_record_path = root / "reports" / "run_record.json"
    run_record = read_json(run_record_path) if run_record_path.exists() else {}
    return {
        "artifact_hashes_passed": artifact_check["passed"],
        "files_checked": artifact_check["files_checked"],
        "failures": artifact_check["failures"],
        "validation_passed": validation_report.get("passed"),
        "validation_checks": validation_report.get("summary"),
        "step1_completed": run_record.get("step1_completed", False),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and verify fixed Step 1 data, model, and signal artifacts."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/step1.yaml"),
        help="fixed Step 1 input YAML (default: configs/step1.yaml)",
    )
    parser.add_argument(
        "--step2-config",
        type=Path,
        default=Path("configs/step2.yaml"),
        help="fixed Step 2 input YAML (default: configs/step2.yaml)",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="Binance HTTP timeout in seconds",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare-data", help="cache and validate the fixed Binance window only"
    )
    prepare.add_argument("--offline", action="store_true")
    run = subparsers.add_parser(
        "run", help="generate the formal Step 1 artifacts into artifacts/step1"
    )
    run.add_argument(
        "--offline",
        action="store_true",
        help="forbid all network access and require full local caches",
    )
    subparsers.add_parser(
        "reproduce",
        help="re-run offline into artifacts/replay/step1 and compare every byte hash",
    )
    subparsers.add_parser("verify", help="verify the formal artifact hash manifest")
    subparsers.add_parser(
        "step2-build",
        help="freeze Step 1 inputs, run the backtest, and build the six-file bundle",
    )
    subparsers.add_parser(
        "step2-verify",
        help="run positive, negative-matrix, and second-environment bundle checks",
    )
    subparsers.add_parser(
        "step2-reproduce",
        help="rebuild in artifacts/replay/step2, compare bytes, and freeze the bundle",
    )
    subparsers.add_parser(
        "step2-status", help="read the Step 2 status without modifying artifacts"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command.startswith("step2-"):
            step2_config = load_step2_config(args.step2_config)
            if args.command == "step2-build":
                result = build_step2(step2_config)
            elif args.command == "step2-verify":
                result = verify_step2(step2_config)
            elif args.command == "step2-reproduce":
                result = reproduce_step2(step2_config)
            elif args.command == "step2-status":
                result = status_step2(step2_config)
            else:  # pragma: no cover - argparse enforces choices
                parser.error(f"unknown command: {args.command}")
                return 2
        else:
            config = load_config(args.config)
            if args.command == "prepare-data":
                result = prepare_only(
                    config,
                    offline=args.offline,
                    http_timeout=args.http_timeout,
                )
            elif args.command == "run":
                result = generate_pipeline(
                    config,
                    _artifact_root(config),
                    offline=args.offline,
                    http_timeout=args.http_timeout,
                )
            elif args.command == "reproduce":
                result = reproduce(config, http_timeout=args.http_timeout)
            elif args.command == "verify":
                result = verify(config)
            else:  # pragma: no cover - argparse enforces choices
                parser.error(f"unknown command: {args.command}")
                return 2
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        if args.command == "verify" and (
            not result["artifact_hashes_passed"] or not result["validation_passed"]
        ):
            return 1
        return 0
    except Exception as error:
        LOGGER.error("%s: %s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
