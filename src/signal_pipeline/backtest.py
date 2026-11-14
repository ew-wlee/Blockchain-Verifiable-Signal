"""Deterministic, long-only, next-period backtest over frozen Step 1 signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Mapping, Sequence

import pandas as pd

from .data import DATASET_COLUMNS
from .hashing import ArtifactError
from .signals import SIGNAL_COLUMNS

UTC = timezone.utc
SUMMARY_SCHEMA_VERSION = "step2-backtest-summary-v1"
CHECKS_SCHEMA_VERSION = "step2-backtest-checks-v1"
TIMESERIES_COLUMNS = (
    "signal_timestamp_utc",
    "symbol",
    "split",
    "signal",
    "previous_position",
    "position",
    "open",
    "close",
    "period_return",
    "cost",
    "net_return",
    "equity",
)
DECIMAL_PLACES = 12
_QUANTUM = Decimal(1).scaleb(-DECIMAL_PLACES)


class BacktestError(RuntimeError):
    """Raised when a frozen backtest invariant does not hold."""


@dataclass(frozen=True)
class BacktestResult:
    summary: dict[str, Any]
    checks: dict[str, Any]
    timeseries: pd.DataFrame


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _q(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)


def _text(value: Decimal) -> str:
    return f"{_q(value):.{DECIMAL_PLACES}f}"


def _period_return(open_price: Decimal, close_price: Decimal) -> Decimal:
    if open_price <= 0 or close_price <= 0:
        raise BacktestError("backtest prices must be positive")
    return _q(close_price / open_price - Decimal(1))


def _split_row(
    symbol: str,
    split: str,
    subset: pd.DataFrame,
    *,
    dataset_lookup: Mapping[tuple[str, str], tuple[str, str]],
    fee_rate: Decimal,
    initial_position: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    equity = Decimal(1)
    peak = Decimal(1)
    max_drawdown = Decimal(0)
    previous_position = initial_position
    trade_count = 0
    total_cost = Decimal(0)
    exposure_hours = 0
    positive_periods = 0
    positions_sum = 0
    rows: list[dict[str, Any]] = []
    times = [_parse_time(value) for value in subset["signal_timestamp_utc"]]

    for index, (_, signal_row) in enumerate(subset.iterrows()):
        timestamp_text = str(signal_row["signal_timestamp_utc"])
        timestamp = times[index]
        if timestamp.minute or timestamp.second:
            raise BacktestError(f"signal timestamp is not hourly: {timestamp_text}")
        if index and timestamp - times[index - 1] != timedelta(hours=1):
            raise BacktestError(
                f"non-consecutive backtest timestamps for {symbol}/{split}: "
                f"{timestamp_text}"
            )
        if timestamp != _parse_time(str(signal_row["context_end_timestamp_utc"])) + timedelta(hours=1):
            raise BacktestError(
                f"signal is not aligned to the next hourly period: {timestamp_text}"
            )
        lookup_key = (symbol, timestamp_text)
        if lookup_key not in dataset_lookup:
            raise BacktestError(f"dataset is missing {symbol} at {timestamp_text}")
        open_text, close_text = dataset_lookup[lookup_key]
        open_decimal = Decimal(open_text)
        close_decimal = Decimal(close_text)

        label = str(signal_row["signal"])
        if label == "buy":
            target_position = 1
        elif label == "sell":
            target_position = 0
        elif label == "hold":
            target_position = previous_position
        else:
            raise BacktestError(f"unknown signal label: {label!r}")
        if target_position not in (0, 1):
            raise BacktestError("long-only backtest produced a short position")

        changed = target_position != previous_position
        cost = fee_rate if changed else Decimal(0)
        period_return = _period_return(open_decimal, close_decimal)
        net_return = _q(
            Decimal(target_position) * period_return - cost
        )
        if changed:
            trade_count += 1
        total_cost += cost
        exposure_hours += target_position
        positions_sum += target_position
        if net_return > 0:
            positive_periods += 1
        equity = _q(equity * (Decimal(1) + net_return))
        if equity > peak:
            peak = equity
        drawdown = _q((peak - equity) / peak)
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        rows.append(
            {
                "signal_timestamp_utc": timestamp_text,
                "symbol": symbol,
                "split": split,
                "signal": label,
                "previous_position": str(previous_position),
                "position": str(target_position),
                "open": open_text,
                "close": close_text,
                "period_return": _text(period_return),
                "cost": _text(cost),
                "net_return": _text(net_return),
                "equity": _text(equity),
            }
        )
        previous_position = target_position

    first_open = Decimal(dataset_lookup[(symbol, str(subset.iloc[0]["signal_timestamp_utc"]))][0])
    last_close = Decimal(dataset_lookup[(symbol, str(subset.iloc[-1]["signal_timestamp_utc"]))][1])
    buy_hold_return = _q(last_close / first_open - Decimal(1))
    periods = len(subset)
    metrics = {
        "periods": periods,
        "trade_count": trade_count,
        "cost_paid": _text(total_cost),
        "cumulative_return": _text(equity - Decimal(1)),
        "buy_hold_return": _text(buy_hold_return),
        "max_drawdown": _text(max_drawdown),
        "final_position": previous_position,
        "exposure_hours": exposure_hours,
        "average_position": _text(Decimal(positions_sum) / Decimal(periods)),
        "positive_periods": positive_periods,
        "first_signal_timestamp_utc": str(subset.iloc[0]["signal_timestamp_utc"]),
        "last_signal_timestamp_utc": str(subset.iloc[-1]["signal_timestamp_utc"]),
    }
    return metrics, rows


def run_backtest(
    dataset: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    symbols: Sequence[str],
    splits: Mapping[str, Mapping[str, Any]],
    fee_bps: int,
    initial_position: int = 0,
    expected_tau: str,
    input_metadata: Mapping[str, Any] | None = None,
) -> BacktestResult:
    if list(dataset.columns) != list(DATASET_COLUMNS):
        raise BacktestError("dataset columns do not match the frozen schema")
    if list(signals.columns) != list(SIGNAL_COLUMNS):
        raise BacktestError("signals columns do not match the frozen schema")
    if fee_bps < 0:
        raise BacktestError("fee_bps must be non-negative")
    if initial_position not in (0, 1):
        raise BacktestError("initial position must be 0 or 1 for a long-only backtest")
    if set(signals["tau"]) != {expected_tau}:
        raise BacktestError("signals do not carry the frozen tau")

    dataset_lookup: dict[tuple[str, str], tuple[str, str]] = {}
    for row in dataset.to_dict(orient="records"):
        key = (str(row["symbol"]), str(row["timestamp_utc"]))
        if key in dataset_lookup:
            raise BacktestError(f"duplicate dataset key: {key}")
        dataset_lookup[key] = (str(row["open"]), str(row["close"]))

    split_names = tuple(splits)
    if set(split_names) != {"validation", "test"}:
        raise BacktestError("backtest requires validation and test splits")
    if set(signals["split"]) != {"validation", "test"}:
        raise BacktestError("signals must contain validation and test rows")

    fee_rate = _q(Decimal(fee_bps) / Decimal(10_000))
    details: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    results: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []

    for split in ("validation", "test"):
        start = _parse_time(str(splits[split]["start_utc"]))
        end = _parse_time(str(splits[split]["end_utc"]))
        expected_hours = int((end - start).total_seconds() // 3600)
        results[split] = {}
        for symbol in symbols:
            subset = signals.loc[
                (signals["split"] == split) & (signals["symbol"] == symbol)
            ].copy()
            subset["_timestamp"] = [
                _parse_time(value) for value in subset["signal_timestamp_utc"]
            ]
            subset = subset.sort_values("_timestamp").drop(columns=["_timestamp"])
            if len(subset) != expected_hours:
                raise BacktestError(
                    f"{symbol}/{split} has {len(subset)} signals, "
                    f"expected {expected_hours}"
                )
            if not all(start <= _parse_time(value) < end for value in subset["signal_timestamp_utc"]):
                raise BacktestError(f"{symbol}/{split} contains an out-of-split signal")
            metrics, rows = _split_row(
                symbol,
                split,
                subset.reset_index(drop=True),
                dataset_lookup=dataset_lookup,
                fee_rate=fee_rate,
                initial_position=initial_position,
            )
            results[split][symbol] = metrics
            all_rows.extend(rows)

    timeseries = pd.DataFrame(all_rows, columns=TIMESERIES_COLUMNS)
    if len(timeseries) != len(signals):
        raise BacktestError("backtest timeseries row count mismatch")

    signal_counts = {
        split: {
            label: int(
                (
                    (signals["split"] == split) & (signals["signal"] == label)
                ).sum()
            )
            for label in ("buy", "hold", "sell")
        }
        for split in ("validation", "test")
    }
    no_short = all(
        int(value) in (0, 1) for value in timeseries["position"]
    ) and all(
        int(value) in (0, 1) for value in timeseries["previous_position"]
    )
    fee_rows_ok = True
    for row in timeseries.to_dict(orient="records"):
        expected_cost = (
            fee_rate
            if int(row["position"]) != int(row["previous_position"])
            else Decimal(0)
        )
        fee_rows_ok = fee_rows_ok and Decimal(row["cost"]) == _q(expected_cost)
    expected_rows = sum(
        int(splits[split]["hours_per_symbol"]) * len(symbols)
        for split in ("validation", "test")
    )
    checks = {
        "signal_count_matches_frozen_splits": len(signals) == expected_rows,
        "validation_and_test_are_separate": True,
        "signal_executes_in_same_target_bar": True,
        "hourly_signal_continuity": True,
        "long_only_positions": no_short,
        "ten_bps_charged_only_on_position_change": fee_rows_ok,
        "frozen_tau_not_reselected": True,
        "all_signal_labels_accounted_for": signal_counts,
    }
    checks_passed = (
        checks["signal_count_matches_frozen_splits"]
        and checks["validation_and_test_are_separate"]
        and checks["signal_executes_in_same_target_bar"]
        and checks["hourly_signal_continuity"]
        and checks["long_only_positions"]
        and checks["ten_bps_charged_only_on_position_change"]
        and checks["frozen_tau_not_reselected"]
    )
    if not checks_passed:
        raise BacktestError("one or more non-leakage backtest checks failed")

    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "rules": {
            "execution": "same_bar_open_to_close",
            "signal_timestamp_semantics": (
                "signal at t is produced from context ending at t-1h and "
                "executes at t open; realized return is close_t/open_t-1"
            ),
            "position_mapping": {
                "buy": 1,
                "sell": 0,
                "hold": "carry previous position",
            },
            "long_only": True,
            "initial_position": initial_position,
            "fee_bps": fee_bps,
            "fee_rate": _text(fee_rate),
            "fee_rule": "charged only when target position changes",
            "split_handling": "validation and test independently start flat",
            "tau_reselection": False,
            "tau": expected_tau,
        },
        "input": dict(input_metadata or {}),
        "results": results,
        "signal_counts": signal_counts,
        "checks": {
            key: value
            for key, value in checks.items()
            if not isinstance(value, dict)
        },
        "passed": True,
    }
    detail_checks = [
        {
            "name": "signal_count_matches_frozen_splits",
            "passed": checks["signal_count_matches_frozen_splits"],
            "details": {"signals": len(signals), "expected": expected_rows},
        },
        {
            "name": "validation_and_test_are_separate",
            "passed": True,
            "details": signal_counts,
        },
        {
            "name": "signal_executes_in_same_target_bar",
            "passed": True,
            "details": "t signal from t-1h context, executed at t open",
        },
        {
            "name": "hourly_signal_continuity",
            "passed": True,
            "details": "all split/symbol timestamps checked for hourly continuity",
        },
        {
            "name": "long_only_positions",
            "passed": no_short,
            "details": "positions restricted to 0 or 1",
        },
        {
            "name": "ten_bps_charged_only_on_position_change",
            "passed": fee_rows_ok,
            "details": {"fee_bps": fee_bps},
        },
        {
            "name": "frozen_tau_not_reselected",
            "passed": True,
            "details": {"tau": expected_tau},
        },
    ]
    check_report = {
        "schema_version": CHECKS_SCHEMA_VERSION,
        "checks": detail_checks,
        "passed": all(item["passed"] for item in detail_checks),
        "summary_sha256_note": "computed from final backtest_summary.json bytes",
    }
    return BacktestResult(summary=summary, checks=check_report, timeseries=timeseries)


def ensure_backtest_passed(result: BacktestResult) -> None:
    if not result.summary.get("passed") or not result.checks.get("passed"):
        raise ArtifactError("backtest did not pass its required checks")
