"""Deterministic serialization and SHA256 helpers."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


class ArtifactError(RuntimeError):
    """Raised when an artifact violates a deterministic storage rule."""


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize JSON with fixed ordering, whitespace, UTF-8, and final LF."""
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: bytes, *, overwrite: bool) -> None:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_bytes(path: Path, payload: bytes, *, overwrite: bool = False) -> None:
    _atomic_write(path, payload, overwrite=overwrite)


def write_json(path: Path, payload: Any, *, overwrite: bool = False) -> None:
    write_bytes(path, canonical_json_bytes(payload), overwrite=overwrite)


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_csv_frame(
    frame: pd.DataFrame,
    path: Path,
    columns: Sequence[str],
    *,
    overwrite: bool = False,
) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ArtifactError(f"CSV frame is missing columns: {missing}")
    buffer = io.StringIO(newline="")
    frame.loc[:, list(columns)].to_csv(
        buffer,
        index=False,
        lineterminator="\n",
        na_rep="",
    )
    write_bytes(path, buffer.getvalue().encode("utf-8"), overwrite=overwrite)


def read_csv_exact(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        lineterminator="\n",
    )
    if list(frame.columns) != list(columns):
        raise ArtifactError(
            f"unexpected columns in {path}: {list(frame.columns)} != {list(columns)}"
        )
    return frame


def relative_posix(path: Path, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError as exc:
        raise ArtifactError(f"{path} is outside bundle root {base}") from exc


def hash_relative_file(path: Path, base: Path) -> dict[str, Any]:
    path = Path(path)
    return {
        "path": relative_posix(path, base),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def hash_relative_files(paths: Iterable[Path], base: Path) -> list[dict[str, Any]]:
    entries = [hash_relative_file(path, base) for path in paths]
    entries.sort(key=lambda item: item["path"])
    return entries


def verify_relative_hashes(
    base: Path,
    entries: Sequence[Mapping[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for entry in entries:
        path = Path(base) / str(entry["path"])
        if not path.is_file():
            failures.append(f"missing:{entry['path']}")
            continue
        actual_size = path.stat().st_size
        if actual_size != int(entry["size"]):
            failures.append(
                f"size:{entry['path']}:{actual_size}!={int(entry['size'])}"
            )
        actual_hash = sha256_file(path)
        if actual_hash != entry["sha256"]:
            failures.append(f"sha256:{entry['path']}:{actual_hash}!={entry['sha256']}")
    return failures


def combined_hash(files: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(files, key=lambda item: str(item["path"]))
    return sha256_bytes(canonical_json_bytes(ordered))
