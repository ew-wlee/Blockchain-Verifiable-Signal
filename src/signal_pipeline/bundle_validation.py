"""Independent bundle verification, negative mutations, and second environment."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .hashing import canonical_json_bytes

POSITIVE_SCHEMA_VERSION = "step2-bundle-validation-v1"
NEGATIVE_SCHEMA_VERSION = "step2-bundle-negative-checks-v1"
SECOND_ENV_SCHEMA_VERSION = "step2-second-environment-v1"
TOOL_RELATIVE_PATH = Path("tools") / "verify_bundle.py"


class BundleValidationError(RuntimeError):
    """Raised when independent bundle verification does not pass."""


@dataclass(frozen=True)
class VerifierRun:
    interpreter: str
    exit_code: int
    report: dict[str, Any]


def _parse_verifier_stdout(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "schema_version": "standalone-bundle-verification-v1",
            "checks": [],
            "failures": [f"unparseable-validator-output:{completed.stdout[:200]!r}"],
            "passed": False,
        }
    return payload


def run_standalone_verifier(
    bundle_dir: Path,
    expected_manifest_hash: str,
    *,
    tool_path: Path,
    python_executable: Path | str = sys.executable,
    cwd: Path | None = None,
) -> VerifierRun:
    completed = subprocess.run(
        [
            str(python_executable),
            "-I",
            "-B",
            str(tool_path),
            "--bundle",
            str(bundle_dir),
            "--expected-manifest-hash",
            expected_manifest_hash,
        ],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return VerifierRun(
        interpreter=str(python_executable),
        exit_code=completed.returncode,
        report=_parse_verifier_stdout(completed),
    )


def positive_validation(
    bundle_dir: Path,
    expected_manifest_hash: str,
    *,
    project_root: Path,
) -> dict[str, Any]:
    tool_path = project_root / TOOL_RELATIVE_PATH
    run = run_standalone_verifier(
        bundle_dir,
        expected_manifest_hash,
        tool_path=tool_path,
        python_executable=sys.executable,
        cwd=project_root,
    )
    report = run.report
    try:
        report["bundle"] = bundle_dir.relative_to(project_root).as_posix()
    except ValueError:
        report["bundle"] = "<external-bundle>"
    passed = run.exit_code == 0 and report.get("passed") is True
    return {
        "schema_version": POSITIVE_SCHEMA_VERSION,
        "validator": "tools/verify_bundle.py",
        "validator_imports_project_package": False,
        "isolated": True,
        "interpreter": "venv-python",
        "exit_code": run.exit_code,
        "manifest_sha256": report.get("manifest_sha256"),
        "object_id": report.get("object_id"),
        "payload_commitment": report.get("payload_commitment"),
        "checks": report.get("checks", []),
        "failures": report.get("failures", []),
        "passed": passed,
    }


def _mutate_dataset(bundle_dir: Path) -> None:
    with (bundle_dir / "dataset.csv").open("ab") as handle:
        handle.write(b"tampered\n")


def _mutate_signals(bundle_dir: Path) -> None:
    with (bundle_dir / "signals.csv").open("ab") as handle:
        handle.write(b"tampered\n")


def _mutate_summary(bundle_dir: Path) -> None:
    with (bundle_dir / "backtest_summary.json").open("ab") as handle:
        handle.write(b" ")


def _mutate_config(bundle_dir: Path) -> None:
    with (bundle_dir / "config.json").open("ab") as handle:
        handle.write(b" ")


def _mutate_model_reference(bundle_dir: Path) -> None:
    with (bundle_dir / "model_reference.json").open("ab") as handle:
        handle.write(b" ")


def _delete_signals(bundle_dir: Path) -> None:
    (bundle_dir / "signals.csv").unlink()


def _rewrite_manifest(
    bundle_dir: Path, mutation: Callable[[dict[str, Any]], None]
) -> None:
    path = bundle_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutation(manifest)
    path.write_bytes(canonical_json_bytes(manifest))


def _mutate_path(bundle_dir: Path) -> None:
    def mutation(manifest: dict[str, Any]) -> None:
        manifest["files"][0]["path"] = "renamed.bin"

    _rewrite_manifest(bundle_dir, mutation)


def _mutate_order(bundle_dir: Path) -> None:
    _rewrite_manifest(bundle_dir, lambda manifest: manifest["files"].reverse())


def _mutate_object_id(bundle_dir: Path) -> None:
    _rewrite_manifest(
        bundle_dir, lambda manifest: manifest.__setitem__("object_id", "0x" + "0" * 64)
    )


def _add_extra_file(bundle_dir: Path) -> None:
    (bundle_dir / "extra.txt").write_text("not part of the bundle\n", encoding="utf-8")


def _mutate_manifest_whitespace(bundle_dir: Path) -> None:
    path = bundle_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


NEGATIVE_CASES: tuple[tuple[str, Callable[[Path], None]], ...] = (
    ("dataset_byte_changed", _mutate_dataset),
    ("signals_byte_changed", _mutate_signals),
    ("backtest_summary_byte_changed", _mutate_summary),
    ("config_byte_changed", _mutate_config),
    ("model_reference_byte_changed", _mutate_model_reference),
    ("signals_file_deleted", _delete_signals),
    ("manifest_path_changed", _mutate_path),
    ("files_order_reversed", _mutate_order),
    ("object_id_replaced", _mutate_object_id),
    ("extra_file_added", _add_extra_file),
    ("manifest_whitespace_changed", _mutate_manifest_whitespace),
)


def negative_validation(
    bundle_dir: Path,
    expected_manifest_hash: str,
    *,
    project_root: Path,
) -> dict[str, Any]:
    tool_path = project_root / TOOL_RELATIVE_PATH
    case_reports: list[dict[str, Any]] = []
    all_rejected = True
    with tempfile.TemporaryDirectory(prefix="step2-negative-") as temp_name:
        temp_root = Path(temp_name)
        for name, mutate in NEGATIVE_CASES:
            target = temp_root / name
            shutil.copytree(bundle_dir, target)
            mutate(target)
            run = run_standalone_verifier(
                target,
                expected_manifest_hash,
                tool_path=tool_path,
                python_executable=sys.executable,
                cwd=project_root,
            )
            rejected = run.exit_code != 0 and run.report.get("passed") is False
            all_rejected = all_rejected and rejected
            case_reports.append(
                {
                    "case": name,
                    "validator_exit_code": run.exit_code,
                    "rejected": rejected,
                    "failures": run.report.get("failures", []),
                }
            )
    report = {
        "schema_version": NEGATIVE_SCHEMA_VERSION,
        "cases": case_reports,
        "case_count": len(case_reports),
        "all_rejected": all_rejected,
        "passed": all_rejected,
    }
    if not all_rejected:
        raise BundleValidationError("one or more tamper cases were not rejected")
    return report


def second_environment_validation(
    bundle_dir: Path,
    expected_manifest_hash: str,
    *,
    project_root: Path,
) -> dict[str, Any]:
    interpreter = Path("/usr/bin/python3")
    if not interpreter.is_file():
        raise BundleValidationError(
            "second environment requires /usr/bin/python3, but it is unavailable"
        )
    version = subprocess.run(
        [str(interpreter), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode != 0:
        raise BundleValidationError("unable to query the second interpreter version")
    with tempfile.TemporaryDirectory(prefix="step2-second-env-") as temp_name:
        temp_root = Path(temp_name)
        copied_bundle = temp_root / "bundle"
        copied_tool = temp_root / "verify_bundle.py"
        shutil.copytree(bundle_dir, copied_bundle)
        shutil.copy2(project_root / TOOL_RELATIVE_PATH, copied_tool)
        completed = subprocess.run(
            [
                str(interpreter),
                "-I",
                "-B",
                str(copied_tool),
                "--bundle",
                str(copied_bundle),
                "--expected-manifest-hash",
                expected_manifest_hash,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        report = _parse_verifier_stdout(completed)
    passed = (
        completed.returncode == 0
        and report.get("passed") is True
        and report.get("manifest_sha256") == expected_manifest_hash
    )
    result = {
        "schema_version": SECOND_ENV_SCHEMA_VERSION,
        "interpreter": str(interpreter),
        "interpreter_version": (version.stdout or version.stderr).strip(),
        "isolated_args": ["-I", "-B"],
        "bundle_copied_outside_project": True,
        "validator_copied_with_bundle": True,
        "validator_imports_project_package": False,
        "exit_code": completed.returncode,
        "manifest_sha256": report.get("manifest_sha256"),
        "object_id": report.get("object_id"),
        "payload_commitment": report.get("payload_commitment"),
        "checks": report.get("checks", []),
        "failures": report.get("failures", []),
        "passed": passed,
    }
    if not passed:
        raise BundleValidationError("second environment verification failed")
    return result


def assert_positive_bundle(report: Mapping[str, Any]) -> None:
    if report.get("passed") is not True:
        raise BundleValidationError(
            f"bundle positive verification failed: {report.get('failures')}"
        )
