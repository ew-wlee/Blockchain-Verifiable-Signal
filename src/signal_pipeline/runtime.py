"""Runtime provenance, dependency versions, and strict offline guard."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Iterator


class RuntimeProvenanceError(RuntimeError):
    """Raised when a reproducible code or environment invariant is missing."""


class NetworkBlockedError(RuntimeError):
    """Raised when offline mode catches an attempted network connection."""


@dataclass(frozen=True)
class CodeProvenance:
    commit: str
    tracked_tree_clean: bool


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeProvenanceError(f"git {' '.join(args)} failed: {message}")
    return completed.stdout.strip()


def code_provenance(project_root: Path) -> CodeProvenance:
    commit = _git(project_root, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise RuntimeProvenanceError(f"unexpected git commit value: {commit!r}")
    status = _git(project_root, "status", "--porcelain", "--untracked-files=no")
    if status:
        raise RuntimeProvenanceError(
            "tracked files have uncommitted changes; formal generation requires "
            "a committed code version:\n" + status
        )
    return CodeProvenance(commit=commit, tracked_tree_clean=True)


def dependency_versions() -> dict[str, str]:
    distributions = (
        "chronos-forecasting",
        "huggingface-hub",
        "numpy",
        "pandas",
        "PyYAML",
        "requests",
        "torch",
        "transformers",
    )
    versions: dict[str, str] = {}
    for name in distributions:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def runtime_metadata() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }


@contextmanager
def block_network() -> Iterator[None]:
    """Fail fast on socket connections and force Hugging Face local-only mode."""
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection
    env_names = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")
    previous_env = {name: os.environ.get(name) for name in env_names}

    def deny_connect(*_args: object, **_kwargs: object) -> None:
        raise NetworkBlockedError("network access is forbidden in offline mode")

    socket.socket.connect = deny_connect  # type: ignore[method-assign]
    socket.create_connection = deny_connect  # type: ignore[assignment]
    for name in env_names:
        os.environ[name] = "1"
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
