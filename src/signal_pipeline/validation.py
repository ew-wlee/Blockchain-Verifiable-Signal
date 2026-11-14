"""Independent, deterministic checks over the frozen Step 1 artifacts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import Step1Config, format_utc
from .data import DATASET_COLUMNS, prepare_raw_cache
from .hashing import (
    ArtifactError,
    combined_hash,
    hash_relative_files,
    read_csv_exact,
    read_json,
    sha256_file,
    verify_relative_hashes,
)
from .inference import PREDICTION_COLUMNS
from .signals import (
    ALLOWED_SIGNALS,
    SIGNAL_COLUMNS,
    boundary_cases,
    classify_return,
    select_tau,
    verify_signal_mapping,
)

UTC = timezone.utc
VALIDATION_SCHEMA_VERSION = "step1-validation-v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "step1-artifact-hashes-v1"
_RETURN_PLACES = 12
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

DETERMINISTIC_ARTIFACTS = (
    "config.json",
    "dataset.csv",
    "model_reference.json",
    "predictions.csv",
    "reports/data_quality.json",
    "reports/tau_freeze.json",
    "reports/validation.json",
    "signals.csv",
)


class ValidationError(RuntimeError):
    """Raised when any required artifact check fails."""


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    details: Any | None = None,
) -> None:
    payload: dict[str, Any] = {"name": name, "passed": bool(passed)}
    if details is not None:
        payload["details"] = details
    checks.append(payload)


def _dataset_checks(
    root: Path,
    config: Step1Config,
    checks: list[dict[str, Any]],
) -> Any:
    dataset = read_csv_exact(root / "dataset.csv", DATASET_COLUMNS)
    expected_hours = config.expected_hours
    _check(
        checks,
        "dataset_row_count",
        len(dataset) == config.expected_rows,
        {"actual": len(dataset), "expected": config.expected_rows},
    )
    row_symbol_counts = {
        symbol: int((dataset["symbol"] == symbol).sum())
        for symbol in config.symbols
    }
    _check(
        checks,
        "dataset_symbol_coverage",
        set(dataset["symbol"]) == set(config.symbols)
        and all(count == expected_hours for count in row_symbol_counts.values()),
        row_symbol_counts,
    )
    unique_count = len(dataset.drop_duplicates(["timestamp_utc", "symbol"]))
    _check(
        checks,
        "dataset_unique_timestamp_symbol",
        unique_count == len(dataset),
        {"unique": unique_count, "rows": len(dataset)},
    )
    expected_keys = [
        (format_utc(config.window_start + timedelta(hours=hour)), symbol)
        for hour in range(expected_hours)
        for symbol in config.symbols
    ]
    actual_keys = list(zip(dataset["timestamp_utc"], dataset["symbol"]))
    _check(
        checks,
        "dataset_fixed_order_and_window",
        actual_keys == expected_keys,
        {
            "first": actual_keys[0] if actual_keys else None,
            "last": actual_keys[-1] if actual_keys else None,
        },
    )
    continuity_ok = True
    for symbol in config.symbols:
        symbol_frame = dataset.loc[dataset["symbol"] == symbol]
        timestamps = [_parse_time(value) for value in symbol_frame["timestamp_utc"]]
        continuity_ok = continuity_ok and all(
            current - previous == timedelta(hours=1)
            for previous, current in zip(timestamps, timestamps[1:])
        )
    _check(checks, "dataset_hourly_continuity", continuity_ok)
    prices_valid = True
    for column in ("open", "high", "low", "close"):
        prices_valid = prices_valid and all(
            Decimal(value) > 0 for value in dataset[column]
        )
    _check(checks, "dataset_positive_prices", prices_valid)
    return dataset


def _quality_checks(
    root: Path,
    config: Step1Config,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    quality = read_json(root / "reports" / "data_quality.json")
    _check(checks, "data_quality_passed", quality.get("passed") is True)
    totals = quality.get("totals", {})
    _check(
        checks,
        "data_quality_row_accounting",
        totals.get("cleaned_rows") == config.expected_rows
        and totals.get("expected_rows") == config.expected_rows
        and totals.get("missing_rows") == 0
        and totals.get("conflicting_duplicates") == 0
        and totals.get("unexpected_rows") == 0,
        totals,
    )
    cache = quality.get("cache", {})
    chunks = cache.get("chunks", [])
    _check(
        checks,
        "cache_evidence_complete",
        cache.get("rows_across_cache", -1) >= config.expected_rows
        and len(chunks) > 0
        and all(
            isinstance(chunk.get("payload_sha256"), str)
            and len(chunk["payload_sha256"]) == 64
            for chunk in chunks
        ),
        {"chunk_count": len(chunks), "rows": cache.get("rows_across_cache")},
    )
    return quality


def _cache_checks(
    config: Step1Config,
    checks: list[dict[str, Any]],
) -> None:
    status = prepare_raw_cache(config, offline=True)
    _check(
        checks,
        "raw_cache_hashes_valid",
        status.network_requests == 0 and len(status.chunks) > 0,
        {
            "chunks": len(status.chunks),
            "cached_rows": status.expected_rows,
            "network_requests": status.network_requests,
        },
    )


def _frozen_config_checks(
    root: Path,
    config: Step1Config,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    frozen = read_json(root / "config.json")
    data = frozen.get("data", {})
    model = frozen.get("model", {})
    signals = frozen.get("signals", {})
    _check(
        checks,
        "frozen_config_window",
        data.get("window_start_utc") == format_utc(config.window_start)
        and data.get("window_end_utc") == format_utc(config.window_end)
        and data.get("validation", {}).get("start_utc")
        == format_utc(config.validation.start)
        and data.get("validation", {}).get("end_utc")
        == format_utc(config.validation.end)
        and data.get("test", {}).get("start_utc") == format_utc(config.test.start)
        and data.get("test", {}).get("end_utc") == format_utc(config.test.end),
    )
    revision = model.get("resolved_revision", "")
    _check(
        checks,
        "frozen_model_revision",
        isinstance(revision, str)
        and _COMMIT_RE.fullmatch(revision) is not None
        and model.get("id") == config.model_id,
        {"resolved_revision": revision},
    )
    _check(
        checks,
        "frozen_code_commit",
        isinstance(frozen.get("code_commit"), str)
        and _COMMIT_RE.fullmatch(frozen["code_commit"]) is not None,
    )
    tau = signals.get("tau")
    tau_ok = isinstance(tau, str)
    if tau_ok:
        try:
            tau_ok = Decimal(tau) > 0
        except Exception:
            tau_ok = False
    _check(checks, "frozen_positive_tau", tau_ok, {"tau": tau})
    _check(
        checks,
        "frozen_tau_input_split_validation",
        signals.get("tau_input_split") == "validation"
        and signals.get("tau_rule") == config.tau_rule,
    )
    return frozen


def _model_checks(
    root: Path,
    config: Step1Config,
    frozen: dict[str, Any],
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    reference = read_json(root / "model_reference.json")
    model = frozen.get("model", {})
    revision = model.get("resolved_revision")
    snapshot_path = model.get("snapshot_path")
    root_ok = isinstance(snapshot_path, str) and snapshot_path
    snapshot_root = config.project_root / snapshot_path if root_ok else Path()
    _check(
        checks,
        "model_reference_identity",
        reference.get("model_id") == config.model_id
        and reference.get("resolved_revision") == revision
        and reference.get("snapshot_path") == snapshot_path,
    )
    files = reference.get("files", [])
    sorted_files = sorted(files, key=lambda item: item.get("path", ""))
    hashes_ok = bool(files) and files == sorted_files
    actual_entries: list[dict[str, Any]] = []
    if root_ok and snapshot_root.is_dir():
        for entry in files:
            path = snapshot_root / entry["path"]
            if not path.is_file():
                hashes_ok = False
                continue
            actual_entries.append(
                {
                    "path": entry["path"],
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
            hashes_ok = (
                hashes_ok
                and path.stat().st_size == int(entry["size"])
                and sha256_file(path) == entry["sha256"]
            )
    else:
        hashes_ok = False
    _check(
        checks,
        "model_checkpoint_files",
        hashes_ok,
        {"file_count": len(files)},
    )
    commitment_ok = False
    if actual_entries:
        commitment_ok = combined_hash(actual_entries) == reference.get(
            "checkpoint_commitment_sha256"
        )
    _check(checks, "model_checkpoint_commitment", commitment_ok)
    required_ok = root_ok and (snapshot_root / "config.json").is_file() and (
        snapshot_root / "model.safetensors"
    ).is_file()
    _check(
        checks,
        "model_required_files",
        required_ok,
        {"snapshot_path": snapshot_path},
    )
    revision_path_ok = (
        root_ok
        and isinstance(revision, str)
        and snapshot_root.name.endswith(revision)
    )
    _check(checks, "model_snapshot_path_matches_revision", revision_path_ok)
    return reference


def _prediction_checks(
    root: Path,
    config: Step1Config,
    dataset: Any,
    checks: list[dict[str, Any]],
    frozen: dict[str, Any],
) -> Any:
    predictions = read_csv_exact(root / "predictions.csv", PREDICTION_COLUMNS)
    expected_targets = {
        split: {
            format_utc(start + timedelta(hours=hour))
            for hour in range(hours)
        }
        for split, start, hours in (
            ("validation", config.validation.start, config.validation.hours),
            ("test", config.test.start, config.test.hours),
        )
    }
    target_sets: dict[tuple[str, str], set[str]] = {}
    unique_ok = True
    for split in ("validation", "test"):
        for symbol in config.symbols:
            subset = predictions.loc[
                (predictions["split"] == split) & (predictions["symbol"] == symbol)
            ]
            target_sets[(split, symbol)] = set(subset["target_timestamp_utc"])
            unique_ok = unique_ok and len(subset) == len(
                set(zip(subset["target_timestamp_utc"], subset["symbol"]))
            )
    _check(
        checks,
        "prediction_full_split_coverage",
        all(
            target_sets[(split, symbol)] == expected_targets[split]
            for split in ("validation", "test")
            for symbol in config.symbols
        ),
        {
            split: sum(
                len(target_sets[(split, symbol)]) for symbol in config.symbols
            )
            for split in ("validation", "test")
        },
    )
    _check(checks, "prediction_unique_targets", unique_ok)
    order_ok = list(zip(predictions["target_timestamp_utc"], predictions["symbol"])) == sorted(
        zip(predictions["target_timestamp_utc"], predictions["symbol"]),
        key=lambda item: (item[0], config.symbols.index(item[1])),
    )
    _check(checks, "prediction_fixed_order", order_ok)
    context_ok = True
    alignment_ok = True
    return_ok = True
    price_ok = True
    params_ok = True
    for row in predictions.to_dict(orient="records"):
        target = _parse_time(row["target_timestamp_utc"])
        context_start = _parse_time(row["context_start_timestamp_utc"])
        context_end = _parse_time(row["context_end_timestamp_utc"])
        context_ok = context_ok and (
            context_end == target - timedelta(hours=1)
            and context_start == target - timedelta(hours=config.context_length)
        )
        expected_split = config.split_for(target)
        alignment_ok = alignment_ok and expected_split == row["split"]
        dataset_row = dataset.loc[
            (dataset["symbol"] == row["symbol"])
            & (dataset["timestamp_utc"] == row["context_end_timestamp_utc"])
        ]
        if len(dataset_row) != 1:
            alignment_ok = False
            continue
        current_close = dataset_row.iloc[0]["close"]
        alignment_ok = alignment_ok and current_close == row["current_close"]
        predicted_close = Decimal(row["predicted_close_median"])
        price_ok = price_ok and predicted_close > 0 and Decimal(current_close) > 0
        expected_return = (
            predicted_close / Decimal(current_close) - Decimal(1)
        ).quantize(
            Decimal(1).scaleb(-_RETURN_PLACES), rounding=ROUND_HALF_EVEN
        )
        return_ok = return_ok and expected_return == Decimal(row["predicted_return"])
        params_ok = params_ok and (
            int(row["num_samples"]) == config.num_samples
            and int(row["seed"]) == config.seed
        )
    _check(checks, "prediction_context_alignment", context_ok)
    _check(checks, "prediction_split_and_close_alignment", alignment_ok)
    _check(checks, "prediction_return_recomputes", return_ok)
    _check(checks, "prediction_positive_prices", price_ok)
    _check(checks, "prediction_frozen_inference_params", params_ok)
    return predictions


def _tau_and_signal_checks(
    root: Path,
    config: Step1Config,
    predictions: Any,
    frozen: dict[str, Any],
    checks: list[dict[str, Any]],
) -> tuple[Any, Decimal]:
    tau_report = read_json(root / "reports" / "tau_freeze.json")
    validation_predictions = predictions.loc[
        predictions["split"] == "validation"
    ].copy()
    recomputed_tau = select_tau(
        validation_predictions,
        rule=config.tau_rule,
        decimals=config.tau_decimals,
    )
    tau_text = f"{recomputed_tau:.{config.tau_decimals}f}"
    frozen_tau = frozen.get("signals", {}).get("tau")
    report_tau = tau_report.get("tau")
    _check(
        checks,
        "tau_recomputes_from_validation_only",
        tau_text == frozen_tau == report_tau,
        {"recomputed": tau_text, "frozen": frozen_tau, "report": report_tau},
    )
    _check(
        checks,
        "tau_freeze_order_and_no_test_input",
        tau_report.get("input_split") == "validation"
        and tau_report.get("tau_inputs", {}).get("test_rows_supplied") == 0
        and tau_report.get("tau_inputs", {}).get("test_metrics_computed") is False,
    )
    boundary_ok = all(
        classify_return(value, recomputed_tau) == expected
        for _, value, expected in boundary_cases(recomputed_tau)
    )
    _check(checks, "tau_boundary_mapping", boundary_ok)

    signals = read_csv_exact(root / "signals.csv", SIGNAL_COLUMNS)
    _check(
        checks,
        "signals_row_count",
        len(signals) == len(predictions),
        {"signals": len(signals), "predictions": len(predictions)},
    )
    projection = list(
        zip(
            predictions["target_timestamp_utc"],
            predictions["symbol"],
            predictions["split"],
            predictions["context_end_timestamp_utc"],
            predictions["current_close"],
            predictions["predicted_close_median"],
            predictions["predicted_return"],
        )
    )
    signal_projection = list(
        zip(
            signals["signal_timestamp_utc"],
            signals["symbol"],
            signals["split"],
            signals["context_end_timestamp_utc"],
            signals["current_close"],
            signals["predicted_close_median"],
            signals["predicted_return"],
        )
    )
    _check(
        checks,
        "signals_time_and_prediction_alignment",
        projection == signal_projection,
    )
    mapping_failures = verify_signal_mapping(
        signals, tau=recomputed_tau, config=config
    )
    _check(
        checks,
        "signals_frozen_tau_mapping",
        not mapping_failures,
        {"failures": mapping_failures[:5]},
    )
    label_coverage = {
        label: int((signals["signal"] == label).sum())
        for label in ALLOWED_SIGNALS
    }
    _check(
        checks,
        "signals_cover_all_labels",
        all(count > 0 for count in label_coverage.values()),
        label_coverage,
    )
    _check(
        checks,
        "signals_split_labels",
        set(signals["split"]) == {"validation", "test"},
    )
    return signals, recomputed_tau


def build_validation_report(
    root: Path,
    config: Step1Config,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    dataset = _dataset_checks(root, config, checks)
    _quality_checks(root, config, checks)
    _cache_checks(config, checks)
    frozen = _frozen_config_checks(root, config, checks)
    _model_checks(root, config, frozen, checks)
    predictions = _prediction_checks(root, config, dataset, checks, frozen)
    _tau_and_signal_checks(root, config, predictions, frozen, checks)
    passed_count = sum(item["passed"] for item in checks)
    passed = passed_count == len(checks)
    report = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "checks": checks,
        "summary": {
            "total_checks": len(checks),
            "passed_checks": passed_count,
            "failed_checks": len(checks) - passed_count,
        },
        "passed": passed,
    }
    return report


def write_validation_report(root: Path, report: dict[str, Any]) -> Path:
    from .hashing import write_json

    path = root / "reports" / "validation.json"
    write_json(path, report)
    return path


def build_artifact_manifest(root: Path) -> dict[str, Any]:
    missing = [name for name in DETERMINISTIC_ARTIFACTS if not (root / name).is_file()]
    if missing:
        raise ArtifactError(f"cannot hash missing formal artifacts: {missing}")
    files = hash_relative_files(
        [root / name for name in DETERMINISTIC_ARTIFACTS], root
    )
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "canonical_rule": (
            "SHA256 over final file bytes; files sorted by POSIX relative path; "
            "combined hash over canonical JSON of the sorted entries"
        ),
        "files": files,
        "combined_sha256": combined_hash(files),
    }


def write_artifact_manifest(root: Path) -> Path:
    from .hashing import write_json

    manifest = build_artifact_manifest(root)
    path = root / "artifact_hashes.json"
    write_json(path, manifest)
    return path


def verify_artifact_manifest(root: Path) -> dict[str, Any]:
    path = root / "artifact_hashes.json"
    manifest = read_json(path)
    entries = manifest.get("files", [])
    failures = verify_relative_hashes(root, entries)
    actual_paths = [entry.get("path") for entry in entries]
    expected_paths = sorted(DETERMINISTIC_ARTIFACTS)
    if actual_paths != expected_paths:
        failures.append("artifact-list-mismatch")
    recomputed = combined_hash(entries) if not failures else None
    if recomputed != manifest.get("combined_sha256"):
        failures.append("combined-sha256-mismatch")
    return {
        "passed": not failures,
        "files_checked": len(entries),
        "failures": failures,
        "combined_sha256": manifest.get("combined_sha256"),
    }
