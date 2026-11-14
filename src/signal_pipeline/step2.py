"""Step 2 orchestration: input freeze, backtest, bundle, verification, and freeze."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from .backtest import TIMESERIES_COLUMNS, run_backtest
from .bundle import (
    BUNDLE_FILE_NAMES,
    BundleResult,
    Step1BundleInput,
    Step2BundleRules,
    create_bundle,
)
from .bundle_validation import (
    negative_validation,
    positive_validation,
    second_environment_validation,
)
from .data import DATASET_COLUMNS
from .hashing import (
    combined_hash,
    hash_relative_files,
    read_csv_exact,
    read_json,
    sha256_file,
    verify_relative_hashes,
    write_csv_frame,
    write_json,
)
from .runtime import (
    code_provenance,
    dependency_versions,
    runtime_metadata,
    utc_now_iso,
)
from .signals import SIGNAL_COLUMNS
from .validation import verify_artifact_manifest

STEP2_CONFIG_SCHEMA_VERSION = "step2-input-v1"
INPUT_FREEZE_SCHEMA_VERSION = "step2-input-freeze-v1"
RUN_RECORD_SCHEMA_VERSION = "step2-run-record-v1"
REPRODUCIBILITY_SCHEMA_VERSION = "step2-reproducibility-v1"
BUNDLE_FROZEN_SCHEMA_VERSION = "step2-bundle-frozen-v1"
COMMITMENT_SCHEMA_VERSION = "step2-manifest-commitment-v1"
EXECUTION_RULE = "same_bar_open_to_close"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class Step2Error(RuntimeError):
    """Raised when Step 2 input, output, or verification invariants fail."""


@dataclass(frozen=True)
class Step2Config:
    config_path: Path
    project_root: Path
    config_sha256: str
    step1_root: Path
    output_root: Path
    bundle_dir: Path
    evidence_dir: Path
    reports_dir: Path
    fee_bps: int
    initial_position: int
    long_only: bool
    execution: str
    splits_independent: bool
    bundle_schema_version: str

    def with_output_root(self, root: Path) -> "Step2Config":
        return replace(
            self,
            output_root=root,
            bundle_dir=root / "bundle",
            evidence_dir=root / "evidence",
            reports_dir=root / "reports",
        )


@dataclass(frozen=True)
class VerifiedStep1:
    source: Step1BundleInput
    root: Path
    freeze: dict[str, Any]
    frozen_config: dict[str, Any]
    run_record: dict[str, Any]
    validation_report: dict[str, Any]
    reproducibility_report: dict[str, Any]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Step2Error(f"{field} must be a mapping")
    return value


def _expect_keys(mapping: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(mapping) != expected:
        raise Step2Error(
            f"{field} keys mismatch: missing={sorted(expected - set(mapping))}, "
            f"unknown={sorted(set(mapping) - expected)}"
        )


def _relative_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise Step2Error(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise Step2Error(f"{field} must stay within the project root")
    return Path(*path.parts)


def _ensure_child(path: Path, parent: Path, field: str) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise Step2Error(f"{field} must be inside {parent}") from exc


def load_step2_config(
    path: Path, *, project_root: Path | None = None
) -> Step2Config:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise Step2Error(f"Step 2 config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise Step2Error(f"invalid Step 2 YAML: {exc}") from exc
    root = _mapping(raw, "root")
    _expect_keys(root, {"schema_version", "step1", "output", "backtest", "bundle"}, "root")
    if root["schema_version"] != STEP2_CONFIG_SCHEMA_VERSION:
        raise Step2Error("unexpected Step 2 config schema")
    step1 = _mapping(root["step1"], "step1")
    _expect_keys(step1, {"artifacts_dir"}, "step1")
    output = _mapping(root["output"], "output")
    _expect_keys(output, {"root", "bundle_dir", "evidence_dir", "reports_dir"}, "output")
    backtest = _mapping(root["backtest"], "backtest")
    _expect_keys(
        backtest,
        {
            "fee_bps",
            "initial_position",
            "long_only",
            "execution",
            "splits_independent",
        },
        "backtest",
    )
    bundle = _mapping(root["bundle"], "bundle")
    _expect_keys(bundle, {"schema_version"}, "bundle")

    project_root = (
        Path(project_root).resolve()
        if project_root is not None
        else config_path.parent.parent
    )
    step1_root = project_root / _relative_path(step1["artifacts_dir"], "step1.artifacts_dir")
    output_root = project_root / _relative_path(output["root"], "output.root")
    bundle_dir = project_root / _relative_path(output["bundle_dir"], "output.bundle_dir")
    evidence_dir = project_root / _relative_path(output["evidence_dir"], "output.evidence_dir")
    reports_dir = project_root / _relative_path(output["reports_dir"], "output.reports_dir")
    for child, field in (
        (bundle_dir, "output.bundle_dir"),
        (evidence_dir, "output.evidence_dir"),
        (reports_dir, "output.reports_dir"),
    ):
        _ensure_child(child, output_root, field)
    if output_root.resolve() == step1_root.resolve():
        raise Step2Error("Step 2 output must not overwrite Step 1 artifacts")
    try:
        output_root.resolve().relative_to(step1_root.resolve())
        raise Step2Error("Step 2 output must not be inside Step 1 artifacts")
    except ValueError:
        pass

    if backtest["fee_bps"] != 10:
        raise Step2Error("fee_bps is frozen at 10")
    if backtest["initial_position"] != 0:
        raise Step2Error("initial_position is frozen at 0")
    if backtest["long_only"] is not True:
        raise Step2Error("long_only must be true")
    if backtest["execution"] != EXECUTION_RULE:
        raise Step2Error(f"execution must be {EXECUTION_RULE!r}")
    if backtest["splits_independent"] is not True:
        raise Step2Error("splits_independent must be true")
    if bundle["schema_version"] != "verifiable-signal-bundle-v1":
        raise Step2Error("unsupported bundle schema version")

    return Step2Config(
        config_path=config_path,
        project_root=project_root,
        config_sha256=sha256_file(config_path),
        step1_root=step1_root,
        output_root=output_root,
        bundle_dir=bundle_dir,
        evidence_dir=evidence_dir,
        reports_dir=reports_dir,
        fee_bps=10,
        initial_position=0,
        long_only=True,
        execution=EXECUTION_RULE,
        splits_independent=True,
        bundle_schema_version=str(bundle["schema_version"]),
    )


def _artifact_hash_map(artifact_manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = artifact_manifest.get("files")
    if not isinstance(entries, list):
        raise Step2Error("Step 1 artifact manifest files[] is missing")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise Step2Error("Step 1 artifact manifest contains an invalid entry")
        result[entry["path"]] = entry
    return result


def _input_freeze_report(
    config: Step2Config,
    *,
    frozen_config: Mapping[str, Any],
    artifact_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    root = config.step1_root
    paths = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    files = hash_relative_files(paths, root)
    checks = [
        {"name": "step1_artifact_manifest_verified", "passed": True},
        {"name": "step1_completed", "passed": True},
        {"name": "step1_offline_reproduction_passed", "passed": True},
        {"name": "step1_validation_passed", "passed": True},
        {"name": "frozen_window_and_splits_match", "passed": True},
        {"name": "frozen_tau_positive", "passed": True},
        {"name": "model_revision_immutable", "passed": True},
    ]
    return {
        "schema_version": INPUT_FREEZE_SCHEMA_VERSION,
        "step1_root": config.step1_root.relative_to(config.project_root).as_posix(),
        "step1_code_commit": frozen_config["code_commit"],
        "step1_artifact_manifest_sha256": sha256_file(root / "artifact_hashes.json"),
        "files": files,
        "combined_sha256": combined_hash(files),
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def verify_step1_inputs(config: Step2Config) -> VerifiedStep1:
    root = config.step1_root
    required = (
        "artifact_hashes.json",
        "dataset.csv",
        "signals.csv",
        "config.json",
        "model_reference.json",
        "reports/run_record.json",
        "reports/validation.json",
        "reports/reproducibility.json",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise Step2Error(f"Step 1 inputs are missing: {missing}")

    artifact_check = verify_artifact_manifest(root)
    if not artifact_check["passed"]:
        raise Step2Error(f"Step 1 artifact hashes are invalid: {artifact_check['failures']}")
    artifact_manifest = read_json(root / "artifact_hashes.json")
    artifact_entries = _artifact_hash_map(artifact_manifest)
    if not artifact_manifest.get("combined_sha256"):
        raise Step2Error("Step 1 artifact manifest is missing combined_sha256")
    if combined_hash(list(artifact_entries.values())) != artifact_manifest["combined_sha256"]:
        raise Step2Error("Step 1 artifact manifest combined hash is invalid")

    run_record = read_json(root / "reports" / "run_record.json")
    if run_record.get("status") != "completed" or run_record.get("step1_completed") is not True:
        raise Step2Error("Step 1 run record is not complete")
    reproducibility = read_json(root / "reports" / "reproducibility.json")
    if reproducibility.get("passed") is not True:
        raise Step2Error("Step 1 reproduction did not pass")
    validation = read_json(root / "reports" / "validation.json")
    if validation.get("passed") is not True:
        raise Step2Error("Step 1 validation did not pass")

    frozen_config = read_json(root / "config.json")
    if frozen_config.get("schema_version") != "step1-frozen-v1":
        raise Step2Error("unsupported Step 1 frozen config")
    code_commit = frozen_config.get("code_commit")
    if not isinstance(code_commit, str) or not _COMMIT_RE.fullmatch(code_commit):
        raise Step2Error("Step 1 frozen config has an invalid code commit")
    if run_record.get("code_commit") != code_commit:
        raise Step2Error("Step 1 code commit differs between config and run record")
    data = frozen_config.get("data", {})
    model = frozen_config.get("model", {})
    signals_config = frozen_config.get("signals", {})
    if data.get("source") != "Binance Spot":
        raise Step2Error("unexpected Step 1 data source")
    if data.get("symbols") != ["BTCUSDT", "ETHUSDT"]:
        raise Step2Error("unexpected Step 1 symbols")
    if data.get("window_start_utc") != "2024-01-01T00:00:00Z":
        raise Step2Error("unexpected Step 1 window start")
    if data.get("window_end_utc") != "2025-01-01T00:00:00Z":
        raise Step2Error("unexpected Step 1 window end")
    if data.get("validation", {}).get("start_utc") != "2024-07-01T00:00:00Z":
        raise Step2Error("unexpected validation start")
    if data.get("validation", {}).get("end_utc") != "2024-10-01T00:00:00Z":
        raise Step2Error("unexpected validation end")
    if data.get("test", {}).get("start_utc") != "2024-10-01T00:00:00Z":
        raise Step2Error("unexpected test start")
    if data.get("test", {}).get("end_utc") != "2025-01-01T00:00:00Z":
        raise Step2Error("unexpected test end")
    revision = model.get("resolved_revision")
    if not isinstance(revision, str) or not _COMMIT_RE.fullmatch(revision):
        raise Step2Error("Step 1 model revision is not immutable")
    tau = signals_config.get("tau")
    try:
        tau_decimal = Decimal(str(tau))
    except Exception as exc:
        raise Step2Error("Step 1 tau is not a decimal") from exc
    if tau_decimal <= 0:
        raise Step2Error("Step 1 tau must be positive")
    model_reference = read_json(root / "model_reference.json")
    if (
        model_reference.get("model_id") != model.get("id")
        or model_reference.get("resolved_revision") != revision
    ):
        raise Step2Error("Step 1 model reference does not match frozen config")
    checkpoint = model_reference.get("checkpoint_commitment_sha256")
    if not isinstance(checkpoint, str) or len(checkpoint) != 64:
        raise Step2Error("Step 1 checkpoint commitment is missing")

    required_manifest_paths = ("config.json", "dataset.csv", "signals.csv", "model_reference.json")
    for name in required_manifest_paths:
        if name not in artifact_entries:
            raise Step2Error(f"Step 1 artifact manifest is missing {name}")

    freeze = _input_freeze_report(
        config,
        frozen_config=frozen_config,
        artifact_manifest=artifact_manifest,
    )
    source = Step1BundleInput(
        dataset_path=root / "dataset.csv",
        signals_path=root / "signals.csv",
        model_reference_path=root / "model_reference.json",
        step1_config=frozen_config,
        step1_config_sha256=sha256_file(root / "config.json"),
        step1_artifact_manifest_sha256=sha256_file(root / "artifact_hashes.json"),
        step1_code_commit=code_commit,
        dataset_sha256=sha256_file(root / "dataset.csv"),
        signal_sha256=sha256_file(root / "signals.csv"),
        model_reference_sha256=sha256_file(root / "model_reference.json"),
        tau=str(tau),
        tau_decimals=int(signals_config.get("tau_decimals")),
        model_id=str(model.get("id")),
        model_revision=revision,
        checkpoint_commitment_sha256=checkpoint,
    )
    return VerifiedStep1(
        source=source,
        root=root,
        freeze=freeze,
        frozen_config=frozen_config,
        run_record=run_record,
        validation_report=validation,
        reproducibility_report=reproducibility,
    )


def _prepare_empty_output(config: Step2Config) -> None:
    if config.output_root.exists() and any(config.output_root.iterdir()):
        raise Step2Error(
            f"refusing to overwrite non-empty Step 2 output: {config.output_root}"
        )
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    config.reports_dir.mkdir(parents=True, exist_ok=True)


def _rules(config: Step2Config) -> Step2BundleRules:
    return Step2BundleRules(
        step2_config_sha256=config.config_sha256,
        bundle_schema_version=config.bundle_schema_version,
        fee_bps=config.fee_bps,
        initial_position=config.initial_position,
        long_only=config.long_only,
        execution=config.execution,
        splits_independent=config.splits_independent,
    )


def _record_commitment(config: Step2Config, result: BundleResult) -> dict[str, Any]:
    return {
        "schema_version": COMMITMENT_SCHEMA_VERSION,
        "bundle_dir": config.bundle_dir.relative_to(config.project_root).as_posix(),
        "manifest_file": "manifest.json",
        "manifest_sha256": result.manifest_sha256,
        "object_id": result.object_id,
        "payload_commitment": result.payload_commitment,
        "files": list(result.files),
        "manifest_self_hash_in_bundle": False,
        "manifest_hash_storage": "external_report",
    }


def _base_run_record(
    config: Step2Config,
    *,
    code_commit: str,
    started_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "status": "running",
        "step2_completed": False,
        "bundle_frozen": False,
        "started_at_utc": started_at,
        "code_commit": code_commit,
        "step1_code_commit": None,
        "step2_config_path": config.config_path.relative_to(config.project_root).as_posix(),
        "step2_config_sha256": config.config_sha256,
        "step1_root": config.step1_root.relative_to(config.project_root).as_posix(),
        "output_dir": config.output_root.relative_to(config.project_root).as_posix(),
        "runtime": runtime_metadata(),
        "dependencies": dependency_versions(),
        "stages": {},
        "blockers": [],
    }


def _write_failure(
    config: Step2Config,
    record: dict[str, Any],
    *,
    stage: str,
    error: BaseException,
    started_perf: float,
) -> None:
    record.update(
        {
            "status": "failed",
            "step2_completed": False,
            "bundle_frozen": False,
            "failed_stage": stage,
            "failure": {"type": type(error).__name__, "message": str(error)},
            "finished_at_utc": utc_now_iso(),
            "total_elapsed_seconds": round(time.perf_counter() - started_perf, 3),
            "blockers": [f"{stage}: {type(error).__name__}: {error}"],
        }
    )
    write_json(
        config.reports_dir / "run_record.json",
        record,
        overwrite=True,
    )


def build_step2(config: Step2Config) -> dict[str, Any]:
    provenance = code_provenance(config.project_root)
    _prepare_empty_output(config)
    started_at = utc_now_iso()
    started_perf = time.perf_counter()
    record = _base_run_record(
        config, code_commit=provenance.commit, started_at=started_at
    )
    timings: dict[str, float] = {}
    stage = "startup"
    try:
        started = time.perf_counter()
        stage = "step1_input_freeze"
        verified = verify_step1_inputs(config)
        record["step1_code_commit"] = verified.source.step1_code_commit
        record["step1_artifact_manifest_sha256"] = (
            verified.source.step1_artifact_manifest_sha256
        )
        write_json(config.reports_dir / "input_freeze.json", verified.freeze)
        timings["input_freeze_seconds"] = round(time.perf_counter() - started, 3)

        started = time.perf_counter()
        stage = "backtest"
        dataset = read_csv_exact(verified.source.dataset_path, DATASET_COLUMNS)
        signals = read_csv_exact(verified.source.signals_path, SIGNAL_COLUMNS)
        splits = {
            "validation": verified.frozen_config["data"]["validation"],
            "test": verified.frozen_config["data"]["test"],
        }
        input_metadata = {
            "step1_code_commit": verified.source.step1_code_commit,
            "step1_artifact_manifest_sha256": (
                verified.source.step1_artifact_manifest_sha256
            ),
            "dataset_sha256": verified.source.dataset_sha256,
            "signal_sha256": verified.source.signal_sha256,
            "config_sha256": verified.source.step1_config_sha256,
            "model_reference_sha256": verified.source.model_reference_sha256,
            "tau": verified.source.tau,
        }
        backtest = run_backtest(
            dataset,
            signals,
            symbols=verified.frozen_config["data"]["symbols"],
            splits=splits,
            fee_bps=config.fee_bps,
            initial_position=config.initial_position,
            expected_tau=verified.source.tau,
            input_metadata=input_metadata,
        )
        write_csv_frame(
            backtest.timeseries,
            config.evidence_dir / "backtest_timeseries.csv",
            TIMESERIES_COLUMNS,
        )
        write_json(config.reports_dir / "backtest_checks.json", backtest.checks)
        timings["backtest_seconds"] = round(time.perf_counter() - started, 3)

        started = time.perf_counter()
        stage = "bundle"
        result = create_bundle(
            config.bundle_dir,
            verified.source,
            _rules(config),
            code_commit=provenance.commit,
            backtest_summary=backtest.summary,
        )
        commitment = _record_commitment(config, result)
        write_json(config.reports_dir / "manifest_commitment.json", commitment)
        timings["bundle_seconds"] = round(time.perf_counter() - started, 3)

        started = time.perf_counter()
        stage = "bundle_positive_validation"
        positive = positive_validation(
            config.bundle_dir,
            result.manifest_sha256,
            project_root=config.project_root,
        )
        write_json(config.reports_dir / "bundle_validation.json", positive)
        if not positive["passed"]:
            raise Step2Error(f"bundle positive validation failed: {positive['failures']}")
        timings["positive_validation_seconds"] = round(
            time.perf_counter() - started, 3
        )

        record.update(
            {
                "status": "built",
                "step2_completed": False,
                "bundle_frozen": False,
                "finished_at_utc": utc_now_iso(),
                "total_elapsed_seconds": round(time.perf_counter() - started_perf, 3),
                "stages": timings,
                "input_freeze": {
                    "files": len(verified.freeze["files"]),
                    "combined_sha256": verified.freeze["combined_sha256"],
                },
                "backtest": {
                    "signals": len(signals),
                    "timeseries_rows": len(backtest.timeseries),
                    "summary_sha256": result.backtest_summary_sha256,
                },
                "bundle": {
                    "dir": config.bundle_dir.relative_to(config.project_root).as_posix(),
                    "object_id": result.object_id,
                    "payload_commitment": result.payload_commitment,
                    "manifest_sha256": result.manifest_sha256,
                    "files": list(BUNDLE_FILE_NAMES),
                    "positive_validation_passed": positive["passed"],
                },
                "next_required": "run step2-verify and step2-reproduce before freezing",
                "blockers": [],
            }
        )
        write_json(config.reports_dir / "run_record.json", record)
        return record
    except Exception as error:
        _write_failure(
            config,
            record,
            stage=stage,
            error=error,
            started_perf=started_perf,
        )
        raise


def _load_commitment(config: Step2Config) -> dict[str, Any]:
    path = config.reports_dir / "manifest_commitment.json"
    if not path.is_file():
        raise Step2Error("manifest commitment report is missing")
    return read_json(path)


def verify_step2(config: Step2Config) -> dict[str, Any]:
    record_path = config.reports_dir / "run_record.json"
    if not record_path.is_file():
        raise Step2Error("Step 2 run record is missing")
    record = read_json(record_path)
    if record.get("status") not in {"built", "verified"}:
        raise Step2Error(f"Step 2 is not ready for verification: {record.get('status')}")
    commitment = _load_commitment(config)
    expected_hash = commitment["manifest_sha256"]

    positive = positive_validation(
        config.bundle_dir, expected_hash, project_root=config.project_root
    )
    write_json(config.reports_dir / "bundle_validation.json", positive, overwrite=True)
    if not positive["passed"]:
        raise Step2Error(f"bundle positive validation failed: {positive['failures']}")

    negative = negative_validation(
        config.bundle_dir, expected_hash, project_root=config.project_root
    )
    write_json(config.reports_dir / "negative_checks.json", negative)
    if not negative["passed"]:
        raise Step2Error("bundle negative checks did not reject every mutation")

    second_environment = second_environment_validation(
        config.bundle_dir, expected_hash, project_root=config.project_root
    )
    write_json(
        config.reports_dir / "second_environment.json",
        second_environment,
    )

    record["status"] = "verified"
    record["step2_completed"] = False
    record["verification"] = {
        "positive": positive["passed"],
        "negative_cases": negative["case_count"],
        "negative_all_rejected": negative["all_rejected"],
        "second_environment": second_environment["passed"],
        "second_environment_interpreter": second_environment["interpreter_version"],
        "manifest_sha256": expected_hash,
    }
    record["next_required"] = "run step2-reproduce before freezing the bundle"
    write_json(record_path, record, overwrite=True)
    return record["verification"]


def _report_passed(config: Step2Config, name: str) -> dict[str, Any]:
    path = config.reports_dir / name
    if not path.is_file():
        raise Step2Error(f"required verification report is missing: {name}")
    report = read_json(path)
    if report.get("passed") is not True:
        raise Step2Error(f"verification report did not pass: {name}")
    return report


def reproduce_step2(config: Step2Config) -> dict[str, Any]:
    formal_record_path = config.reports_dir / "run_record.json"
    if not formal_record_path.is_file():
        raise Step2Error("formal Step 2 run record is missing")
    formal_record = read_json(formal_record_path)
    if formal_record.get("status") != "verified":
        raise Step2Error("step2-verify must pass before replay")
    _report_passed(config, "bundle_validation.json")
    _report_passed(config, "negative_checks.json")
    _report_passed(config, "second_environment.json")
    commitment = _load_commitment(config)
    expected_hash = commitment["manifest_sha256"]

    replay_root = config.project_root / "artifacts" / "replay" / "step2"
    if replay_root.exists() and any(replay_root.iterdir()):
        raise Step2Error(f"refusing to overwrite replay output: {replay_root}")
    replay_config = config.with_output_root(replay_root)
    build_step2(replay_config)

    checks: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for name in BUNDLE_FILE_NAMES:
        formal_hash = sha256_file(config.bundle_dir / name)
        replay_hash = sha256_file(replay_config.bundle_dir / name)
        passed = formal_hash == replay_hash
        checks.append({"file": name, "sha256": formal_hash})
        if not passed:
            mismatches.append(
                {
                    "file": name,
                    "formal_sha256": formal_hash,
                    "replay_sha256": replay_hash,
                }
            )
    replay_commitment = _load_commitment(replay_config)
    manifest_match = replay_commitment["manifest_sha256"] == expected_hash
    if not manifest_match:
        mismatches.append(
            {
                "file": "manifestHash",
                "formal_sha256": expected_hash,
                "replay_sha256": replay_commitment["manifest_sha256"],
            }
        )
    report = {
        "schema_version": REPRODUCIBILITY_SCHEMA_VERSION,
        "formal_dir": config.output_root.relative_to(config.project_root).as_posix(),
        "replay_dir": replay_root.relative_to(config.project_root).as_posix(),
        "files_compared": len(BUNDLE_FILE_NAMES),
        "checks": checks,
        "mismatches": mismatches,
        "passed": not mismatches and manifest_match,
        "replay_run_status": read_json(
            replay_config.reports_dir / "run_record.json"
        ).get("status"),
    }
    write_json(config.reports_dir / "reproducibility.json", report)
    if not report["passed"]:
        formal_record["status"] = "reproduction_failed"
        formal_record["step2_completed"] = False
        formal_record["blockers"] = ["replay output differs from the formal bundle"]
        write_json(formal_record_path, formal_record, overwrite=True)
        raise Step2Error("replay bundle does not match the formal bundle")

    positive = _report_passed(config, "bundle_validation.json")
    negative = _report_passed(config, "negative_checks.json")
    second = _report_passed(config, "second_environment.json")
    frozen = {
        "schema_version": BUNDLE_FROZEN_SCHEMA_VERSION,
        "bundle_dir": config.bundle_dir.relative_to(config.project_root).as_posix(),
        "object_id": commitment["object_id"],
        "payload_commitment": commitment["payload_commitment"],
        "manifest_sha256": expected_hash,
        "files": list(BUNDLE_FILE_NAMES),
        "checks": {
            "positive_validation": positive["passed"],
            "negative_all_rejected": negative["all_rejected"],
            "second_environment": second["passed"],
            "replay_byte_identical": report["passed"],
            "manifest_hash_recomputed": manifest_match,
        },
        "frozen": True,
        "next_stage": "Step 3 is intentionally not started",
    }
    write_json(config.reports_dir / "bundle_frozen.json", frozen)
    formal_record["status"] = "completed"
    formal_record["step2_completed"] = True
    formal_record["bundle_frozen"] = True
    formal_record["frozen_at_utc"] = utc_now_iso()
    formal_record["reproducibility"] = {
        "passed": report["passed"],
        "files_compared": report["files_compared"],
        "mismatches": report["mismatches"],
        "report": "reports/reproducibility.json",
    }
    formal_record["blockers"] = []
    formal_record["next_required"] = "stop at Step 2; do not deploy or register on-chain"
    write_json(formal_record_path, formal_record, overwrite=True)
    return {
        "step2_completed": True,
        "bundle_frozen": True,
        "object_id": commitment["object_id"],
        "manifest_sha256": expected_hash,
        "files_compared": report["files_compared"],
        "mismatches": mismatches,
    }


def status_step2(config: Step2Config) -> dict[str, Any]:
    record_path = config.reports_dir / "run_record.json"
    record = read_json(record_path) if record_path.is_file() else {}
    commitment_path = config.reports_dir / "manifest_commitment.json"
    commitment = read_json(commitment_path) if commitment_path.is_file() else {}
    frozen_path = config.reports_dir / "bundle_frozen.json"
    frozen = read_json(frozen_path) if frozen_path.is_file() else {}
    return {
        "status": record.get("status"),
        "step2_completed": record.get("step2_completed", False),
        "bundle_frozen": record.get("bundle_frozen", False),
        "object_id": commitment.get("object_id"),
        "manifest_sha256": commitment.get("manifest_sha256"),
        "frozen": frozen.get("frozen", False),
    }
