from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import ExplodingSession, FakeSession, hourly_rows, tiny_config
from signal_pipeline.data import (
    DataError,
    DataQualityError,
    clean_records,
    prepare_raw_cache,
)

UTC = timezone.utc
START = datetime(2024, 1, 1, tzinfo=UTC)


def _clean(rows, *, symbols=("BTCUSDT",), hours=3):
    records = [(symbol, row) for symbol in symbols for row in rows]
    return clean_records(
        records,
        symbols=symbols,
        window_start=START,
        window_end=START + timedelta(hours=hours),
    )


def test_clean_records_accepts_exact_fixture(fixture_rows):
    frame, quality = _clean(fixture_rows)
    assert len(frame) == 3
    assert list(frame["timestamp_utc"]) == [
        "2024-01-01T00:00:00Z",
        "2024-01-01T01:00:00Z",
        "2024-01-01T02:00:00Z",
    ]
    assert frame["close"].iloc[-1] == "107.00000000"
    assert quality["passed"] is True
    assert quality["totals"]["missing_rows"] == 0


def test_clean_records_deduplicates_identical_rows(fixture_rows):
    frame, quality = _clean(fixture_rows + [fixture_rows[0]])
    assert len(frame) == 3
    assert quality["symbols"]["BTCUSDT"]["exact_duplicates_removed"] == 1
    assert quality["totals"]["raw_rows"] == 4


def test_clean_records_rejects_conflicting_duplicate(fixture_rows):
    conflicting = list(fixture_rows[0])
    conflicting[4] = "103.00000000"
    with pytest.raises(DataQualityError, match="conflicting duplicate"):
        _clean(fixture_rows + [conflicting])


def test_clean_records_rejects_missing_hour(fixture_rows):
    with pytest.raises(DataQualityError, match="missing 1 fixed hourly rows"):
        _clean([fixture_rows[0], fixture_rows[2]])


def test_clean_records_rejects_invalid_ohlc(fixture_rows):
    broken = list(fixture_rows[0])
    broken[2] = "101.00000000"
    with pytest.raises(DataQualityError, match="invalid high price"):
        _clean([broken, fixture_rows[1], fixture_rows[2]])


def test_clean_records_rejects_out_of_window_row(fixture_rows):
    late = hourly_rows(START + timedelta(hours=3), 1)[0]
    with pytest.raises(DataQualityError, match="outside fixed window"):
        _clean(fixture_rows + [late])


def test_clean_records_sorts_and_records_out_of_order(fixture_rows):
    frame, quality = _clean(
        [fixture_rows[2], fixture_rows[0], fixture_rows[1]]
    )
    assert quality["symbols"]["BTCUSDT"]["out_of_order_input"] is True
    assert list(frame["timestamp_utc"]) == sorted(frame["timestamp_utc"])


def test_cache_is_reused_without_network(tmp_path, base_config, fixture_rows):
    config = tiny_config(
        tmp_path,
        base_config,
        start=START,
        end=START + timedelta(hours=3),
    )
    first_session = FakeSession([fixture_rows])
    first = prepare_raw_cache(config, offline=False, session=first_session)
    assert first.cache_hit is False
    assert first.network_requests == 1
    assert first_session.calls == 1

    second_session = ExplodingSession()
    second = prepare_raw_cache(config, offline=False, session=second_session)
    assert second.cache_hit is True
    assert second.network_requests == 0
    assert second_session.calls == 0
    assert second.chunks[0].payload_sha256 == first.chunks[0].payload_sha256


def test_offline_missing_cache_fails_without_request(tmp_path, base_config):
    config = tiny_config(
        tmp_path,
        base_config,
        start=START,
        end=START + timedelta(hours=3),
    )
    with pytest.raises(DataError, match="offline mode is missing"):
        prepare_raw_cache(config, offline=True, session=ExplodingSession())
