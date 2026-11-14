"""Immutable model revision resolution and checkpoint commitment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, snapshot_download

from .config import Step1Config
from .hashing import (
    canonical_json_bytes,
    combined_hash,
    read_json,
    sha256_file,
    write_json,
)
from .runtime import dependency_versions, utc_now_iso

PIN_SCHEMA_VERSION = "step1-model-pin-v1"
REFERENCE_SCHEMA_VERSION = "step1-model-reference-v1"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_FILES = ("config.json", "model.safetensors")


class ModelError(RuntimeError):
    """Raised when model revision or checkpoint bytes cannot be pinned."""


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSnapshot:
    model_id: str
    requested_revision: str
    resolved_revision: str
    root: Path
    relative_path: str
    files: tuple[SnapshotFile, ...]
    commitment_sha256: str

    def reference_payload(self, config: Step1Config) -> dict[str, Any]:
        return {
            "schema_version": REFERENCE_SCHEMA_VERSION,
            "model_id": self.model_id,
            "requested_revision": self.requested_revision,
            "resolved_revision": self.resolved_revision,
            "snapshot_path": self.relative_path,
            "file_count": len(self.files),
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in self.files
            ],
            "checkpoint_commitment_sha256": self.commitment_sha256,
            "inference": {
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
                "statistic": "median",
            },
            "verification": {
                "revision_format": "full 40-character hexadecimal commit",
                "loaded_from_local_snapshot_only": True,
                "hashes_verified_before_load": True,
                "hashes_reverified_after_load": True,
                "required_files_present": list(_REQUIRED_FILES),
            },
            "library_versions": dependency_versions(),
        }


def _snapshot_files(root: Path) -> tuple[SnapshotFile, ...]:
    entries: list[SnapshotFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if ".cache" in relative.parts:
            continue
        entries.append(
            SnapshotFile(
                path=relative.as_posix(),
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    if not entries:
        raise ModelError(f"model snapshot contains no files: {root}")
    return tuple(entries)


def _snapshot_from_root(
    config: Step1Config, revision: str, root: Path
) -> ModelSnapshot:
    missing = [name for name in _REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise ModelError(f"pinned snapshot is missing required files {missing}: {root}")
    files = _snapshot_files(root)
    commitment = combined_hash(
        [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in files
        ]
    )
    relative_path = root.relative_to(config.project_root).as_posix()
    return ModelSnapshot(
        model_id=config.model_id,
        requested_revision=config.requested_revision,
        resolved_revision=revision,
        root=root,
        relative_path=relative_path,
        files=files,
        commitment_sha256=commitment,
    )


def _read_pin(path: Path, config: Step1Config) -> str | None:
    if not path.exists():
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError) as exc:
        raise ModelError(f"unreadable model pin: {path}") from exc
    if payload.get("schema_version") != PIN_SCHEMA_VERSION:
        raise ModelError("model pin schema mismatch")
    if payload.get("model_id") != config.model_id:
        raise ModelError("model pin belongs to a different model")
    if payload.get("requested_revision") != config.requested_revision:
        raise ModelError("model pin requested revision mismatch")
    revision = payload.get("resolved_revision")
    if not isinstance(revision, str) or not _COMMIT_RE.fullmatch(revision):
        raise ModelError("model pin does not contain a full immutable commit")
    return revision


def resolve_snapshot(config: Step1Config, *, offline: bool) -> ModelSnapshot:
    """Resolve main once, then reuse the pinned commit and local snapshot forever."""
    snapshot_root = config.project_root / config.snapshot_root
    snapshot_root.mkdir(parents=True, exist_ok=True)
    pin_path = snapshot_root / "pinned_revision.json"
    revision = _read_pin(pin_path, config)

    if revision is None:
        if offline:
            raise ModelError(
                "offline mode requires an existing pinned model revision: "
                f"{pin_path}"
            )
        info = HfApi().model_info(config.model_id, revision=config.requested_revision)
        resolved = info.sha
        if not isinstance(resolved, str) or not _COMMIT_RE.fullmatch(resolved):
            raise ModelError(f"model registry returned a non-immutable revision: {resolved!r}")
        pin_payload = {
            "schema_version": PIN_SCHEMA_VERSION,
            "model_id": config.model_id,
            "requested_revision": config.requested_revision,
            "resolved_revision": resolved,
            "resolved_at_utc": utc_now_iso(),
        }
        write_json(pin_path, pin_payload)
        revision = resolved

    model_slug = config.model_id.rsplit("/", 1)[-1]
    model_root = snapshot_root / f"{model_slug}-{revision}"
    if not all((model_root / name).is_file() for name in _REQUIRED_FILES):
        if offline:
            raise ModelError(f"offline mode is missing the pinned model snapshot: {model_root}")
        snapshot_download(
            repo_id=config.model_id,
            revision=revision,
            local_dir=model_root,
        )

    return _snapshot_from_root(config, revision, model_root)


def verify_snapshot(snapshot: ModelSnapshot) -> dict[str, Any]:
    """Recompute every captured checkpoint hash and reject any drift."""
    current_files = _snapshot_files(snapshot.root)
    expected = [
        {"path": item.path, "size": item.size, "sha256": item.sha256}
        for item in snapshot.files
    ]
    actual = [
        {"path": item.path, "size": item.size, "sha256": item.sha256}
        for item in current_files
    ]
    if actual != expected:
        raise ModelError("model snapshot bytes changed after resolution")
    commitment = combined_hash(actual)
    if commitment != snapshot.commitment_sha256:
        raise ModelError("model checkpoint commitment changed")
    return {
        "file_count": len(actual),
        "checkpoint_commitment_sha256": commitment,
        "files_match": True,
    }


def model_reference_path(output_root: Path) -> Path:
    return output_root / "model_reference.json"


def write_model_reference(
    output_root: Path, snapshot: ModelSnapshot, config: Step1Config
) -> Path:
    path = model_reference_path(output_root)
    write_json(path, snapshot.reference_payload(config))
    return path
