"""Installer progress stays live and reports actual process outcomes."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from workbench.brew import install
from workbench.engine import execute
from workbench.process import stream_command


class ProgressTests(unittest.TestCase):
    def test_partial_line_reaches_terminal_before_process_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            signal = Path(directory) / 'seen'

            class Terminal(io.StringIO):
                def write(self, value):
                    result = super().write(value)
                    if 'Password: ' in self.getvalue():
                        signal.touch()
                    return result

            script = ('import pathlib,sys,time; '
                      'sys.stdout.write("Password: "); sys.stdout.flush(); '
                      'p=pathlib.Path(sys.argv[1]); deadline=time.monotonic()+3\n'
                      'while not p.exists() and time.monotonic()<deadline: time.sleep(.01)\n'
                      'sys.exit(0 if p.exists() else 9)')
            output = Terminal()
            with patch('sys.stdout', output):
                code = stream_command([sys.executable, '-c', script, str(signal)], label='example')
            self.assertEqual(code, 0, 'output was buffered until newline/process exit')
            self.assertIn('Password: ', output.getvalue())

    def test_quiet_process_reports_elapsed_time_and_preserves_exit_code(self):
        output, log = io.StringIO(), io.StringIO()
        with patch('sys.stdout', output):
            code = stream_command([sys.executable, '-c', 'import time; time.sleep(.2); raise SystemExit(7)'],
                                  label='Homebrew 2/4 cmake', log=log, heartbeat=.05)
        self.assertEqual(code, 7)
        self.assertIn('[WAIT] Homebrew 2/4 cmake', output.getvalue())
        self.assertIn('已运行', output.getvalue())
        self.assertEqual(log.getvalue(), output.getvalue())

    def test_split_utf8_ansi_and_carriage_return_output(self):
        script = ('import os,time\n'
                  'for chunk in [b"\\x1b[", b"31m", b"\\xe4", b"\\xb8\\xad", '
                  'b"\\x1b[0m", b" 50%\\r", b"100%\\n"]:\n'
                  ' os.write(1,chunk); time.sleep(.02)\n')
        output, log = io.StringIO(), io.StringIO()
        with patch('sys.stdout', output):
            self.assertEqual(stream_command([sys.executable, '-c', script], label='download', log=log), 0)
        self.assertEqual(output.getvalue(), '中 50%\r100%\n')
        self.assertEqual(log.getvalue(), '中 50%\n100%\n')

    def fake_brew(self, root):
        brew = root / 'brew'
        brew.write_text(f'#!{sys.executable}\n' + '''import json,os,pathlib,sys
args=sys.argv[1:]
manifest=pathlib.Path(next(a.split('=',1)[1] for a in args if a.startswith('--file=')))
name=manifest.read_text().split('"')[1]
with (pathlib.Path(os.environ['BREW_TEST_ROOT'])/'calls').open('a') as out:
    out.write(json.dumps([name, args])+"\\n")
if args[1]=='check':
    sys.exit(0 if name=='existing' else 1)
print('Downloading '+name, flush=True)
print('backend detail '+name, file=sys.stderr, flush=True)
sys.exit(7 if name=='broken' else 0)
''')
        brew.chmod(0o755)
        return brew

    def test_brew_failure_counts_reuse_success_and_unattempted_and_is_logged(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.geteuid', return_value=1000):
            root = Path(directory)
            brew = self.fake_brew(root)
            manifest = root / 'Brewfile'
            manifest.write_text('brew "existing"\nbrew "fresh"\ncask "broken"\ncask "later"\n')
            step = dict(component='platform', action='install', command=[sys.executable, '-c',
                'from pathlib import Path; from workbench.brew import install; import sys; '
                'sys.exit(install(sys.argv[1],Path(sys.argv[2]),"install"))', str(brew), str(manifest)])
            output = io.StringIO()
            with patch.dict(os.environ, BREW_TEST_ROOT=str(root)), patch('sys.stdout', output):
                self.assertEqual(execute([step], REPO, root, 'macos'), 7)
            text = output.getvalue()
            self.assertIn('[SKIP] existing 已安装', text)
            self.assertIn('[OK] fresh', text)
            self.assertIn('[FAIL] broken', text)
            self.assertIn('已完成 2/4', text)
            self.assertIn('失败 1 项，未执行 1 项', text)
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
            installs = [(name, args) for name, args in calls if args[1] == 'install']
            self.assertEqual([name for name, _ in installs], ['fresh', 'broken'])
            self.assertTrue(all('--no-upgrade' in args and '--verbose' in args for _, args in installs))
            log = next((root / 'logs').glob('*.log')).read_text()
            self.assertIn('[SKIP] existing', log)
            self.assertIn('backend detail fresh', log)
            self.assertIn('[FAIL] broken', log)
            receipt = json.loads(next((root / 'data/workbench/runs').glob('*.json')).read_text())
            self.assertEqual(receipt['status'], 'failed')
            self.assertIn('elapsed_seconds', receipt['steps'][0])

    def test_update_does_not_skip_existing_packages_or_disable_upgrades(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brew = self.fake_brew(root)
            manifest = root / 'Brewfile'
            manifest.write_text('brew "existing"\n')
            output = io.StringIO()
            with patch.dict(os.environ, BREW_TEST_ROOT=str(root)), patch('sys.stdout', output):
                self.assertEqual(install(brew, manifest, 'update'), 0)
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1][1], 'install')
            self.assertNotIn('--no-upgrade', calls[0][1])
            self.assertIn('[BREW DONE] 1/1', output.getvalue())

    def test_unsupported_manifest_fails_before_any_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'Brewfile'
            manifest.write_text('brew "existing"\ncask "app", args: { appdir: "~/Apps" }\n')
            with patch('workbench.brew.stream_command') as run:
                with self.assertRaisesRegex(RuntimeError, 'Brewfile:2'):
                    install('brew', manifest, 'install')
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
