"""Fixed 64-hour sliding-window inference with a pinned Chronos checkpoint."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from .config import Step1Config, format_utc
from .data import DATASET_COLUMNS
from .hashing import read_csv_exact
from .model import ModelSnapshot

UTC = timezone.utc
PREDICTION_COLUMNS = (
    "target_timestamp_utc",
    "symbol",
    "split",
    "context_start_timestamp_utc",
    "context_end_timestamp_utc",
    "current_close",
    "predicted_close_median",
    "predicted_return",
    "num_samples",
    "seed",
)
_RETURN_PLACES = 12
_CLOSE_PLACES = 8


class InferenceError(RuntimeError):
    """Raised when model inference or its input alignment is invalid."""


@dataclass(frozen=True)
class PredictionWindow:
    symbol: str
    target_timestamp: datetime
    split: str
    context_start: datetime
    context_end: datetime
    current_close: str
    context: tuple[float, ...]


@dataclass(frozen=True)
class InferenceResult:
    predictions: pd.DataFrame
    batch_count: int
    model_root: str
    pipeline_class: str


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def build_prediction_windows(
    dataset: pd.DataFrame, config: Step1Config
) -> list[PredictionWindow]:
    if list(dataset.columns) != list(DATASET_COLUMNS):
        raise InferenceError("dataset columns do not match the frozen schema")
    windows: list[PredictionWindow] = []
    for symbol_index, symbol in enumerate(config.symbols):
        symbol_frame = dataset.loc[dataset["symbol"] == symbol].reset_index(drop=True)
        if len(symbol_frame) != config.expected_hours:
            raise InferenceError(
                f"{symbol} has {len(symbol_frame)} rows, expected {config.expected_hours}"
            )
        timestamps = [_parse_timestamp(value) for value in symbol_frame["timestamp_utc"]]
        if timestamps != sorted(timestamps):
            raise InferenceError(f"{symbol} timestamps are not sorted")
        for index in range(config.context_length, len(symbol_frame)):
            target = timestamps[index]
            split = config.split_for(target)
            if split is None:
                continue
            context_rows = symbol_frame.iloc[
                index - config.context_length : index
            ]
            context_end = timestamps[index - 1]
            if context_end != target - timedelta(hours=1):
                raise InferenceError(
                    f"context does not end immediately before target for {symbol} "
                    f"at {format_utc(target)}"
                )
            context_start = timestamps[index - config.context_length]
            context = tuple(float(value) for value in context_rows["close"])
            if len(context) != config.context_length:
                raise InferenceError("context length mismatch")
            windows.append(
                PredictionWindow(
                    symbol=symbol,
                    target_timestamp=target,
                    split=split,
                    context_start=context_start,
                    context_end=context_end,
                    current_close=str(context_rows.iloc[-1]["close"]),
                    context=context,
                )
            )
        del symbol_index
    windows.sort(
        key=lambda item: (
            item.target_timestamp,
            config.symbols.index(item.symbol),
        )
    )
    return windows


def _quantize(value: Decimal, places: int) -> Decimal:
    return value.quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN
    )


def run_inference(
    windows: Sequence[PredictionWindow],
    config: Step1Config,
    snapshot: ModelSnapshot,
) -> InferenceResult:
    if not windows:
        raise InferenceError("no validation/test prediction windows were built")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

    import torch
    from chronos import ChronosPipeline

    if config.device != "cpu" or config.dtype != "float32":
        raise InferenceError("only the frozen CPU float32 configuration is allowed")
    torch.set_num_threads(1)
    torch.manual_seed(config.seed)
    dtype = torch.float32
    pipeline = ChronosPipeline.from_pretrained(
        str(snapshot.root),
        device_map=config.device,
        dtype=dtype,
    )
    pipeline.model.eval()

    loaded_name = getattr(pipeline.model, "name_or_path", None)
    if loaded_name:
        loaded_path = Path(str(loaded_name)).resolve()
        if loaded_path != snapshot.root.resolve():
            raise InferenceError(
                f"pipeline loaded from {loaded_path}, expected {snapshot.root.resolve()}"
            )

    output_rows: list[dict[str, str | int]] = []
    batch_count = 0
    with torch.inference_mode():
        for start in range(0, len(windows), config.batch_size):
            batch = windows[start : start + config.batch_size]
            contexts = [
                torch.tensor(item.context, dtype=torch.float32) for item in batch
            ]
            samples = pipeline.predict(
                contexts,
                prediction_length=config.prediction_length,
                num_samples=config.num_samples,
                temperature=config.temperature,
                top_k=config.top_k,
                top_p=config.top_p,
            )
            if tuple(samples.shape) != (
                len(batch),
                config.num_samples,
                config.prediction_length,
            ):
                raise InferenceError(
                    f"unexpected model output shape: {tuple(samples.shape)}"
                )
            medians = torch.median(samples[:, :, 0], dim=1).values.cpu().tolist()
            if len(medians) != len(batch):
                raise InferenceError("median output count does not match batch size")
            for item, median in zip(batch, medians):
                predicted_close = _quantize(
                    Decimal(str(float(median))), _CLOSE_PLACES
                )
                current_close = Decimal(item.current_close)
                if current_close <= 0 or predicted_close <= 0:
                    raise InferenceError("model produced a non-positive price")
                predicted_return = _quantize(
                    predicted_close / current_close - Decimal(1),
                    _RETURN_PLACES,
                )
                output_rows.append(
                    {
                        "target_timestamp_utc": format_utc(item.target_timestamp),
                        "symbol": item.symbol,
                        "split": item.split,
                        "context_start_timestamp_utc": format_utc(item.context_start),
                        "context_end_timestamp_utc": format_utc(item.context_end),
                        "current_close": item.current_close,
                        "predicted_close_median": f"{predicted_close:.8f}",
                        "predicted_return": f"{predicted_return:.12f}",
                        "num_samples": config.num_samples,
                        "seed": config.seed,
                    }
                )
            batch_count += 1

    frame = pd.DataFrame(output_rows, columns=PREDICTION_COLUMNS)
    if len(frame) != len(windows):
        raise InferenceError("prediction row count mismatch")
    return InferenceResult(
        predictions=frame,
        batch_count=batch_count,
        model_root=snapshot.relative_path,
        pipeline_class=type(pipeline).__name__,
    )


def load_dataset(path: Path) -> pd.DataFrame:
    return read_csv_exact(path, DATASET_COLUMNS)
