"""Canonical six-file bundle, payload commitment, objectId, and manifestHash."""

from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .hashing import (
    ArtifactError,
    canonical_json_bytes,
    combined_hash,
    hash_relative_files,
    read_json,
    sha256_bytes,
    sha256_file,
    write_bytes,
    write_json,
)

BUNDLE_SCHEMA_VERSION = "verifiable-signal-bundle-v1"
BUNDLE_CONFIG_SCHEMA_VERSION = "step2-bundle-config-v1"
BUNDLE_FILE_NAMES = (
    "backtest_summary.json",
    "config.json",
    "dataset.csv",
    "manifest.json",
    "model_reference.json",
    "signals.csv",
)
CONTENT_FILE_NAMES = tuple(
    name for name in BUNDLE_FILE_NAMES if name != "manifest.json"
)


class BundleError(RuntimeError):
    """Raised when a bundle cannot be generated or violates its schema."""


@dataclass(frozen=True)
class Step1BundleInput:
    dataset_path: Path
    signals_path: Path
    model_reference_path: Path
    step1_config: Mapping[str, Any]
    step1_config_sha256: str
    step1_artifact_manifest_sha256: str
    step1_code_commit: str
    dataset_sha256: str
    signal_sha256: str
    model_reference_sha256: str
    tau: str
    tau_decimals: int
    model_id: str
    model_revision: str
    checkpoint_commitment_sha256: str


@dataclass(frozen=True)
class Step2BundleRules:
    step2_config_sha256: str
    bundle_schema_version: str
    fee_bps: int
    initial_position: int
    long_only: bool
    execution: str
    splits_independent: bool


@dataclass(frozen=True)
class BundleResult:
    bundle_dir: Path
    manifest: dict[str, Any]
    files: tuple[dict[str, Any], ...]
    payload_commitment: str
    object_id: str
    manifest_sha256: str
    config_sha256: str
    backtest_summary_sha256: str


def _assert_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise BundleError(f"{field} must be a lowercase 64-character SHA256")


def compute_payload_commitment(files: Sequence[Mapping[str, Any]]) -> str:
    return combined_hash(files)


def manifest_identity_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    identity = copy.deepcopy(dict(manifest))
    identity.pop("object_id", None)
    return identity


def compute_object_id(manifest: Mapping[str, Any]) -> str:
    identity = manifest_identity_view(manifest)
    return "0x" + sha256_bytes(canonical_json_bytes(identity))


def _build_bundle_config(
    source: Step1BundleInput,
    rules: Step2BundleRules,
    *,
    code_commit: str,
    backtest_summary_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": BUNDLE_CONFIG_SCHEMA_VERSION,
        "code_commit": code_commit,
        "step1_code_commit": source.step1_code_commit,
        "step2_rules": {
            "step2_input_config_sha256": rules.step2_config_sha256,
            "fee_bps": rules.fee_bps,
            "initial_position": rules.initial_position,
            "long_only": rules.long_only,
            "execution": rules.execution,
            "splits_independent": rules.splits_independent,
            "bundle_schema_version": rules.bundle_schema_version,
        },
        "step1": {
            "frozen_config": dict(source.step1_config),
            "frozen_config_sha256": source.step1_config_sha256,
            "artifact_manifest_sha256": source.step1_artifact_manifest_sha256,
            "dataset_sha256": source.dataset_sha256,
            "signal_sha256": source.signal_sha256,
            "model_reference_sha256": source.model_reference_sha256,
            "tau": source.tau,
        },
        "backtest_summary_sha256": backtest_summary_sha256,
        "canonical_rules": {
            "json": (
                "UTF-8, lexicographically sorted keys, compact ',': separators, "
                "single trailing LF"
            ),
            "csv": (
                "frozen Step 1 CSV bytes are copied byte-for-byte; no re-serialization"
            ),
            "relative_paths": "POSIX paths relative to bundle root",
            "files_order": "ascending POSIX path",
            "sha256": "computed over final saved file bytes",
            "payload_commitment": (
                "SHA256 over canonical JSON of the sorted files[] entries"
            ),
            "object_id": (
                "0x + SHA256 over canonical JSON of the final manifest "
                "identity view with object_id removed"
            ),
            "manifest_hash": (
                "SHA256 over final manifest.json bytes; stored outside the bundle"
            ),
        },
        "required_bundle_files": list(BUNDLE_FILE_NAMES),
    }


