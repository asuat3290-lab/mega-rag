"""Build a code-only ZIP from an explicit Git index allow-list. Never package data."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '0.1.0a1'


def allowed(path: str):
    p = Path(path)
    if p.is_absolute() or '..' in p.parts or any(part.startswith('.') for part in p.parts):
        return False
    if p.as_posix() in {'config.yaml', 'source_catalog.yaml', 'megadigital_import_manifest.json'}:
        return False
    if len(p.parts) > 1 and p.parts[0] != 'docs':
        return False
    return p.suffix.lower() in {'.py', '.md', '.yaml', '.txt', '.cmd'} or p.name == 'LICENSE'


def main():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    files = sorted(name for name in tracked if name and allowed(name))
    required = {'portable_install.py', 'portable_run.py', 'portable_paths.py',
                'Install-Windows.cmd', 'Start-Workbench.cmd', 'LICENSE',
                'requirements.txt', 'requirements-mcp.txt', 'test_philology_coverage.py'}
    if not required.issubset(files):
        raise RuntimeError('Required files missing from Git index; git add reviewed files first.')
    payload = {}
    for name in files:
        path = ROOT / name
        if path.is_symlink():
            raise RuntimeError(f'Symlink refused: {name}')
        payload[name] = path.read_bytes()
    manifest = {'bundle_version': VERSION, 'files': {
        name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        for name, data in payload.items()}}
    payload['BUNDLE-MANIFEST.json'] = (json.dumps(manifest, indent=2, ensure_ascii=False)+'\n').encode()
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    archive = destination / f'MegaRAG-{VERSION}-windows-source.zip'
    prefix = f'MegaRAG-{VERSION}/'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(prefix+name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'files': len(payload), 'sha256': digest}))


if __name__ == '__main__':
    main()
