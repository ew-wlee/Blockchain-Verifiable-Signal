"""Fixed-window Binance caching, cleaning, and data-quality evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import requests

from .config import Step1Config, format_utc
from .hashing import (
    canonical_json_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_bytes,
    write_csv_frame,
    write_json,
)

UTC = timezone.utc
CACHE_SCHEMA_VERSION = "step1-binance-cache-v1"
DATASET_SCHEMA_VERSION = "step1-dataset-v1"
QUALITY_SCHEMA_VERSION = "step1-data-quality-v1"
DATASET_COLUMNS = (
    "timestamp_utc",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_utc",
)
PRICE_FIELDS = ("open", "high", "low", "close")
DECIMAL_PLACES = 8


class DataError(RuntimeError):
    """Raised when source data or its cache cannot be trusted."""


class DataQualityError(DataError):
    """Raised when the fixed hourly dataset violates a quality invariant."""


@dataclass(frozen=True)
class CacheChunk:
    symbol: str
    start: datetime
    end: datetime
    payload_path: Path
    metadata_path: Path
    payload_sha256: str
    metadata_sha256: str
    rows: int
    endpoint: str
    request: Mapping[str, Any]

    @property
    def relative_payload_path(self) -> str:
        return self.payload_path.as_posix()


@dataclass(frozen=True)
class CacheStatus:
    cache_hit: bool
    network_requests: int
    chunks: tuple[CacheChunk, ...]
    endpoints_used: tuple[str, ...]

    @property
    def expected_rows(self) -> int:
        return sum(chunk.rows for chunk in self.chunks)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DataError("window timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def expected_chunks(
    start: datetime, end: datetime, symbols: Sequence[str]
) -> list[tuple[str, datetime, datetime]]:
    start = _as_utc(start)
    end = _as_utc(end)
    if start >= end:
        raise DataError("data window must be non-empty")
    chunks: list[tuple[str, datetime, datetime]] = []
    for symbol in symbols:
        cursor = start
        while cursor < end:
            chunk_end = min(end, _next_month(cursor))
            chunks.append((symbol, cursor, chunk_end))
            cursor = chunk_end
    return chunks


def _stem(start: datetime, end: datetime) -> str:
    return (
        start.strftime("%Y%m%dT%H%M%SZ")
        + "__"
        + end.strftime("%Y%m%dT%H%M%SZ")
    )


def _cache_paths(raw_root: Path, symbol: str, start: datetime, end: datetime) -> tuple[Path, Path]:
    stem = _stem(start, end)
    directory = raw_root / symbol
    return directory / f"{stem}.json", directory / f"{stem}.json.meta.json"


def _load_payload(payload: bytes, symbol: str) -> list[list[Any]]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(f"invalid JSON cache payload for {symbol}") from exc
    if not isinstance(decoded, list) or not decoded:
        raise DataError(f"cache payload for {symbol} must be a non-empty list")
    rows: list[list[Any]] = []
    for index, row in enumerate(decoded):
        if not isinstance(row, list) or len(row) != 12:
            raise DataError(
                f"cache payload for {symbol} row {index} must contain 12 fields"
            )
        if not isinstance(row[0], int) or not isinstance(row[6], int):
            raise DataError(
                f"cache payload for {symbol} row {index} has non-integer times"
            )
        rows.append(row)
    return rows


def _validate_metadata(
    metadata: Mapping[str, Any],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    required = {
        "schema_version",
        "source",
        "symbol",
        "start_utc",
        "end_utc",
        "endpoint",
        "request",
        "response_sha256",
        "response_bytes",
        "rows",
    }
    if set(metadata) != required:
        raise DataError(
            f"cache metadata keys mismatch for {symbol} {_stem(start, end)}"
        )
    if metadata["schema_version"] != CACHE_SCHEMA_VERSION:
        raise DataError("cache metadata schema mismatch")
    if metadata["source"] != "Binance Spot" or metadata["symbol"] != symbol:
        raise DataError("cache metadata source/symbol mismatch")
    if metadata["start_utc"] != format_utc(start) or metadata["end_utc"] != format_utc(end):
        raise DataError("cache metadata window mismatch")


def _fetch_chunk(
    session: requests.Session,
    endpoints: Sequence[str],
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    timeout: float,
) -> tuple[bytes, dict[str, Any], int]:
    params = {
        "symbol": symbol,
        "interval": "1h",
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000) - 1,
        "limit": 1000,
    }
    errors: list[str] = []
    for base_url in endpoints:
        url = base_url.rstrip("/") + "/api/v3/klines"
        try:
            response = session.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "blockchain-verifiable-signal/0.1"},
            )
        except requests.RequestException as exc:
            errors.append(f"{base_url}: {type(exc).__name__}: {exc}")
            continue
        if response.status_code != 200:
            errors.append(f"{base_url}: HTTP {response.status_code}")
            continue
        payload = response.content
        try:
            rows = _load_payload(payload, symbol)
        except DataError as exc:
            errors.append(f"{base_url}: {exc}")
            continue
        metadata = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source": "Binance Spot",
            "symbol": symbol,
            "start_utc": format_utc(start),
            "end_utc": format_utc(end),
            "endpoint": base_url.rstrip("/"),
            "request": params,
            "response_sha256": sha256_bytes(payload),
            "response_bytes": len(payload),
            "rows": len(rows),
        }
        return payload, metadata, 1
    raise DataError(
        "unable to fetch fixed Binance window for "
        f"{symbol} {format_utc(start)}..{format_utc(end)}: " + "; ".join(errors)
    )


def _cache_manifest_payload(
    config: Step1Config, chunks: Iterable[CacheChunk]
) -> dict[str, Any]:
    entries = []
    for chunk in sorted(
        chunks, key=lambda item: (item.symbol, item.start, item.end)
    ):
        entries.append(
            {
                "symbol": chunk.symbol,
                "start_utc": format_utc(chunk.start),
                "end_utc": format_utc(chunk.end),
                "payload_path": chunk.relative_payload_path,
                "payload_sha256": chunk.payload_sha256,
                "metadata_path": chunk.metadata_path.as_posix(),
                "metadata_sha256": chunk.metadata_sha256,
                "rows": chunk.rows,
                "endpoint": chunk.endpoint,
                "request": dict(chunk.request),
            }
        )
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "source": config.source,
        "interval": config.interval,
        "window_start_utc": format_utc(config.window_start),
        "window_end_utc": format_utc(config.window_end),
        "symbols": list(config.symbols),
        "chunks": entries,
    }


def prepare_raw_cache(
    config: Step1Config,
    *,
    offline: bool,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> CacheStatus:
    """Load or fetch the complete monthly cache without ever using a latest window."""
    raw_root = config.project_root / config.raw_cache_dir
    raw_root.mkdir(parents=True, exist_ok=True)
    expected = expected_chunks(config.window_start, config.window_end, config.symbols)
    chunks: list[CacheChunk] = []
    missing: list[tuple[str, datetime, datetime]] = []
    network_requests = 0
    owned_session = session is None
    active_session = session or requests.Session()

    try:
        for symbol, start, end in expected:
            payload_path, metadata_path = _cache_paths(raw_root, symbol, start, end)
            if payload_path.exists() != metadata_path.exists():
                raise DataError(
                    "incomplete cache pair; remove the partial file before retrying: "
                    f"{payload_path} / {metadata_path}"
                )
            if not payload_path.exists():
                missing.append((symbol, start, end))
                continue
            payload = payload_path.read_bytes()
            try:
                metadata = read_json(metadata_path)
            except (OSError, json.JSONDecodeError) as exc:
                raise DataError(f"unreadable cache metadata: {metadata_path}") from exc
            _validate_metadata(
                metadata, symbol=symbol, start=start, end=end
            )
            if metadata["response_sha256"] != sha256_bytes(payload):
                raise DataError(f"cache payload hash mismatch: {payload_path}")
            rows = _load_payload(payload, symbol)
            if len(rows) != metadata["rows"]:
                raise DataError(f"cache row count mismatch: {payload_path}")
            chunks.append(
                CacheChunk(
                    symbol=symbol,
                    start=start,
                    end=end,
                    payload_path=payload_path.relative_to(config.project_root),
                    metadata_path=metadata_path.relative_to(config.project_root),
                    payload_sha256=metadata["response_sha256"],
                    metadata_sha256=sha256_file(metadata_path),
                    rows=len(rows),
                    endpoint=str(metadata["endpoint"]),
                    request=dict(metadata["request"]),
                )
            )

        cache_hit = not missing
        if missing and offline:
            formatted = ", ".join(
                f"{symbol}:{format_utc(start)}..{format_utc(end)}"
                for symbol, start, end in missing
            )
            raise DataError(f"offline mode is missing fixed cache chunks: {formatted}")

        for symbol, start, end in missing:
            payload, metadata, requests_used = _fetch_chunk(
                active_session,
                config.endpoints,
                symbol,
                start,
                end,
                timeout=timeout,
            )
            network_requests += requests_used
            payload_path, metadata_path = _cache_paths(raw_root, symbol, start, end)
            write_bytes(payload_path, payload)
            write_json(metadata_path, metadata)
            chunks.append(
                CacheChunk(
                    symbol=symbol,
                    start=start,
                    end=end,
                    payload_path=payload_path.relative_to(config.project_root),
                    metadata_path=metadata_path.relative_to(config.project_root),
                    payload_sha256=metadata["response_sha256"],
                    metadata_sha256=sha256_file(metadata_path),
                    rows=int(metadata["rows"]),
                    endpoint=str(metadata["endpoint"]),
                    request=dict(metadata["request"]),
                )
            )
    finally:
        if owned_session:
            active_session.close()

    chunks.sort(key=lambda item: (item.symbol, item.start, item.end))
    manifest_path = raw_root / "cache_manifest.json"
    manifest_payload = _cache_manifest_payload(config, chunks)
    if manifest_path.exists():
        if manifest_path.read_bytes() != canonical_json_bytes(manifest_payload):
            raise DataError(
                "cache manifest does not match the fixed cache metadata: "
                f"{manifest_path}"
            )
    else:
        write_json(manifest_path, manifest_payload)

    endpoints_used = tuple(sorted({chunk.endpoint for chunk in chunks}))
    return CacheStatus(
        cache_hit=cache_hit,
        network_requests=network_requests,
        chunks=tuple(chunks),
        endpoints_used=endpoints_used,
    )


def read_cached_records(
    config: Step1Config, status: CacheStatus
) -> list[tuple[str, list[Any]]]:
    records: list[tuple[str, list[Any]]] = []
    for chunk in status.chunks:
        payload = (config.project_root / chunk.payload_path).read_bytes()
        if sha256_bytes(payload) != chunk.payload_sha256:
            raise DataError(f"cache changed after validation: {chunk.payload_path}")
        for row in _load_payload(payload, chunk.symbol):
            records.append((chunk.symbol, row))
    return records


def _decimal(value: Any, field: str, *, allow_zero: bool) -> Decimal:
    if isinstance(value, bool):
        raise DataQualityError(f"{field} must be numeric text")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataQualityError(f"{field} is not a decimal: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise DataQualityError(f"{field} must be a positive finite decimal")
    quantized = parsed.quantize(
        Decimal(1).scaleb(-DECIMAL_PLACES), rounding=ROUND_HALF_EVEN
    )
    if quantized != parsed:
        raise DataQualityError(
            f"{field} has more than {DECIMAL_PLACES} decimal places: {value!r}"
        )
    return quantized


def _decimal_text(value: Decimal) -> str:
    return f"{value:.{DECIMAL_PLACES}f}"


def _timestamp_from_ms(milliseconds: int) -> datetime:
    seconds, remainder = divmod(milliseconds, 1000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(milliseconds=remainder)


def _close_time_text(milliseconds: int) -> str:
    seconds, remainder = divmod(milliseconds, 1000)
    base = datetime.fromtimestamp(seconds, tz=UTC)
    return base.strftime("%Y-%m-%dT%H:%M:%S") + f".{remainder:03d}Z"


def _missing_ranges(missing: set[datetime]) -> list[dict[str, Any]]:
    if not missing:
        return []
    ordered = sorted(missing)
    ranges: list[dict[str, Any]] = []
    run_start = ordered[0]
    previous = ordered[0]
    for current in ordered[1:]:
        if current != previous + timedelta(hours=1):
            ranges.append(
                {
                    "start_utc": format_utc(run_start),
                    "end_utc_exclusive": format_utc(previous + timedelta(hours=1)),
                    "hours": int((previous - run_start).total_seconds() // 3600) + 1,
                }
            )
            run_start = current
        previous = current
    ranges.append(
        {
            "start_utc": format_utc(run_start),
            "end_utc_exclusive": format_utc(previous + timedelta(hours=1)),
            "hours": int((previous - run_start).total_seconds() // 3600) + 1,
        }
    )
    return ranges


def clean_records(
    records: Sequence[tuple[str, Sequence[Any]]],
    *,
    symbols: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Validate fixed-window rows; never interpolate or silently replace values."""
    window_start = _as_utc(window_start)
    window_end = _as_utc(window_end)
    expected_hours = int((window_end - window_start).total_seconds() // 3600)
    if expected_hours <= 0:
        raise DataQualityError("data window must contain at least one hour")
    expected_timestamps = {
        window_start + timedelta(hours=index) for index in range(expected_hours)
    }
    expected_count = expected_hours * len(symbols)
    allowed_symbols = set(symbols)
    per_symbol: dict[str, dict[str, Any]] = {
        symbol: {
            "raw_rows": 0,
            "exact_duplicates_removed": 0,
            "conflicting_duplicates": 0,
            "out_of_order_input": False,
            "sort_applied": False,
            "missing_count": 0,
            "missing_ranges": [],
            "unexpected_count": 0,
        }
        for symbol in symbols
    }
    rows_by_symbol: dict[str, dict[datetime, tuple[str, ...]]] = {
        symbol: {} for symbol in symbols
    }
    raw_order: dict[str, list[datetime]] = {symbol: [] for symbol in symbols}
    row_identity: dict[str, dict[datetime, tuple[str, ...]]] = {
        symbol: {} for symbol in symbols
    }

    for symbol, raw_row in records:
        if symbol not in allowed_symbols:
            raise DataQualityError(f"unexpected symbol in source data: {symbol!r}")
        if not isinstance(raw_row, Sequence) or len(raw_row) != 12:
            raise DataQualityError("each source row must contain exactly 12 fields")
        open_ms = raw_row[0]
        close_ms = raw_row[6]
        if not isinstance(open_ms, int) or not isinstance(close_ms, int):
            raise DataQualityError("open/close timestamps must be integer milliseconds")
        timestamp = _timestamp_from_ms(open_ms)
        if timestamp.minute or timestamp.second or timestamp.microsecond:
            raise DataQualityError(f"timestamp is not aligned to an hour: {open_ms}")
        if not window_start <= timestamp < window_end:
            per_symbol[symbol]["unexpected_count"] += 1
            raise DataQualityError(
                f"timestamp outside fixed window for {symbol}: {format_utc(timestamp)}"
            )
        if close_ms != open_ms + 3_599_999:
            raise DataQualityError(
                f"close timestamp is not the end of the hourly bar: {open_ms}"
            )

        prices = {
            field: _decimal(raw_row[index], f"{symbol}.{field}", allow_zero=False)
            for index, field in zip((1, 2, 3, 4), PRICE_FIELDS)
        }
        volume = _decimal(raw_row[5], f"{symbol}.volume", allow_zero=True)
        if prices["high"] < max(prices["open"], prices["close"], prices["low"]):
            raise DataQualityError(f"invalid high price for {symbol} at {format_utc(timestamp)}")
        if prices["low"] > min(prices["open"], prices["close"], prices["high"]):
            raise DataQualityError(f"invalid low price for {symbol} at {format_utc(timestamp)}")

        canonical = (
            _decimal_text(prices["open"]),
            _decimal_text(prices["high"]),
            _decimal_text(prices["low"]),
            _decimal_text(prices["close"]),
            _decimal_text(volume),
            _close_time_text(close_ms),
        )
        per_symbol[symbol]["raw_rows"] += 1
        raw_order[symbol].append(timestamp)
        existing = row_identity[symbol].get(timestamp)
        if existing is not None:
            if existing == canonical:
                per_symbol[symbol]["exact_duplicates_removed"] += 1
            else:
                per_symbol[symbol]["conflicting_duplicates"] += 1
                raise DataQualityError(
                    f"conflicting duplicate rows for {symbol} at {format_utc(timestamp)}"
                )
            continue
        row_identity[symbol][timestamp] = canonical
        rows_by_symbol[symbol][timestamp] = (
            format_utc(timestamp),
            symbol,
            *canonical,
        )

    for symbol in symbols:
        times = raw_order[symbol]
        if times != sorted(times):
            per_symbol[symbol]["out_of_order_input"] = True
            per_symbol[symbol]["sort_applied"] = True
        seen = set(rows_by_symbol[symbol])
        missing = expected_timestamps - seen
        per_symbol[symbol]["missing_count"] = len(missing)
        per_symbol[symbol]["missing_ranges"] = _missing_ranges(missing)
        if missing:
            raise DataQualityError(
                f"{symbol} is missing {len(missing)} fixed hourly rows; "
                f"first ranges: {per_symbol[symbol]['missing_ranges'][:3]}"
            )
        per_symbol[symbol]["cleaned_rows"] = len(seen)
        per_symbol[symbol]["expected_rows"] = expected_hours
        per_symbol[symbol]["first_timestamp_utc"] = format_utc(window_start)
        per_symbol[symbol]["last_timestamp_utc"] = format_utc(
            window_end - timedelta(hours=1)
        )

    all_rows = [
        rows_by_symbol[symbol][timestamp]
        for timestamp in sorted(expected_timestamps)
        for symbol in symbols
    ]
    frame = pd.DataFrame(all_rows, columns=DATASET_COLUMNS)
    if len(frame) != expected_count:
        raise DataQualityError(
            f"cleaned row count mismatch: {len(frame)} != {expected_count}"
        )

    checks = [
        {"name": "window_start_exact", "passed": True},
        {"name": "window_end_exact", "passed": True},
        {"name": "hourly_continuity", "passed": True},
        {"name": "no_conflicting_duplicates", "passed": True},
        {"name": "no_missing_hours", "passed": True},
        {"name": "valid_ohlc", "passed": True},
        {"name": "exact_period_rows", "passed": True},
    ]
    quality = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "source": "Binance Spot",
        "window": {
            "start_utc": format_utc(window_start),
            "end_utc_exclusive": format_utc(window_end),
            "interval": "1h",
            "expected_hours_per_symbol": expected_hours,
        },
        "symbols": per_symbol,
        "totals": {
            "raw_rows": sum(item["raw_rows"] for item in per_symbol.values()),
            "cleaned_rows": len(frame),
            "expected_rows": expected_count,
            "exact_duplicates_removed": sum(
                item["exact_duplicates_removed"] for item in per_symbol.values()
            ),
            "conflicting_duplicates": 0,
            "missing_rows": 0,
            "unexpected_rows": 0,
        },
        "rules": {
            "interpolation": "forbidden",
            "exact_duplicate": "deduplicated and counted",
            "conflicting_duplicate": "blocked",
            "missing_hour": "blocked",
            "out_of_order": "sorted deterministically and recorded",
            "numeric_format": f"fixed {DECIMAL_PLACES} decimal places",
        },
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }
    return frame, quality


