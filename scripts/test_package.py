"""Regression checks for byte-preserving native session selection/packaging."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch
import zipfile
import unittest

spec = importlib.util.spec_from_file_location('submission_package', Path(__file__).with_name('package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class NativeExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def native(self, cwd):
        source = self.root / 'session.jsonl'
        source.write_text(json.dumps({'type': 'session_meta', 'payload': {'cwd': str(cwd), 'id': 'native-id'}}) + '\n', encoding='utf-8')
        return source

    def test_discovery_excludes_unrelated_chats(self):
        source = self.native(package.ROOT / 'backend')
        self.assertTrue(package.project_session(source))
        source = self.native(self.root / 'unrelated')
        self.assertFalse(package.project_session(source))

    def test_snapshots_preserve_native_bytes_and_original_extension(self):
        source = self.root / 'official-export.zip'
        source.write_bytes(b'first exact native bytes\x00')
        target = self.root / 'collected'
        target.mkdir()
        first = package.copy_snapshot(source, target)
        self.assertEqual(first.read_bytes(), source.read_bytes())
        source.write_bytes(b'updated exact native bytes\xff')
        second = package.copy_snapshot(source, target)
        self.assertNotEqual(first, second)
        self.assertEqual(second.suffix, '.zip')
        self.assertEqual(first.read_bytes(), b'first exact native bytes\x00')
        self.assertEqual(second.read_bytes(), source.read_bytes())
        self.assertEqual(package.copy_snapshot(source, target), second)

    def test_generated_manifest_alone_cannot_satisfy_native_export_requirement(self):
        repo = self.root / 'repository'
        sessions = repo / 'ai-sessions'
        sessions.mkdir(parents=True)
        manifest = sessions / 'manifest.json'
        manifest.write_text(json.dumps({'files': [], 'kind': 'generated provenance'}) + '\n')
        original = manifest.read_bytes()
        output = self.root / 'submission.zip'
        with patch.object(package, 'ROOT', repo), patch('sys.argv', [
            'package.py', '--session-root', str(self.root / 'absent'), '--output', str(output)
        ]), self.assertRaisesRegex(SystemExit, 'No native project sessions found'):
            package.main()
        self.assertFalse(output.exists())
        self.assertEqual(manifest.read_bytes(), original)
        # Supplying the same generated file explicitly cannot bypass the guard.
        with patch.object(package, 'ROOT', repo), patch('sys.argv', [
            'package.py', '--session-root', str(self.root / 'absent'),
            '--native-export', str(manifest), '--output', str(output)
        ]), self.assertRaisesRegex(SystemExit, 'Native export must be'):
            package.main()
        self.assertFalse(output.exists())

    def test_archive_preserves_all_commits_and_excludes_runtime_secrets(self):
        repo = self.root / 'repository'
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Package Test', '-c',
                                            'user.email=package@example.test', *args], cwd=repo)
        git('init', '--initial-branch=main', '--quiet')
        (repo / 'source.txt').write_text('first version')
        git('add', 'source.txt')
        git('commit', '--quiet', '-m', 'first implementation')
        first = git('rev-parse', 'HEAD').strip()
        (repo / 'source.txt').write_text('second version')
        git('commit', '--quiet', '-am', 'second implementation')
        second = git('rev-parse', 'HEAD').strip()
        (repo / '.env').write_text('SECRET=never package runtime secrets')
        source = self.native(repo)
        sessions = repo / 'ai-sessions'
        sessions.mkdir()
        (sessions / 'manifest.json').write_text(json.dumps({'kind': 'generated provenance'}))
        summary = io.StringIO()
        output = self.root / 'submission.zip'
        with redirect_stdout(summary), patch.object(package, 'ROOT', repo), patch('sys.argv', [
            'package.py', '--session-root', str(self.root / 'absent'),
            '--session-file', str(source), '--output', str(output)
        ]):
            package.main()
        self.assertIn('Native session/export files: 1;', summary.getvalue())
        extracted = self.root / 'extracted'
        with zipfile.ZipFile(output) as archive:
            self.assertNotIn('youtube/.env', archive.namelist())
            self.assertIn('youtube/ai-sessions/manifest.json', archive.namelist())
            self.assertEqual(archive.read('youtube/ai-sessions/session.jsonl'), source.read_bytes())
            archive.extractall(extracted)
        history = subprocess.check_output(['git', 'log', '--format=%H'], cwd=extracted / 'youtube').splitlines()
        self.assertEqual(history, [second, first])
        subprocess.run(['git', 'fsck', '--no-dangling'], cwd=extracted / 'youtube', check=True)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd required for compressed metadata')
    def test_compressed_native_session_stays_compressed(self):
        source = self.native(package.ROOT)
        compressed = source.with_suffix('.jsonl.zst')
        subprocess.run(['zstd', '-q', str(source), '-o', str(compressed)], check=True)
        self.assertTrue(package.project_session(compressed))
        original = compressed.read_bytes()
        target = self.root / 'collected'
        target.mkdir()
        copied = package.copy_snapshot(compressed, target)
        self.assertTrue(copied.name.endswith('.jsonl.zst'))
        self.assertEqual(copied.read_bytes(), original)
        self.assertEqual(compressed.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
