from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from signal_pipeline.inference import PREDICTION_COLUMNS
from signal_pipeline.signals import (
    SignalError,
    boundary_cases,
    classify_return,
    freeze_tau,
    generate_signals,
    signal_counts,
)


def _returns_frame(returns, config, splits=None):
    rows = []
    splits = splits or ["validation"] * len(returns)
    for index, (value, split) in enumerate(zip(returns, splits)):
        target = config.validation.start + timedelta(hours=index)
        rows.append(
            {
                "target_timestamp_utc": target.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "symbol": config.symbols[index % len(config.symbols)],
                "split": split,
                "context_start_timestamp_utc": (
                    target - timedelta(hours=64)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "context_end_timestamp_utc": (target - timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "current_close": "100.00000000",
                "predicted_close_median": "101.00000000",
                "predicted_return": f"{Decimal(str(value)):.12f}",
                "num_samples": config.num_samples,
                "seed": config.seed,
            }
        )
    return pd.DataFrame(rows, columns=PREDICTION_COLUMNS)


def test_classification_boundaries_are_hold():
    tau = Decimal("0.010000000000")
    assert classify_return(tau, tau) == "hold"
    assert classify_return(-tau, tau) == "hold"
    assert classify_return(tau + Decimal("0.000000000001"), tau) == "buy"
    assert classify_return(-tau - Decimal("0.000000000001"), tau) == "sell"


def test_boundary_cases_cover_all_labels():
    labels = {expected for _, _, expected in boundary_cases(Decimal("0.01"))}
    assert labels == {"buy", "sell", "hold"}


def test_tau_is_median_absolute_validation_return(base_config):
    frame = _returns_frame([0.01, -0.01, 0.03, -0.03], base_config)
    tau, report = freeze_tau(frame, config=base_config)
    assert tau == Decimal("0.020000000000")
    assert report["input_row_count"] == 4
    assert report["tau_inputs"]["test_rows_supplied"] == 0


def test_tau_rejects_test_rows(base_config):
    frame = _returns_frame(
        [0.01, -0.01, 100.0, -100.0],
        base_config,
        splits=["validation", "validation", "test", "test"],
    )
    with pytest.raises(SignalError, match="validation"):
        freeze_tau(frame, config=base_config)


def test_validation_only_tau_ignores_unseen_test_predictions(base_config):
    validation = _returns_frame([0.01, -0.01, 0.02, -0.02], base_config)
    first_test = _returns_frame(
        [1000.0, -1000.0], base_config, splits=["test", "test"]
    )
    second_test = _returns_frame(
        [-1000.0, 1000.0], base_config, splits=["test", "test"]
    )
    tau_one, _ = freeze_tau(validation, config=base_config)
    tau_two, _ = freeze_tau(validation, config=base_config)
    assert first_test["predicted_return"].tolist() != second_test[
        "predicted_return"
    ].tolist()
    assert tau_one == tau_two == Decimal("0.015000000000")


def test_signal_generation_equal_threshold_holds(base_config):
    tau = Decimal("0.010000000000")
    returns = [
        tau,
        -tau,
        tau + Decimal("0.000000000001"),
        -tau - Decimal("0.000000000001"),
        Decimal(0),
    ]
    frame = _returns_frame(
        returns,
        base_config,
        splits=["validation", "validation", "test", "test", "test"],
    )
    signals = generate_signals(frame, tau=tau, config=base_config)
    assert list(signals["signal"]) == ["hold", "hold", "buy", "sell", "hold"]
    assert list(signals["signal_timestamp_utc"]) == list(
        frame["target_timestamp_utc"]
    )
    assert list(signals["context_end_timestamp_utc"]) == list(
        frame["context_end_timestamp_utc"]
    )
    assert signals["tau"].unique().tolist() == ["0.010000000000"]
    counts = signal_counts(signals)
    assert counts["test"] == {"buy": 1, "hold": 1, "sell": 1}
