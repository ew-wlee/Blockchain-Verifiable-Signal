from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from signal_pipeline.backtest import BacktestError, run_backtest
from signal_pipeline.data import DATASET_COLUMNS
from signal_pipeline.signals import SIGNAL_COLUMNS

UTC = timezone.utc
TAU = "0.001923089035"
VALIDATION_START = datetime(2024, 7, 1, tzinfo=UTC)
TEST_START = datetime(2024, 10, 1, tzinfo=UTC)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dataset(rows):
    records = []
    for timestamp, open_price, close_price in rows:
        records.append(
            {
                "timestamp_utc": _timestamp(timestamp),
                "symbol": "BTCUSDT",
                "open": f"{open_price:.8f}",
                "high": f"{max(open_price, close_price) + 1:.8f}",
                "low": f"{min(open_price, close_price) - 1:.8f}",
                "close": f"{close_price:.8f}",
                "volume": "1.00000000",
                "close_time_utc": timestamp.strftime("%Y-%m-%dT%H:59:59.999Z"),
            }
        )
    return pd.DataFrame(records, columns=DATASET_COLUMNS)


def _signals(rows):
    records = []
    for timestamp, split, label in rows:
        records.append(
            {
                "signal_timestamp_utc": _timestamp(timestamp),
                "symbol": "BTCUSDT",
                "split": split,
                "context_end_timestamp_utc": _timestamp(timestamp - timedelta(hours=1)),
                "current_close": "100.00000000",
                "predicted_close_median": "101.00000000",
                "predicted_return": "0.010000000000",
                "tau": TAU,
                "signal": label,
            }
        )
    return pd.DataFrame(records, columns=SIGNAL_COLUMNS)


def _inputs(val_labels, val_prices, test_labels, test_prices):
    rows = []
    signals = []
    for index, (open_price, close_price) in enumerate(val_prices):
        timestamp = VALIDATION_START + timedelta(hours=index)
        rows.append((timestamp, open_price, close_price))
        signals.append((timestamp, "validation", val_labels[index]))
    for index, (open_price, close_price) in enumerate(test_prices):
        timestamp = TEST_START + timedelta(hours=index)
        rows.append((timestamp, open_price, close_price))
        signals.append((timestamp, "test", test_labels[index]))
    splits = {
        "validation": {
            "start_utc": _timestamp(VALIDATION_START),
            "end_utc": _timestamp(VALIDATION_START + timedelta(hours=len(val_prices))),
            "hours_per_symbol": len(val_prices),
        },
        "test": {
            "start_utc": _timestamp(TEST_START),
            "end_utc": _timestamp(TEST_START + timedelta(hours=len(test_prices))),
            "hours_per_symbol": len(test_prices),
        },
    }
    return _dataset(rows), _signals(signals), splits


def _run(*args, fee_bps=10, expected_tau=TAU):
    dataset, signals, splits = _inputs(*args)
    return run_backtest(
        dataset,
        signals,
        symbols=("BTCUSDT",),
        splits=splits,
        fee_bps=fee_bps,
        initial_position=0,
        expected_tau=expected_tau,
        input_metadata={"tau": expected_tau, "dataset_sha256": "a" * 64},
    )


def test_buy_hold_positions_and_one_entry_fee():
    result = _run(
        ["buy", "hold", "hold"],
        [(100.0, 110.0), (110.0, 121.0), (121.0, 133.1)],
        ["hold"],
        [(100.0, 100.0)],
    )
    val = result.summary["results"]["validation"]["BTCUSDT"]
    assert val["periods"] == 3
    assert val["trade_count"] == 1
    assert val["final_position"] == 1
    assert val["cost_paid"] == "0.001000000000"
    assert list(result.timeseries.loc[result.timeseries["split"] == "validation", "position"]) == [
        "1",
        "1",
        "1",
    ]
    first = result.timeseries.iloc[0]
    assert first["period_return"] == "0.100000000000"
    assert first["net_return"] == "0.099000000000"
    assert first["equity"] == "1.099000000000"
    assert result.summary["input"]["tau"] == TAU


