"""Shared experiment inputs, byte-preserving output and offline execution."""
from contextlib import contextmanager
import csv
import json
from pathlib import Path
import socket
from unittest.mock import patch

from retrieval.bundle import digest, pack_bundle
from retrieval.engine import Environment, prepare

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).write_bytes((json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode())


def write_csv(path, rows, fields=None):
    with Path(path).open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def new_directory(path):
    path = Path(path)
    if path.exists() and any(path.iterdir()):
        raise ValueError(f'Refusing to overwrite evidence: {path}')
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def offline():
    def deny(*args, **kwargs):
        raise RuntimeError('Network access forbidden during evaluation')
    with patch.object(socket.socket, 'connect', deny), patch.object(socket, 'create_connection', deny):
        yield


def inputs():
    evaluation = read_json(ROOT / 'configs/evaluation.json')
    retrieval = read_json(ROOT / evaluation['retrieval_config'])
    anchor = read_json(ROOT / evaluation['anchor_path'])
    payload = pack_bundle(ROOT / evaluation['bundle_dir'])
    schemes = [prepare(payload, anchor, **scheme) for scheme in retrieval['schemes']]
    configurations = {}
    for label, parameters in retrieval['environments'].items():
        env = Environment(**parameters)
        for prepared in schemes:
            if prepared.storage_overhead != 2:
                raise ValueError('All schemes must have exactly twice the common payload storage')
            key = f'{label}-{prepared.scheme}-{prepared.k}-{prepared.n}'
            configurations[key] = (prepared, env)
    if len(configurations) != 6:
        raise ValueError('Expected exactly six configurations')
    return evaluation, retrieval, anchor, payload, configurations


def fingerprints():
    paths = [ROOT / 'configs/evaluation.json', ROOT / 'configs/retrieval.json',
             ROOT / 'deployments/sepolia.json', ROOT / 'tools/verify_bundle.py']
    for folder in ('evaluation', 'retrieval', 'demo', 'artifacts/step2/bundle'):
        paths.extend(p for p in (ROOT / folder).iterdir() if p.is_file())
    return {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sorted(paths)}


def checksums(directory):
    directory = Path(directory)
    return {p.relative_to(directory).as_posix(): digest(p.read_bytes())
            for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'checksums.json'}


def verify_checksums(directory):
    directory = Path(directory)
    expected = read_json(directory / 'checksums.json')
    actual = checksums(directory)
    if expected != actual:
        changed = sorted(k for k in set(expected) | set(actual) if expected.get(k) != actual.get(k))
        raise ValueError(f'Checksum mismatch: {changed}')
    return len(expected)