def _build_manifest(
    source: Step1BundleInput,
    rules: Step2BundleRules,
    *,
    code_commit: str,
    config_sha256: str,
    backtest_summary_sha256: str,
    files: Sequence[Mapping[str, Any]],
    payload_commitment: str,
) -> dict[str, Any]:
    config_data = source.step1_config["data"]
    return {
        "schema_version": rules.bundle_schema_version,
        "code_commit": code_commit,
        "step1": {
            "code_commit": source.step1_code_commit,
            "config_sha256": source.step1_config_sha256,
            "artifact_manifest_sha256": source.step1_artifact_manifest_sha256,
        },
        "data": {
            "source": config_data["source"],
            "window_start_utc": config_data["window_start_utc"],
            "window_end_utc": config_data["window_end_utc"],
            "symbols": list(config_data["symbols"]),
        },
        "model": {
            "model_id": source.model_id,
            "revision": source.model_revision,
            "checkpoint_commitment_sha256": source.checkpoint_commitment_sha256,
        },
        "signals": {
            "tau": source.tau,
            "tau_decimals": source.tau_decimals,
        },
        "backtest": {
            "fee_bps": rules.fee_bps,
            "execution": rules.execution,
            "initial_position": rules.initial_position,
            "long_only": rules.long_only,
            "splits_independent": rules.splits_independent,
        },
        "config_sha256": config_sha256,
        "dataset_sha256": source.dataset_sha256,
        "signal_sha256": source.signal_sha256,
        "model_reference_sha256": source.model_reference_sha256,
        "backtest_summary_sha256": backtest_summary_sha256,
        "files": [dict(item) for item in files],
        "payload_commitment": payload_commitment,
    }


def create_bundle(
    bundle_dir: Path,
    source: Step1BundleInput,
    rules: Step2BundleRules,
    *,
    code_commit: str,
    backtest_summary: Mapping[str, Any],
) -> BundleResult:
    """Create the canonical five content files and the self-contained manifest."""
    bundle_dir = Path(bundle_dir)
    if bundle_dir.exists() and any(bundle_dir.iterdir()):
        raise BundleError(f"refusing to overwrite non-empty bundle: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if len(code_commit) != 40:
        raise BundleError("bundle code_commit must be a full 40-character SHA")
    for value, field in (
        (source.step1_config_sha256, "step1_config_sha256"),
        (source.step1_artifact_manifest_sha256, "step1_artifact_manifest_sha256"),
        (source.dataset_sha256, "dataset_sha256"),
        (source.signal_sha256, "signal_sha256"),
        (source.model_reference_sha256, "model_reference_sha256"),
        (source.checkpoint_commitment_sha256, "checkpoint_commitment_sha256"),
        (rules.step2_config_sha256, "step2_config_sha256"),
    ):
        _assert_sha256(value, field)
    if source.step1_config.get("schema_version") != "step1-frozen-v1":
        raise BundleError("Step 1 frozen config schema is not supported")

    copied: dict[str, bytes] = {}
    for name, path in (
        ("dataset.csv", source.dataset_path),
        ("signals.csv", source.signals_path),
        ("model_reference.json", source.model_reference_path),
    ):
        payload = Path(path).read_bytes()
        write_bytes(bundle_dir / name, payload)
        copied[name] = payload
    if sha256_bytes(copied["dataset.csv"]) != source.dataset_sha256:
        raise BundleError("dataset bytes do not match the frozen Step 1 hash")
    if sha256_bytes(copied["signals.csv"]) != source.signal_sha256:
        raise BundleError("signals bytes do not match the frozen Step 1 hash")
    if sha256_bytes(copied["model_reference.json"]) != source.model_reference_sha256:
        raise BundleError("model reference bytes do not match the frozen Step 1 hash")

    summary_payload = canonical_json_bytes(dict(backtest_summary))
    write_bytes(bundle_dir / "backtest_summary.json", summary_payload)
    backtest_summary_sha256 = sha256_bytes(summary_payload)
    bundle_config = _build_bundle_config(
        source,
        rules,
        code_commit=code_commit,
        backtest_summary_sha256=backtest_summary_sha256,
    )
    write_json(bundle_dir / "config.json", bundle_config)
    config_sha256 = sha256_file(bundle_dir / "config.json")

    file_entries = hash_relative_files(
        [bundle_dir / name for name in CONTENT_FILE_NAMES], bundle_dir
    )
    if [entry["path"] for entry in file_entries] != list(CONTENT_FILE_NAMES):
        raise BundleError("bundle file order is not the fixed sorted order")
    payload_commitment = compute_payload_commitment(file_entries)
    manifest = _build_manifest(
        source,
        rules,
        code_commit=code_commit,
        config_sha256=config_sha256,
        backtest_summary_sha256=backtest_summary_sha256,
        files=file_entries,
        payload_commitment=payload_commitment,
    )
    object_id = compute_object_id(manifest)
    manifest["object_id"] = object_id
    manifest_path = bundle_dir / "manifest.json"
    write_json(manifest_path, manifest)
    if set(path.name for path in bundle_dir.iterdir() if path.is_file()) != set(
        BUNDLE_FILE_NAMES
    ):
        raise BundleError("bundle does not contain exactly the six required files")
    return BundleResult(
        bundle_dir=bundle_dir,
        manifest=manifest,
        files=tuple(file_entries),
        payload_commitment=payload_commitment,
        object_id=object_id,
        manifest_sha256=sha256_file(manifest_path),
        config_sha256=config_sha256,
        backtest_summary_sha256=backtest_summary_sha256,
    )


def load_manifest(bundle_dir: Path) -> dict[str, Any]:
    path = Path(bundle_dir) / "manifest.json"
    if not path.is_file():
        raise BundleError(f"manifest is missing: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise BundleError("manifest must be a JSON object")
    return payload


def copy_bundle(source: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise BundleError(f"refusing to overwrite bundle copy: {destination}")
    shutil.copytree(source, destination, dirs_exist_ok=True)