def add_cache_evidence(quality: dict[str, Any], status: CacheStatus) -> dict[str, Any]:
    enriched = dict(quality)
    enriched["cache"] = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "chunk_count": len(status.chunks),
        "rows_across_cache": status.expected_rows,
        "endpoints": list(status.endpoints_used),
        "chunks": [
            {
                "symbol": chunk.symbol,
                "start_utc": format_utc(chunk.start),
                "end_utc_exclusive": format_utc(chunk.end),
                "payload_path": chunk.relative_payload_path,
                "payload_sha256": chunk.payload_sha256,
                "metadata_path": chunk.metadata_path.as_posix(),
                "metadata_sha256": chunk.metadata_sha256,
                "rows": chunk.rows,
                "endpoint": chunk.endpoint,
            }
            for chunk in status.chunks
        ],
    }
    return enriched


def write_dataset_and_quality(
    config: Step1Config,
    output_root: Path,
    status: CacheStatus,
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = read_cached_records(config, status)
    frame, quality = clean_records(
        records,
        symbols=config.symbols,
        window_start=config.window_start,
        window_end=config.window_end,
    )
    quality = add_cache_evidence(quality, status)
    dataset_path = output_root / "dataset.csv"
    quality_path = output_root / "reports" / "data_quality.json"
    write_csv_frame(frame, dataset_path, DATASET_COLUMNS)
    write_json(quality_path, quality)
    return quality, {
        "rows": len(frame),
        "symbols": list(frame["symbol"].drop_duplicates()),
        "dataset_path": dataset_path,
        "quality_path": quality_path,
    }
