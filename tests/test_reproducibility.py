from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from signal_pipeline.config import load_config
from signal_pipeline.data import DATASET_COLUMNS, clean_records
from signal_pipeline.hashing import (
    canonical_json_bytes,
    sha256_file,
    write_csv_frame,
    write_json,
)
from signal_pipeline.inference import build_prediction_windows
from signal_pipeline.runtime import NetworkBlockedError, block_network
from signal_pipeline.validation import (
    DETERMINISTIC_ARTIFACTS,
    verify_artifact_manifest,
    write_artifact_manifest,
)

from conftest import hourly_rows, tiny_config

UTC = timezone.utc


def test_canonical_json_is_order_independent():
    left = canonical_json_bytes({"b": 2, "a": 1})
    right = canonical_json_bytes({"a": 1, "b": 2})
    assert left == right
    assert left.endswith(b"\n")
    assert left == b'{"a":1,"b":2}\n'


def test_csv_bytes_are_reproducible(tmp_path):
    frame = pd.DataFrame(
        [["2024-01-01T00:00:00Z", "BTCUSDT", "1.00000000"]],
        columns=["timestamp_utc", "symbol", "close"],
    )
    first = tmp_path / "one.csv"
    second = tmp_path / "two.csv"
    write_csv_frame(frame, first, frame.columns)
    write_csv_frame(frame, second, frame.columns)
    assert sha256_file(first) == sha256_file(second)
    assert first.read_bytes().endswith(b"\n")


def test_artifact_manifest_detects_tampering(tmp_path):
    for name in DETERMINISTIC_ARTIFACTS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".json"):
            write_json(path, {"name": name})
        else:
            path.write_text(f"{name}\n", encoding="utf-8", newline="\n")
    manifest_path = write_artifact_manifest(tmp_path)
    assert manifest_path.is_file()
    assert verify_artifact_manifest(tmp_path)["passed"] is True

    signals = tmp_path / "signals.csv"
    signals.write_text("tampered\n", encoding="utf-8")
    result = verify_artifact_manifest(tmp_path)
    assert result["passed"] is False
    assert any(item.startswith("sha256:signals.csv") for item in result["failures"])


def test_offline_guard_blocks_socket_connections():
    with block_network():
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("example.com", 80), timeout=0.01)
        with pytest.raises(NetworkBlockedError):
            socket.socket().connect(("example.com", 80))


def test_fixed_config_has_expected_windows():
    config = load_config(Path("configs/step1.yaml"))
    assert config.window_start.isoformat() == "2024-01-01T00:00:00+00:00"
    assert config.window_end.isoformat() == "2025-01-01T00:00:00+00:00"
    assert config.validation.start.isoformat() == "2024-07-01T00:00:00+00:00"
    assert config.test.start.isoformat() == "2024-10-01T00:00:00+00:00"
    assert config.expected_hours == 8784
    assert config.expected_rows == 17568
    assert config.context_length == 64
    assert config.prediction_length == 1


def test_prediction_windows_target_next_hour(tmp_path, base_config):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    config = tiny_config(
        tmp_path,
        base_config,
        start=start,
        end=start + timedelta(hours=66),
    )
    rows = hourly_rows(start, 66)
    frame, quality = clean_records(
        [("BTCUSDT", row) for row in rows],
        symbols=config.symbols,
        window_start=config.window_start,
        window_end=config.window_end,
    )
    assert quality["passed"] is True
    windows = build_prediction_windows(frame, config)
    assert len(windows) == 2
    for window in windows:
        assert window.context_end == window.target_timestamp - timedelta(hours=1)
        assert window.context_start == window.target_timestamp - timedelta(
            hours=64
        )
        assert len(window.context) == 64
    assert list(frame.columns) == list(DATASET_COLUMNS)
