from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from signal_pipeline.config import (
    Step1Config,
    TimeSplit,
    load_config,
)

UTC = timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "btc_3h.json"


@pytest.fixture(scope="session")
def base_config() -> Step1Config:
    return load_config(PROJECT_ROOT / "configs" / "step1.yaml")


@pytest.fixture(scope="session")
def fixture_rows() -> list[list[Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def raw_kline(
    timestamp: datetime,
    *,
    open_price: str = "100.00000000",
    high: str = "105.00000000",
    low: str = "99.00000000",
    close: str = "104.00000000",
    volume: str = "10.00000000",
) -> list[Any]:
    open_ms = int(timestamp.timestamp() * 1000)
    return [
        open_ms,
        open_price,
        high,
        low,
        close,
        volume,
        open_ms + 3_599_999,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


def hourly_rows(
    start: datetime,
    hours: int,
    *,
    base_price: float = 100.0,
) -> list[list[Any]]:
    rows = []
    for hour in range(hours):
        price = base_price + hour
        rows.append(
            raw_kline(
                start + timedelta(hours=hour),
                open_price=f"{price:.8f}",
                high=f"{price + 2:.8f}",
                low=f"{price - 1:.8f}",
                close=f"{price + 1:.8f}",
            )
        )
    return rows


def tiny_config(
    tmp_path: Path,
    base: Step1Config,
    *,
    start: datetime,
    end: datetime,
    symbols: tuple[str, ...] = ("BTCUSDT",),
) -> Step1Config:
    validation_start = start + timedelta(hours=64)
    validation_end = min(end, validation_start + timedelta(hours=1))
    test_start = validation_end
    test_end = end
    return replace(
        base,
        project_root=tmp_path,
        symbols=symbols,
        window_start=start,
        window_end=end,
        validation=TimeSplit(validation_start, validation_end),
        test=TimeSplit(test_start, test_end),
        raw_cache_dir=Path("data/raw"),
    )


class FakeResponse:
    def __init__(self, payload: bytes, status_code: int = 200) -> None:
        self.content = payload
        self.status_code = status_code


class FakeSession:
    def __init__(self, payloads: list[list[list[Any]]]) -> None:
        self.payloads = payloads
        self.calls = 0

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        payload = self.payloads[self.calls]
        self.calls += 1
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    def close(self) -> None:
        return None


class ExplodingSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.calls += 1
        raise AssertionError("network must not be used when cache is complete")

    def close(self) -> None:
        return None
