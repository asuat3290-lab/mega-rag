"""Install this source bundle into its own venv. No admin rights or data import."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def clean_env():
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['PYTHONNOUSERSITE'] = '1'
    env['GRADIO_ANALYTICS_ENABLED'] = 'False'
    env['HF_HUB_DISABLE_TELEMETRY'] = '1'
    return env


def default_config(root: Path):
    data = root.resolve() / 'data'
    paths = {name: str(data / rel) for name, rel in {
        'ocr_texts': 'corpus', 'metadata_db': 'metadata.db',
        'vector_db': 'vectors.lancedb', 'cache_db': 'cache.db',
        'research_exports': 'research_exports',
        'research_library_db': 'research_library.db',
        'research_library_exports': 'research_library_exports',
        'sachregister_db': 'sachregister.db',
        'megadigital_downloads': 'megadigital',
        'import_manifest': 'megadigital_import_manifest.json',
        'embedding_state': '.embed_megadigital_state.json',
    }.items()}
    return {
        'paths': paths,
        'ollama': {'base_url': 'http://localhost:11434', 'embedding_model': 'bge-m3', 'embedding_dim': 1024},
        'retrieval': {'top_k_bm25': 30, 'top_k_embedding': 30, 'final_top_k': 20, 'mmr_lambda': 0.7, 'rrf_k': 60},
        'chunking': {'strategy': 'by_page', 'max_chunk_chars': 3000},
        'models': {name: {'provider': 'openai', 'model': model, 'base_url': 'https://api.deepseek.com/v1', 'api_key_env': 'DEEPSEEK_API_KEY'}
                   for name, model in [('flash', 'deepseek-chat'), ('pro', 'deepseek-reasoner')]},
        'cache': {'ttl_hours': 48, 'max_entries': 10000},
    }


def write_config_once(root: Path) -> bool:
    # JSON is valid YAML. No dependency or credential access is needed here.
    path = root / 'config.yaml'
    try:
        with path.open('x', encoding='utf-8') as handle:
            json.dump(default_config(root), handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    except FileExistsError:
        return False
    return True


def install_command(python: Path, profile: str, wheelhouse: Path | None):
    command = [str(python), '-m', 'pip', 'install']
    if wheelhouse is not None:
        command += ['--no-index', '--find-links', str(wheelhouse.resolve())]
    if profile == 'minimal':
        command += ['PyYAML==6.0.2']
    else:
        command += ['-r', str(ROOT / 'requirements.txt'), '-r', str(ROOT / 'requirements-mcp.txt')]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['minimal', 'full'], default='full')
    parser.add_argument('--wheelhouse', type=Path, help='Use only an existing offline wheel directory')
    parser.add_argument('--dry-run', action='store_true', help='Show plan without writing files or installing anything')
    args = parser.parse_args(argv)
    if args.wheelhouse and not args.wheelhouse.is_dir():
        parser.error('wheelhouse must be an existing directory')
    target = ROOT / '.venv'
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if args.dry_run:
        print(json.dumps({'root': str(ROOT), 'profile': args.profile,
                          'pip_command': install_command(python, args.profile, args.wheelhouse),
                          'existing_config_preserved': (ROOT / 'config.yaml').exists()}, indent=2))
        return 0
    if sys.version_info[:2] != (3, 13):
        parser.error('This bundle targets Python 3.13; install 64-bit Python 3.13 first.')
    if sys.maxsize <= 2**32:
        parser.error('A 64-bit Python is required.')
    # Detect accidental relocation before writing or running a stale venv.
    receipt = ROOT / '.portable-install.json'
    if receipt.exists() and json.loads(receipt.read_text(encoding='utf-8'))['root'] != str(ROOT):
        parser.error('Installed folder has moved. Extract a fresh bundle and reinstall; preserve old data separately.')
    if not target.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    elif not python.is_file():
        parser.error('Existing .venv is incomplete; use a new empty installation directory.')
    subprocess.run(install_command(python, args.profile, args.wheelhouse), check=True, env=clean_env(), cwd=ROOT)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True, env=clean_env(), cwd=ROOT)
    (ROOT / 'data' / 'corpus').mkdir(parents=True, exist_ok=True)
    created = write_config_once(ROOT)
    print('Created local config.' if created else 'Preserved existing config unchanged.')
    subprocess.run([str(python), '-m', 'unittest', 'test_philology_coverage', '-v'], check=True, env=clean_env(), cwd=ROOT)
    if args.profile == 'full':
        subprocess.run([str(python), '-c', 'import gradio,lancedb,ollama,openai,pyarrow; from mcp.server.fastmcp import FastMCP'], check=True, env=clean_env(), cwd=ROOT)
    receipt.write_text(json.dumps({'root': str(ROOT), 'profile': args.profile, 'python': str(python)}, indent=2), encoding='utf-8')
    print('INSTALLATION VERIFIED. No corpus imported and no model called.')
    if args.profile == 'minimal':
        print('Minimal profile: synthetic tests and lexical utilities only; UI/MCP need --profile full.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
