import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_portable
import portable_install


class PortableTests(unittest.TestCase):
    def test_generated_paths_are_local_to_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'Chinese 中文 space'
            config = portable_install.default_config(root)
            for value in config['paths'].values():
                self.assertTrue(Path(value).is_relative_to(root.resolve()))
            self.assertNotIn('api_key', config['models']['flash'])

    def test_config_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertTrue(portable_install.write_config_once(root))
            original = (root/'config.yaml').read_bytes()
            self.assertFalse(portable_install.write_config_once(root))
            self.assertEqual(original, (root/'config.yaml').read_bytes())
            self.assertIn('metadata_db', json.loads(original)['paths'])

    def test_bundle_allowlist_rejects_sensitive_or_escape_paths(self):
        for path in ['config.yaml', 'source_catalog.yaml', 'data/test.py', '../secret.py',
                     '.env', '.git/config', 'research.db', 'corpus.txt.pdf', 'data/corpus.jsonl']:
            self.assertFalse(build_portable.allowed(path), path)
        for path in ['mega_agent.py', 'docs/operations.md', 'LICENSE', 'Install-Windows.cmd']:
            self.assertTrue(build_portable.allowed(path), path)

    def test_offline_install_command(self):
        command = portable_install.install_command(Path('python.exe'), 'minimal', Path('wheels'))
        self.assertIn('--no-index', command)
        self.assertIn('--find-links', command)
        self.assertIn('PyYAML==6.0.2', command)

    def test_dry_run_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(portable_install, 'ROOT', Path(folder)):
            with patch.object(portable_install.subprocess, 'run') as run:
                self.assertEqual(portable_install.main(['--dry-run']), 0)
                run.assert_not_called()
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_launcher_environment_excludes_foreign_python_paths(self):
        with patch.dict('os.environ', {'PYTHONPATH': 'foreign', 'PYTHONHOME': 'foreign'}):
            env = portable_install.clean_env()
            self.assertNotIn('PYTHONPATH', env)
            self.assertNotIn('PYTHONHOME', env)
            self.assertEqual(env['PYTHONNOUSERSITE'], '1')


if __name__ == '__main__':
    unittest.main()
