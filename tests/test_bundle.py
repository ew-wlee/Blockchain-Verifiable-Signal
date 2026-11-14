from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from signal_pipeline.bundle import (
    BUNDLE_FILE_NAMES,
    BundleError,
    Step1BundleInput,
    Step2BundleRules,
    compute_object_id,
    create_bundle,
    manifest_identity_view,
)
from signal_pipeline.hashing import canonical_json_bytes, sha256_bytes, sha256_file

CODE_COMMIT = "1" * 40


def _source(tmp_path: Path) -> Step1BundleInput:
    dataset = tmp_path / "dataset.csv"
    signals = tmp_path / "signals.csv"
    model = tmp_path / "model_reference.json"
    dataset.write_bytes(b"timestamp_utc,symbol,open,high,low,close,volume,close_time_utc\n")
    signals.write_bytes(b"signal_timestamp_utc,symbol,split,signal\n")
    model.write_bytes(canonical_json_bytes({"model_id": "amazon/chronos-t5-small"}))
    step1_config = {
        "schema_version": "step1-frozen-v1",
        "code_commit": "a" * 40,
        "data": {
            "source": "Binance Spot",
            "window_start_utc": "2024-01-01T00:00:00Z",
            "window_end_utc": "2025-01-01T00:00:00Z",
            "symbols": ["BTCUSDT", "ETHUSDT"],
        },
        "model": {
            "id": "amazon/chronos-t5-small",
            "resolved_revision": "b" * 40,
        },
        "signals": {"tau": "0.001923089035", "tau_decimals": 12},
    }
    return Step1BundleInput(
        dataset_path=dataset,
        signals_path=signals,
        model_reference_path=model,
        step1_config=step1_config,
        step1_config_sha256="c" * 64,
        step1_artifact_manifest_sha256="d" * 64,
        step1_code_commit="a" * 40,
        dataset_sha256=sha256_file(dataset),
        signal_sha256=sha256_file(signals),
        model_reference_sha256=sha256_file(model),
        tau="0.001923089035",
        tau_decimals=12,
        model_id="amazon/chronos-t5-small",
        model_revision="b" * 40,
        checkpoint_commitment_sha256="e" * 64,
    )


def _rules() -> Step2BundleRules:
    return Step2BundleRules(
        step2_config_sha256="f" * 64,
        bundle_schema_version="verifiable-signal-bundle-v1",
        fee_bps=10,
        initial_position=0,
        long_only=True,
        execution="same_bar_open_to_close",
        splits_independent=True,
    )


def _summary():
    return {
        "schema_version": "step2-backtest-summary-v1",
        "rules": {"fee_bps": 10},
        "results": {},
        "passed": True,
    }


def _build(tmp_path: Path, name: str = "bundle"):
    bundle_dir = tmp_path / name
    result = create_bundle(
        bundle_dir,
        _source(tmp_path),
        _rules(),
        code_commit=CODE_COMMIT,
        backtest_summary=_summary(),
    )
    return bundle_dir, result


def test_bundle_has_exactly_six_files_and_deterministic_identity(tmp_path):
    bundle_dir, result = _build(tmp_path)
    assert sorted(path.name for path in bundle_dir.iterdir()) == list(
        sorted(BUNDLE_FILE_NAMES)
    )
    manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
    assert manifest_bytes == canonical_json_bytes(
        json.loads(manifest_bytes.decode("utf-8"))
    )
    manifest = json.loads(manifest_bytes)
    assert manifest["object_id"] == result.object_id
    assert manifest["object_id"] == compute_object_id(manifest)
    assert manifest["object_id"].startswith("0x")
    assert len(manifest["object_id"]) == 66
    assert manifest["payload_commitment"] == result.payload_commitment
    assert manifest["config_sha256"] == sha256_file(bundle_dir / "config.json")
    assert manifest["backtest_summary_sha256"] == sha256_file(
        bundle_dir / "backtest_summary.json"
    )
    assert manifest["dataset_sha256"] == sha256_file(bundle_dir / "dataset.csv")
    assert manifest["signal_sha256"] == sha256_file(bundle_dir / "signals.csv")
    assert manifest["model_reference_sha256"] == sha256_file(
        bundle_dir / "model_reference.json"
    )
    assert result.manifest_sha256 == sha256_file(bundle_dir / "manifest.json")
    assert "manifest_hash" not in manifest
    assert "manifest_sha256" not in manifest


def test_object_identity_view_removes_only_object_id(tmp_path):
    _, result = _build(tmp_path)
    identity = manifest_identity_view(result.manifest)
    assert "object_id" not in identity
    expected = "0x" + sha256_bytes(canonical_json_bytes(identity))
    assert result.object_id == expected


def test_bundle_build_is_byte_identical(tmp_path):
    first_dir, first = _build(tmp_path, "bundle-one")
    second_dir, second = _build(tmp_path, "bundle-two")
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.object_id == second.object_id
    for name in BUNDLE_FILE_NAMES:
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()


def test_step1_core_files_are_copied_byte_for_byte(tmp_path):
    source = _source(tmp_path)
    bundle_dir, _ = _build(tmp_path)
    assert (bundle_dir / "dataset.csv").read_bytes() == source.dataset_path.read_bytes()
    assert (bundle_dir / "signals.csv").read_bytes() == source.signals_path.read_bytes()
    assert (
        bundle_dir / "model_reference.json"
    ).read_bytes() == source.model_reference_path.read_bytes()


def test_bundle_refuses_to_overwrite(tmp_path):
    bundle_dir, _ = _build(tmp_path)
    with pytest.raises(BundleError, match="refusing to overwrite"):
        create_bundle(
            bundle_dir,
            _source(tmp_path),
            _rules(),
            code_commit=CODE_COMMIT,
            backtest_summary=_summary(),
        )


def test_wrong_source_hash_is_rejected(tmp_path):
    source = _source(tmp_path)
    broken = replace(source, dataset_sha256="0" * 64)
    with pytest.raises(BundleError, match="dataset bytes"):
        create_bundle(
            tmp_path / "broken-bundle",
            broken,
            _rules(),
            code_commit=CODE_COMMIT,
            backtest_summary=_summary(),
        )
