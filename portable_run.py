"""Launch MegaRAG with the installed interpreter and a deterministic cwd."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from portable_install import clean_env

ROOT = Path(__file__).resolve().parent
ENTRIES = {'ui': 'research_workbench.py', 'cli': 'mega_agent.py',
           'mcp': 'mega_mcp.py', 'health': 'index_health.py',
           'build-index': 'build_index.py', 'import': 'import_megadigital.py',
           'probe': 'term_probe.py'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=ENTRIES)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    receipt_path = ROOT / '.portable-install.json'
    if not receipt_path.is_file():
        parser.error('Run Install-Windows.cmd first.')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt['root'] != str(ROOT):
        parser.error('Installed folder moved; extract a fresh bundle and reinstall.')
    if receipt['profile'] != 'full' and args.mode in {'ui', 'cli', 'mcp'}:
        parser.error('This entry requires installation with --profile full.')
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file() or not (ROOT / 'config.yaml').is_file():
        parser.error('Installation incomplete: interpreter/config missing.')
    # MCP stdout must contain only the child protocol, never launcher banners.
    return subprocess.call([str(python), str(ROOT / ENTRIES[args.mode]), *args.arguments], cwd=ROOT, env=clean_env())


if __name__ == '__main__':
    raise SystemExit(main())
