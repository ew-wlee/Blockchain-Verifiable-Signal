"""Deterministic envelope and strict validation against Member 1's frozen anchor."""
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from tools.verify_bundle import BUNDLE_FILES, canonical_bytes, verify


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pack_bundle(directory):
    directory = Path(directory)
    if not verify(directory)['passed']:
        raise ValueError('source bundle is invalid')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in BUNDLE_FILES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (directory / name).read_bytes())
    data = out.getvalue()
    # Common transport padding makes both RS schemes exactly 2x storage.
    return data.ljust((len(data) + 5) // 6 * 6, b'\0')


def unpack_bundle(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sorted(archive.namelist()) != sorted(BUNDLE_FILES):
            raise ValueError('envelope must contain exactly the six expected files')
        # No extractall: reject path traversal, duplicates, compression bombs.
        if any(i.file_size > 32 * 1024 * 1024 or i.compress_type != zipfile.ZIP_STORED for i in archive.infolist()):
            raise ValueError('unsupported envelope entry')
        return {name: archive.read(name) for name in BUNDLE_FILES}


def validate_payload(data, anchor):
    """Each invocation independently validates bytes; exceptions fail closed."""
    try:
        files = unpack_bundle(data)
        with tempfile.TemporaryDirectory(prefix='signal-verify-') as tmp:
            root = Path(tmp)
            for name, content in files.items():
                (root / name).write_bytes(content)
            report = verify(root, anchor['manifest_hash'].removeprefix('0x'))
        manifest = json.loads(files['manifest.json'])
        model = json.loads(files['model_reference.json'])
        bindings = {
            'object_id': manifest['object_id'],
            'data_hash': '0x' + digest(files['dataset.csv']),
            'signal_hash': '0x' + digest(files['signals.csv']),
            'model_hash': '0x' + model['checkpoint_commitment_sha256'],
            'manifest_hash': '0x' + digest(files['manifest.json']),
        }
        matches = all(anchor[key] == value for key, value in bindings.items())
        return {'passed': report['passed'] and matches,
                'manifest_hash': bindings['manifest_hash'],
                'payload_commitment': manifest['payload_commitment'],
                'object_id': manifest['object_id'], 'checks': report['checks']}
    except (ValueError, KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile):
        return {'passed': False, 'manifest_hash': None, 'payload_commitment': None}


def trace_signal(data):
    import csv
    files = unpack_bundle(data)
    row = next(csv.DictReader(io.StringIO(files['signals.csv'].decode())))
    model = json.loads(files['model_reference.json'])
    return {'signal': row, 'model_id': model['model_id'], 'revision': model['resolved_revision']}
