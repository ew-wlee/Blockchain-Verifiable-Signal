from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from signal_pipeline.bundle_validation import (
    negative_validation,
    positive_validation,
    second_environment_validation,
)
from signal_pipeline.hashing import canonical_json_bytes
from test_bundle import _build

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "verify_bundle.py"


def _run_verifier(bundle_dir: Path, expected_manifest_hash: str):
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(TOOL),
            "--bundle",
            str(bundle_dir),
            "--expected-manifest-hash",
            expected_manifest_hash,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(completed.stdout)
    return completed.returncode, report


def _copy_bundle(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination


def _rewrite_manifest(bundle_dir: Path, mutate) -> None:
    path = bundle_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_bytes(canonical_json_bytes(manifest))


def test_standalone_validator_accepts_pristine_bundle(tmp_path):
    bundle_dir, result = _build(tmp_path)
    code, report = _run_verifier(bundle_dir, result.manifest_sha256)
    assert code == 0, report
    assert report["passed"] is True
    assert report["object_id"] == result.object_id
    assert report["manifest_sha256"] == result.manifest_sha256


@pytest.mark.parametrize(
    "case",
    ["dataset_byte", "summary_byte", "delete_file", "path", "order", "object_id", "extra", "whitespace"],
)
def test_tamper_cases_are_rejected(tmp_path, case):
    original_dir, result = _build(tmp_path, "original")
    bundle_dir = _copy_bundle(original_dir, tmp_path / case)
    manifest_path = bundle_dir / "manifest.json"

    if case == "dataset_byte":
        with (bundle_dir / "dataset.csv").open("ab") as handle:
            handle.write(b"tampered\n")
    elif case == "summary_byte":
        path = bundle_dir / "backtest_summary.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif case == "delete_file":
        (bundle_dir / "signals.csv").unlink()
    elif case == "path":
        _rewrite_manifest(
            bundle_dir,
            lambda manifest: manifest["files"][0].__setitem__("path", "renamed.csv"),
        )
    elif case == "order":
        _rewrite_manifest(
            bundle_dir,
            lambda manifest: manifest["files"].reverse(),
        )
    elif case == "object_id":
        _rewrite_manifest(
            bundle_dir,
            lambda manifest: manifest.__setitem__("object_id", "0x" + "0" * 64),
        )
    elif case == "extra":
        (bundle_dir / "extra.txt").write_text("not allowed\n", encoding="utf-8")
    elif case == "whitespace":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    else:  # pragma: no cover
        raise AssertionError(case)

    code, report = _run_verifier(bundle_dir, result.manifest_sha256)
    assert code == 1, report
    assert report["passed"] is False
    assert report["failures"]


def test_positive_validation_report_passes(tmp_path):
    bundle_dir, result = _build(tmp_path)
    report = positive_validation(
        bundle_dir, result.manifest_sha256, project_root=PROJECT_ROOT
    )
    assert report["passed"] is True
    assert report["object_id"] == result.object_id


def test_formal_negative_matrix_rejects_all_cases(tmp_path):
    bundle_dir, result = _build(tmp_path)
    report = negative_validation(
        bundle_dir, result.manifest_sha256, project_root=PROJECT_ROOT
    )
    assert report["passed"] is True
    assert report["case_count"] == 11
    assert all(case["rejected"] for case in report["cases"])


def test_second_environment_read_only_validation(tmp_path):
    bundle_dir, result = _build(tmp_path)
    report = second_environment_validation(
        bundle_dir, result.manifest_sha256, project_root=PROJECT_ROOT
    )
    assert report["passed"] is True
    assert report["bundle_copied_outside_project"] is True
    assert report["validator_imports_project_package"] is False
    assert report["manifest_sha256"] == result.manifest_sha256
