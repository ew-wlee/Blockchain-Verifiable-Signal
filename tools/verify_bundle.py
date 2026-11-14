#!/usr/bin/env python3
"""Standalone stdlib-only verifier for a six-file verifiable signal bundle.

This file deliberately does not import the generator package. It can be copied
with a frozen bundle into an isolated Python environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath

BUNDLE_FILES = (
    "backtest_summary.json",
    "config.json",
    "dataset.csv",
    "manifest.json",
    "model_reference.json",
    "signals.csv",
)
CONTENT_FILES = tuple(name for name in BUNDLE_FILES if name != "manifest.json")
KEY_HASH_FIELDS = {
    "config_sha256": "config.json",
    "dataset_sha256": "dataset.csv",
    "signal_sha256": "signals.csv",
    "model_reference_sha256": "model_reference.json",
    "backtest_summary_sha256": "backtest_summary.json",
}
SCHEMA_VERSION = "verifiable-signal-bundle-v1"


def canonical_bytes(payload):
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value, length=64):
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_relative_path(value):
    if not isinstance(value, str) or not value:
        return False
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and path.name == value


def verify(bundle_dir, expected_manifest_hash=None):
    checks = []
    failures = []

    def check(name, passed, details=None):
        record = {"name": name, "passed": bool(passed)}
        if details is not None:
            record["details"] = details
        checks.append(record)
        if not passed:
            failures.append(name)

    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / "manifest.json"
    actual_files = []
    extra_directories = []
    symlinks = []
    if bundle_dir.is_dir():
        for root, dirs, files in os.walk(str(bundle_dir)):
            root_path = Path(root)
            for directory in dirs:
                child = root_path / directory
                if child.is_symlink():
                    symlinks.append(str(child.relative_to(bundle_dir)))
                else:
                    extra_directories.append(str(child.relative_to(bundle_dir)))
            for filename in files:
                path = root_path / filename
                if path.is_symlink():
                    symlinks.append(str(path.relative_to(bundle_dir)))
                else:
                    actual_files.append(path.relative_to(bundle_dir).as_posix())
    actual_files.sort()

    check("bundle_directory_exists", bundle_dir.is_dir())
    check(
        "exactly_six_files",
        actual_files == sorted(BUNDLE_FILES),
        {"actual": actual_files, "expected": sorted(BUNDLE_FILES)},
    )
    check("no_extra_directories", not extra_directories, extra_directories)
    check("no_symlinks", not symlinks, symlinks)

    manifest = None
    raw_manifest = b""
    if manifest_path.is_file():
        raw_manifest = manifest_path.read_bytes()
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except Exception as error:
            failures.append("manifest_json_invalid")
            checks.append(
                {
                    "name": "manifest_json_invalid",
                    "passed": False,
                    "details": type(error).__name__,
                }
            )
    check("manifest_exists", manifest is not None)
    if not isinstance(manifest, dict):
        manifest = {}
    check(
        "manifest_is_canonical_json",
        bool(raw_manifest) and raw_manifest == canonical_bytes(manifest),
    )
    check(
        "manifest_schema",
        manifest.get("schema_version") == SCHEMA_VERSION,
    )
    check(
        "manifest_code_commits",
        _is_sha256(manifest.get("code_commit"), 40)
        and _is_sha256(manifest.get("step1", {}).get("code_commit"), 40),
    )
    object_id = manifest.get("object_id")
    object_id_format_ok = (
        isinstance(object_id, str)
        and object_id.startswith("0x")
        and _is_sha256(object_id[2:])
    )
    check("object_id_format", object_id_format_ok)
    identity = dict(manifest)
    identity.pop("object_id", None)
    expected_object_id = "0x" + sha256_bytes(canonical_bytes(identity))
    check(
        "object_id_recomputes",
        object_id == expected_object_id,
        {"actual": object_id, "expected": expected_object_id},
    )

    files = manifest.get("files")
    files_list_ok = isinstance(files, list)
    entries = files if files_list_ok else []
    expected_paths = list(CONTENT_FILES)
    actual_paths = [entry.get("path") for entry in entries if isinstance(entry, dict)]
    check(
        "files_cover_exactly_five_content_files",
        files_list_ok
        and len(entries) == 5
        and actual_paths == expected_paths
        and len(set(actual_paths)) == len(actual_paths),
        {"actual": actual_paths, "expected": expected_paths},
    )
    paths_safe = all(_safe_relative_path(path) for path in actual_paths)
    check("files_paths_safe", paths_safe)

    computed_hashes = {}
    hashes_ok = True
    sizes_ok = True
    for entry in entries:
        if not isinstance(entry, dict):
            hashes_ok = False
            sizes_ok = False
            continue
        relative = entry.get("path")
        if not isinstance(relative, str) or not _safe_relative_path(relative):
            hashes_ok = False
            sizes_ok = False
            continue
        path = bundle_dir / relative
        if not path.is_file() or path.is_symlink():
            hashes_ok = False
            sizes_ok = False
            continue
        computed_hashes[relative] = sha256_file(path)
        if path.stat().st_size != entry.get("size"):
            sizes_ok = False
        if entry.get("sha256") != computed_hashes[relative]:
            hashes_ok = False
    check("file_sizes_match", sizes_ok)
    check("file_sha256_match", hashes_ok)

    try:
        payload_commitment = sha256_bytes(canonical_bytes(entries))
    except Exception:
        payload_commitment = None
    check(
        "payload_commitment_recomputes",
        payload_commitment == manifest.get("payload_commitment"),
        {
            "actual": manifest.get("payload_commitment"),
            "expected": payload_commitment,
        },
    )

    key_hashes_ok = True
    for field, filename in KEY_HASH_FIELDS.items():
        expected_hash = manifest.get(field)
        actual_hash = computed_hashes.get(filename)
        if expected_hash != actual_hash:
            key_hashes_ok = False
    check("key_hashes_match_files", key_hashes_ok)

    manifest_sha256 = sha256_file(manifest_path) if manifest_path.is_file() else None
    if expected_manifest_hash is not None:
        check(
            "manifest_hash_matches_expected",
            manifest_sha256 == expected_manifest_hash,
            {"actual": manifest_sha256, "expected": expected_manifest_hash},
        )

    passed = not failures
    return {
        "schema_version": "standalone-bundle-verification-v1",
        "bundle": str(bundle_dir),
        "manifest_sha256": manifest_sha256,
        "object_id": object_id,
        "payload_commitment": manifest.get("payload_commitment"),
        "checks": checks,
        "failures": failures,
        "passed": passed,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify a canonical six-file verifiable signal bundle."
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--expected-manifest-hash")
    args = parser.parse_args(argv)
    try:
        report = verify(args.bundle, args.expected_manifest_hash)
    except Exception as error:
        report = {
            "schema_version": "standalone-bundle-verification-v1",
            "bundle": str(args.bundle),
            "checks": [],
            "failures": [f"{type(error).__name__}:{error}"],
            "passed": False,
        }
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
