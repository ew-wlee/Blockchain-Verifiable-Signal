"""Create a deterministic standalone evaluation package from an explicit file allowlist."""
import argparse
import io
import json
from pathlib import Path
import zipfile

from evaluation.common import ROOT
from retrieval.bundle import digest


def package(output):
    output = Path(output)
    if output.exists():
        raise ValueError(f'Refusing to overwrite package: {output}')
    files = {ROOT / name for name in (
        '.gitattributes', 'README.md', 'requirements-part3.txt', 'requirements-part3.lock',
        'configs/evaluation.json', 'configs/retrieval.json', 'deployments/sepolia.json',
        'tools/verify_bundle.py', 'tools/compare_part3_records.py', 'tools/package_part3.py',
        'tools/prune_presentation_assets.py',
        'scripts/build-evaluation-deck.mjs', 'docs/part3-reproduction.md',
        'report/04-member3-evaluation.md', 'report/final-report.md',
        'report/02-member2-retrieval.md', 'artifacts/part2/handoff.json',
        'report/presentations/midterm.pptx', 'report/presentations/final.pptx',
        'report/presentations/speaking-notes.md', 'report/presentations/template_source.json',
        'artifacts/part3/handoff.json', 'artifacts/part3/smoke.json', 'artifacts/part3/full_replay.json',
        'artifacts/part3/demo_success.json', 'artifacts/part3/tests_part2.txt', 'artifacts/part3/tests_part3.txt',
    )}
    for folder in ('evaluation', 'retrieval', 'demo', 'tests_part2', 'tests_part3',
                   'artifacts/step2/bundle', 'artifacts/part3/formal',
                   'artifacts/part3/analysis', 'artifacts/part3/figures', 'artifacts/part2-validation-20261115'):
        files.update(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    missing = sorted(str(p) for p in files if not p.is_file())
    if missing:
        raise ValueError(f'Incomplete delivery: {missing}')
    contents = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in sorted(files)}
    manifest = {name: digest(data) for name, data in contents.items()}
    contents['PACKAGE_CHECKSUMS.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    data = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity failed')
        for name, expected in manifest.items():
            if digest(archive.read(name)) != expected:
                raise ValueError(f'Archive byte verification failed: {name}')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return {'files': len(manifest), 'sha256': digest(data), 'bytes': len(data), 'package_checksums_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.output), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