def test_sell_exits_without_shorting_and_pays_exit_fee():
    result = _run(
        ["buy", "sell", "hold"],
        [(100.0, 100.0), (100.0, 100.0), (100.0, 100.0)],
        ["hold"],
        [(100.0, 100.0)],
    )
    val = result.timeseries.loc[result.timeseries["split"] == "validation"]
    assert list(val["position"]) == ["1", "0", "0"]
    assert list(val["net_return"]) == [
        "-0.001000000000",
        "-0.001000000000",
        "0.000000000000",
    ]
    assert result.summary["results"]["validation"]["BTCUSDT"]["trade_count"] == 2
    assert min(int(value) for value in result.timeseries["position"]) == 0


def test_hold_with_flat_initial_position_has_no_cost():
    result = _run(
        ["hold", "hold"],
        [(100.0, 110.0), (110.0, 121.0)],
        ["hold"],
        [(100.0, 105.0)],
    )
    val = result.timeseries.loc[result.timeseries["split"] == "validation"]
    assert list(val["position"]) == ["0", "0"]
    assert set(val["cost"]) == {"0.000000000000"}
    assert result.summary["results"]["validation"]["BTCUSDT"]["cumulative_return"] == "0.000000000000"


def test_validation_and_test_start_independently_flat():
    result = _run(
        ["buy"],
        [(100.0, 101.0)],
        ["hold", "sell"],
        [(100.0, 101.0), (101.0, 102.0)],
    )
    val = result.timeseries.loc[result.timeseries["split"] == "validation"]
    test = result.timeseries.loc[result.timeseries["split"] == "test"]
    assert val.iloc[0]["previous_position"] == "0"
    assert test.iloc[0]["previous_position"] == "0"
    assert test.iloc[0]["position"] == "0"
    assert test.iloc[0]["cost"] == "0.000000000000"


def test_same_bar_open_to_close_does_not_use_future_bar():
    result = _run(
        ["buy", "buy"],
        [(100.0, 110.0), (500.0, 500.0)],
        ["hold"],
        [(100.0, 100.0)],
    )
    val = result.timeseries.loc[result.timeseries["split"] == "validation"]
    assert val.iloc[0]["period_return"] == "0.100000000000"
    assert val.iloc[1]["period_return"] == "0.000000000000"
    assert val.iloc[0]["open"] == "100.00000000"
    assert val.iloc[0]["close"] == "110.00000000"


def test_wrong_tau_is_rejected():
    with pytest.raises(BacktestError, match="frozen tau"):
        _run(
            ["hold"],
            [(100.0, 100.0)],
            ["hold"],
            [(100.0, 100.0)],
            expected_tau="0.999999999999",
        )


def test_non_consecutive_signal_is_rejected():
    dataset, signals, splits = _inputs(
        ["buy", "hold", "hold"],
        [(100.0, 100.0), (100.0, 100.0), (100.0, 100.0)],
        ["hold"],
        [(100.0, 100.0)],
    )
    signals.loc[1, "signal_timestamp_utc"] = _timestamp(
        VALIDATION_START + timedelta(hours=2)
    )
    signals.loc[1, "context_end_timestamp_utc"] = _timestamp(
        VALIDATION_START + timedelta(hours=1)
    )
    with pytest.raises(BacktestError, match="non-consecutive"):
        run_backtest(
            dataset,
            signals,
            symbols=("BTCUSDT",),
            splits=splits,
            fee_bps=10,
            initial_position=0,
            expected_tau=TAU,
        )


def test_summary_returns_are_fixed_decimal_strings():
    result = _run(
        ["buy"],
        [(3.0, 4.0)],
        ["hold"],
        [(100.0, 100.0)],
    )
    value = result.summary["results"]["validation"]["BTCUSDT"]["cumulative_return"]
    assert value == "0.332333333333"
    assert Decimal(value) == Decimal(value).quantize(Decimal("0.000000000001"))
