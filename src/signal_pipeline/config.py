"""Strict loading and validation of the frozen Step 1 configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when the input configuration is incomplete or inconsistent."""


UTC = timezone.utc
INPUT_SCHEMA_VERSION = "step1-input-v1"
FROZEN_SCHEMA_VERSION = "step1-frozen-v1"
EXPECTED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
TAU_RULE = "median_abs_validation_predicted_return"


@dataclass(frozen=True)
class TimeSplit:
    start: datetime
    end: datetime

    def contains(self, timestamp: datetime) -> bool:
        return self.start <= timestamp < self.end

    @property
    def hours(self) -> int:
        return _whole_hours(self.end - self.start, "split")


@dataclass(frozen=True)
class Step1Config:
    config_path: Path
    project_root: Path
    source: str
    endpoints: tuple[str, ...]
    symbols: tuple[str, ...]
    interval: str
    window_start: datetime
    window_end: datetime
    validation: TimeSplit
    test: TimeSplit
    raw_cache_dir: Path
    model_id: str
    requested_revision: str
    snapshot_root: Path
    device: str
    dtype: str
    context_length: int
    prediction_length: int
    batch_size: int
    num_samples: int
    temperature: float
    top_k: int
    top_p: float
    seed: int
    tau_rule: str
    tau_decimals: int

    @property
    def expected_hours(self) -> int:
        return _whole_hours(self.window_end - self.window_start, "window")

    @property
    def expected_rows(self) -> int:
        return self.expected_hours * len(self.symbols)

    def split_for(self, timestamp: datetime) -> str | None:
        timestamp = _as_utc(timestamp)
        if self.validation.contains(timestamp):
            return "validation"
        if self.test.contains(timestamp):
            return "test"
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ConfigError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _whole_hours(delta: timedelta, label: str) -> int:
    seconds = delta.total_seconds()
    if seconds <= 0 or seconds % 3600 != 0:
        raise ConfigError(f"{label} must contain a positive whole number of hours")
    return int(seconds // 3600)


def parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be an ISO-8601 UTC string")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ConfigError(
            f"{field} must exactly match YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    parsed = parsed.replace(tzinfo=UTC)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ConfigError(f"{field} must be aligned to an exact UTC hour")
    return parsed


def format_utc(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a mapping")
    return value


def _expect_keys(mapping: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(mapping)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise ConfigError(
            f"{field} keys mismatch: missing={missing}, unknown={unknown}"
        )


def _relative_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigError(f"{field} must stay within the project root")
    return Path(*path.parts)


def load_config(
    path: Path,
    *,
    project_root: Path | None = None,
) -> Step1Config:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc

    root = _mapping(raw, "root")
    _expect_keys(root, {"schema_version", "data", "model", "signals"}, "root")
    if root["schema_version"] != INPUT_SCHEMA_VERSION:
        raise ConfigError(
            f"schema_version must be {INPUT_SCHEMA_VERSION!r}, "
            f"got {root['schema_version']!r}"
        )

    data = _mapping(root["data"], "data")
    _expect_keys(
        data,
        {
            "source",
            "endpoints",
            "symbols",
            "interval",
            "timezone",
            "window_start_utc",
            "window_end_utc",
            "validation",
            "test",
            "raw_cache_dir",
        },
        "data",
    )
    model = _mapping(root["model"], "model")
    _expect_keys(
        model,
        {
            "id",
            "revision",
            "snapshot_root",
            "device",
            "dtype",
            "context_length",
            "prediction_length",
            "batch_size",
            "num_samples",
            "temperature",
            "top_k",
            "top_p",
            "seed",
        },
        "model",
    )
    signals = _mapping(root["signals"], "signals")
    _expect_keys(signals, {"tau_rule", "tau_decimals"}, "signals")

    if data["source"] != "Binance Spot":
        raise ConfigError("data.source must be 'Binance Spot'")
    if data["interval"] != "1h" or data.get("timezone") != "UTC":
        raise ConfigError("only fixed 1h UTC data is supported")

    endpoints_raw = data["endpoints"]
    if not isinstance(endpoints_raw, list) or not endpoints_raw:
        raise ConfigError("data.endpoints must be a non-empty list")
    endpoints = tuple(endpoints_raw)
    if len(set(endpoints)) != len(endpoints):
        raise ConfigError("data.endpoints must be unique")
    for endpoint in endpoints:
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ConfigError("each data endpoint must be an HTTPS URL")

    symbols_raw = data["symbols"]
    if not isinstance(symbols_raw, list) or tuple(symbols_raw) != EXPECTED_SYMBOLS:
        raise ConfigError(f"data.symbols must be exactly {list(EXPECTED_SYMBOLS)}")
    symbols = tuple(symbols_raw)

    window_start = parse_utc(data["window_start_utc"], "data.window_start_utc")
    window_end = parse_utc(data["window_end_utc"], "data.window_end_utc")

    def split_from(value: Any, field: str) -> TimeSplit:
        split = _mapping(value, field)
        _expect_keys(split, {"start_utc", "end_utc"}, field)
        return TimeSplit(
            start=parse_utc(split["start_utc"], f"{field}.start_utc"),
            end=parse_utc(split["end_utc"], f"{field}.end_utc"),
        )

    validation = split_from(data["validation"], "data.validation")
    test = split_from(data["test"], "data.test")

    _whole_hours(window_end - window_start, "window")
    model_id = model["id"]
    if model_id != "amazon/chronos-t5-small":
        raise ConfigError("model.id must be 'amazon/chronos-t5-small'")
    if model["revision"] is not None:
        raise ConfigError(
            "model.revision must be null in the input config; "
            "the immutable revision is resolved and recorded at runtime"
        )
    if model["device"] != "cpu" or model["dtype"] != "float32":
        raise ConfigError("formal inference must use CPU float32")
    if model["context_length"] != 64 or model["prediction_length"] != 1:
        raise ConfigError("context_length=64 and prediction_length=1 are fixed")
    if not isinstance(model["batch_size"], int) or model["batch_size"] <= 0:
        raise ConfigError("model.batch_size must be a positive integer")
    if not isinstance(model["num_samples"], int) or not 1 <= model["num_samples"] <= 100:
        raise ConfigError("model.num_samples must be between 1 and 100")
    if not isinstance(model["seed"], int) or model["seed"] < 0:
        raise ConfigError("model.seed must be a non-negative integer")
    if not isinstance(model["top_k"], int) or model["top_k"] < 0:
        raise ConfigError("model.top_k must be a non-negative integer")
    temperature = float(model["temperature"])
    top_p = float(model["top_p"])
    if temperature <= 0 or not 0 < top_p <= 1:
        raise ConfigError("temperature must be > 0 and top_p must be in (0, 1]")

    if signals["tau_rule"] != TAU_RULE:
        raise ConfigError(f"signals.tau_rule must be {TAU_RULE!r}")
    tau_decimals = signals["tau_decimals"]
    if not isinstance(tau_decimals, int) or not 0 <= tau_decimals <= 18:
        raise ConfigError("signals.tau_decimals must be between 0 and 18")

    context_delta = timedelta(hours=int(model["context_length"]))
    if validation.start < window_start + context_delta:
        raise ConfigError(
            "validation must start after at least one full context window"
        )
    if not window_start < validation.start < validation.end:
        raise ConfigError("validation must be a non-empty chronological interval")
    if validation.end != test.start:
        raise ConfigError("validation.end_utc must equal test.start_utc")
    if test.end != window_end:
        raise ConfigError("test.end_utc must equal data.window_end_utc")
    if test.start >= test.end:
        raise ConfigError("test must be a non-empty chronological interval")

    raw_cache_dir = _relative_path(data["raw_cache_dir"], "data.raw_cache_dir")
    snapshot_root = _relative_path(model["snapshot_root"], "model.snapshot_root")
    root_path = (
        Path(project_root).resolve()
        if project_root is not None
        else config_path.parent.parent
    )

    return Step1Config(
        config_path=config_path,
        project_root=root_path,
        source=str(data["source"]),
        endpoints=endpoints,
        symbols=symbols,
        interval=str(data["interval"]),
        window_start=window_start,
        window_end=window_end,
        validation=validation,
        test=test,
        raw_cache_dir=raw_cache_dir,
        model_id=str(model_id),
        requested_revision="main",
        snapshot_root=snapshot_root,
        device=str(model["device"]),
        dtype=str(model["dtype"]),
        context_length=int(model["context_length"]),
        prediction_length=int(model["prediction_length"]),
        batch_size=int(model["batch_size"]),
        num_samples=int(model["num_samples"]),
        temperature=temperature,
        top_k=int(model["top_k"]),
        top_p=top_p,
        seed=int(model["seed"]),
        tau_rule=str(signals["tau_rule"]),
        tau_decimals=int(tau_decimals),
    )


def frozen_config_payload(
    config: Step1Config,
    *,
    code_commit: str,
    resolved_revision: str,
    snapshot_relative_path: str,
    tau: str,
) -> dict[str, Any]:
    """Build the reviewable config that freezes the actual run."""
    return {
        "schema_version": FROZEN_SCHEMA_VERSION,
        "code_commit": code_commit,
        "data": {
            "source": config.source,
            "endpoints": list(config.endpoints),
            "symbols": list(config.symbols),
            "interval": config.interval,
            "timezone": "UTC",
            "window_start_utc": format_utc(config.window_start),
            "window_end_utc": format_utc(config.window_end),
            "expected_hours_per_symbol": config.expected_hours,
            "expected_rows": config.expected_rows,
            "validation": {
                "start_utc": format_utc(config.validation.start),
                "end_utc": format_utc(config.validation.end),
                "hours_per_symbol": config.validation.hours,
            },
            "test": {
                "start_utc": format_utc(config.test.start),
                "end_utc": format_utc(config.test.end),
                "hours_per_symbol": config.test.hours,
            },
        },
        "model": {
            "id": config.model_id,
            "requested_revision": config.requested_revision,
            "resolved_revision": resolved_revision,
            "snapshot_path": snapshot_relative_path,
            "device": config.device,
            "dtype": config.dtype,
            "context_length": config.context_length,
            "prediction_length": config.prediction_length,
            "batch_size": config.batch_size,
            "num_samples": config.num_samples,
            "temperature": config.temperature,
            "top_k": config.top_k,
            "top_p": config.top_p,
            "seed": config.seed,
            "prediction_statistic": "median_of_samples_for_next_close",
        },
        "signals": {
            "tau_rule": config.tau_rule,
            "tau_decimals": config.tau_decimals,
            "tau": tau,
            "tau_input_split": "validation",
            "mapping": {
                "buy": "predicted_return > tau",
                "sell": "predicted_return < -tau",
                "hold": "otherwise, including equality at either boundary",
            },
            "timestamp_semantics": (
                "signal timestamp is the next hourly close targeted by the "
                "64-hour context"
            ),
        },
    }
