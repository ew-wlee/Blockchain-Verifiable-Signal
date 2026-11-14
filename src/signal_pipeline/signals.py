"""Validation-only tau freezing and deterministic buy/hold/sell signals."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Sequence

import pandas as pd

from .config import Step1Config
from .hashing import ArtifactError

TAU_REPORT_SCHEMA_VERSION = "step1-tau-freeze-v1"
SIGNAL_SCHEMA_VERSION = "step1-signals-v1"
SIGNAL_COLUMNS = (
    "signal_timestamp_utc",
    "symbol",
    "split",
    "context_end_timestamp_utc",
    "current_close",
    "predicted_close_median",
    "predicted_return",
    "tau",
    "signal",
)
ALLOWED_SIGNALS = ("buy", "hold", "sell")


class SignalError(RuntimeError):
    """Raised when tau or signal generation violates the frozen rule."""


def _quantize_tau(value: Decimal, places: int) -> Decimal:
    return value.quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN
    )


def classify_return(predicted_return: Decimal, tau: Decimal) -> str:
    if predicted_return > tau:
        return "buy"
    if predicted_return < -tau:
        return "sell"
    return "hold"


def select_tau(
    validation_predictions: pd.DataFrame,
    *,
    rule: str,
    decimals: int,
) -> Decimal:
    """Compute tau from validation rows only; reject any other split."""
    if rule != "median_abs_validation_predicted_return":
        raise SignalError(f"unsupported tau rule: {rule}")
    if validation_predictions.empty:
        raise SignalError("validation predictions are empty")
    if set(validation_predictions["split"]) != {"validation"}:
        raise SignalError("tau is only allowed to consume validation rows")
    absolute_returns = sorted(
        abs(Decimal(str(value))) for value in validation_predictions["predicted_return"]
    )
    count = len(absolute_returns)
    if count % 2 == 1:
        median = absolute_returns[count // 2]
    else:
        median = (absolute_returns[count // 2 - 1] + absolute_returns[count // 2]) / 2
    tau = _quantize_tau(median, decimals)
    if tau <= 0:
        raise SignalError("validation-only tau must be positive")
    return tau


def _tau_report(
    validation_predictions: pd.DataFrame,
    *,
    tau: Decimal,
    decimals: int,
    config: Step1Config,
) -> dict[str, Any]:
    by_symbol: dict[str, Any] = {}
    for symbol in config.symbols:
        subset = validation_predictions.loc[
            validation_predictions["symbol"] == symbol
        ]
        if subset.empty:
            raise SignalError(f"validation predictions are missing {symbol}")
        absolute = [abs(Decimal(str(value))) for value in subset["predicted_return"]]
        by_symbol[symbol] = {
            "row_count": len(subset),
            "abs_return_min": f"{min(absolute):.{decimals}f}",
            "abs_return_max": f"{max(absolute):.{decimals}f}",
        }
    return {
        "schema_version": TAU_REPORT_SCHEMA_VERSION,
        "rule": config.tau_rule,
        "input_split": "validation",
        "input_row_count": len(validation_predictions),
        "input_rows_by_symbol": by_symbol,
        "formula": (
            "median of abs(predicted_return) over both symbols on validation, "
            "computed with Decimal arithmetic"
        ),
        "rounding": f"ROUND_HALF_EVEN to {decimals} decimal places",
        "tau": f"{tau:.{decimals}f}",
        "tau_inputs": {
            "validation_only": True,
            "test_rows_supplied": 0,
            "test_metrics_computed": False,
        },
        "freeze_order": [
            "validation predictions written",
            "tau computed from validation only",
            "tau written into frozen config",
            "test signals generated with frozen tau",
        ],
        "signal_boundaries": {
            "buy": "predicted_return > tau",
            "sell": "predicted_return < -tau",
            "hold": "otherwise, including equality",
        },
    }


def freeze_tau(
    validation_predictions: pd.DataFrame,
    *,
    config: Step1Config,
) -> tuple[Decimal, dict[str, Any]]:
    """Freeze tau from a validation-only frame; test rows never enter this call."""
    if set(validation_predictions["split"]) != {"validation"}:
        raise SignalError("tau freezing accepts validation rows only")
    tau = select_tau(
        validation_predictions,
        rule=config.tau_rule,
        decimals=config.tau_decimals,
    )
    return tau, _tau_report(
        validation_predictions,
        tau=tau,
        decimals=config.tau_decimals,
        config=config,
    )


def generate_signals(
    predictions: pd.DataFrame,
    *,
    tau: Decimal,
    config: Step1Config,
) -> pd.DataFrame:
    tau_text = f"{tau:.{config.tau_decimals}f}"
    rows: list[dict[str, str]] = []
    for row in predictions.to_dict(orient="records"):
        split = row["split"]
        if split not in {"validation", "test"}:
            raise SignalError(f"unexpected split for signal generation: {split!r}")
        predicted_return = Decimal(str(row["predicted_return"]))
        signal = classify_return(predicted_return, tau)
        rows.append(
            {
                "signal_timestamp_utc": str(row["target_timestamp_utc"]),
                "symbol": str(row["symbol"]),
                "split": str(split),
                "context_end_timestamp_utc": str(row["context_end_timestamp_utc"]),
                "current_close": str(row["current_close"]),
                "predicted_close_median": str(row["predicted_close_median"]),
                "predicted_return": str(row["predicted_return"]),
                "tau": tau_text,
                "signal": signal,
            }
        )
    frame = pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
    if frame.empty:
        raise ArtifactError("signal output is empty")
    if not set(frame["signal"]).issubset(set(ALLOWED_SIGNALS)):
        raise SignalError("signal output contains an unknown label")
    return frame


def signal_counts(signals: pd.DataFrame) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for split in ("validation", "test"):
        subset = signals.loc[signals["split"] == split]
        counts[split] = {
            label: int((subset["signal"] == label).sum())
            for label in ALLOWED_SIGNALS
        }
    return counts


def verify_signal_mapping(
    signals: pd.DataFrame,
    *,
    tau: Decimal,
    config: Step1Config,
) -> list[str]:
    failures: list[str] = []
    tau_text = f"{tau:.{config.tau_decimals}f}"
    for index, row in signals.iterrows():
        if row["tau"] != tau_text:
            failures.append(f"tau:{index}")
        expected = classify_return(Decimal(str(row["predicted_return"])), tau)
        if row["signal"] != expected:
            failures.append(f"mapping:{index}")
        if row["split"] not in {"validation", "test"}:
            failures.append(f"split:{index}")
    return failures


def boundary_cases(tau: Decimal) -> Sequence[tuple[str, Decimal, str]]:
    smallest = Decimal(1).scaleb(-12)
    return (
        ("tau", tau, "hold"),
        ("negative_tau", -tau, "hold"),
        ("above_tau", tau + smallest, "buy"),
        ("below_negative_tau", -tau - smallest, "sell"),
        ("zero", Decimal(0), "hold"),
    )
