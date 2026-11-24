"""MacTeX is an optional post-check official-PKG installation, never a Brew prerequisite."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from workbench import mactex
from workbench.brew import entries
from workbench.cli import workflow
from workbench.data import read
from workbench.engine import execute
from workbench.registry import discover

SPEC = read(REPO / 'components/texlive/component.toml')['install']['macos']


class MacTeXTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'Software'
        self.output = io.StringIO()
        output_patch = patch('sys.stdout', self.output)
        output_patch.start()
        self.addCleanup(output_patch.stop)

    def test_offer_is_last_after_all_checks_and_absent_from_check_update_and_base(self):
        components = discover(REPO, self.root)
        steps = workflow(components, 'install', [], REPO, self.root, 'macos')
        self.assertEqual([(s['component'], s['action']) for s in steps if s['component'] == 'texlive'],
                         [('texlive', 'offer')])
        self.assertEqual((steps[-1]['component'], steps[-1]['action']), ('texlive', 'offer'))
        self.assertTrue(any(s['action'] == 'check' for s in steps[:-1]))
        self.assertNotIn('mactex', [name for _, name, _ in entries(REPO / 'platforms/macos/Brewfile')])
        for action, profile in [('check', []), ('update', []), ('install', ['base'])]:
            planned = workflow(components, action, profile, REPO, self.root, 'macos')
            self.assertFalse(any(s['component'] == 'texlive' for s in planned))
        linux = workflow(components, 'install', ['scientific'], REPO, self.root, 'fedora')
        self.assertIn(('texlive', 'install'), [(s['component'], s['action']) for s in linux])
        self.assertFalse(self.root.exists())

    def test_earlier_failure_never_reaches_offer(self):
        steps = [dict(component='required', action='install', command=['install']),
                 dict(component='required', action='check', command=['check']),
                 dict(component='texlive', action='offer', command=['offer'])]
        with patch('os.geteuid', return_value=1000), \
             patch('workbench.engine.stream_command', side_effect=[0, 7]) as run:
            self.assertEqual(execute(steps, REPO, self.root, 'macos'), 7)
        self.assertEqual([call.args[0] for call in run.call_args_list], [['install'], ['check']])

    def test_offer_no_declines_without_downloading_and_yes_installs(self):
        with patch.object(mactex, 'installed', return_value=False), \
             patch.object(mactex, 'confirm', return_value=False) as confirm, \
             patch.object(mactex, 'install_package', return_value=0) as install:
            self.assertEqual(mactex.mactex_action('offer', self.root, SPEC), 0)
            install.assert_not_called()
            self.assertIn('texlive install', self.output.getvalue())
            confirm.return_value = True
            self.assertEqual(mactex.mactex_action('offer', self.root, SPEC), 0)
            install.assert_called_once_with(self.root, SPEC, update=False)

    def test_manual_install_does_not_prompt_and_existing_install_does_not_download(self):
        with patch.object(mactex, 'installed', return_value=False) as ready, \
             patch.object(mactex, 'confirm') as confirm, \
             patch.object(mactex, 'install_package', return_value=0) as install:
            self.assertEqual(mactex.mactex_action('install', self.root, SPEC), 0)
            install.assert_called_once()
            confirm.assert_not_called()
            install.reset_mock()
            ready.return_value = True
            self.assertEqual(mactex.mactex_action('offer', self.root, SPEC), 0)
            install.assert_not_called()
            confirm.assert_not_called()

    def test_noninteractive_never_opens_dialog_and_gui_defaults_to_later(self):
        with patch.object(sys.stdin, 'isatty', return_value=False), patch.object(mactex.subprocess, 'run') as run:
            self.assertFalse(mactex.confirm(self.root))
            run.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(sys.stdin, 'isatty', return_value=True), \
             patch.object(mactex.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '现在安装\n', '')) as run:
            self.assertTrue(mactex.confirm(self.root))
            self.assertIn('default button "稍后安装"', run.call_args.args[0][2])
            self.assertIn('texlive install', run.call_args.args[0][-1])
            run.return_value = subprocess.CompletedProcess([], 1, '', 'User canceled (-128)')
            self.assertFalse(mactex.confirm(self.root))

    def test_ssh_asks_in_terminal_and_enter_skips(self):
        with patch.dict(os.environ, SSH_CONNECTION='remote'), patch.object(sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', return_value='') as answer, patch.object(mactex.subprocess, 'run') as run:
            self.assertFalse(mactex.confirm(self.root))
            answer.return_value = 'yes'
            self.assertTrue(mactex.confirm(self.root))
            run.assert_not_called()

    def prepare_download(self, bad=False):
        payload = b'official package test fixture'
        checksum = hashlib.sha512(payload).hexdigest()
        metadata = subprocess.CompletedProcess([], 0, checksum + '  MacTeX.pkg\n', '')
        calls = []

        def command(args, **kwargs):
            calls.append(args)
            if args[0] == '/usr/bin/curl':
                self.assertIn('--continue-at', args)
                self.assertIn('--progress-bar', args)
                Path(args[args.index('--output') + 1]).write_bytes(b'corrupt' if bad else payload)
            return 0

        return metadata, calls, command

    def test_pkg_install_verifies_before_admin_and_reuses_cached_download(self):
        metadata, calls, command = self.prepare_download()
        with patch.object(mactex.subprocess, 'run', return_value=metadata), \
             patch.object(mactex, 'stream_command', side_effect=command), \
             patch.object(mactex, 'installed', return_value=True):
            self.assertEqual(mactex.install_package(self.root, SPEC), 0)
            self.assertEqual([args[0] for args in calls], ['/usr/bin/curl', '/usr/sbin/pkgutil',
                                                        '/usr/sbin/spctl', '/usr/bin/sudo'])
            self.assertIn('/usr/sbin/installer', calls[-1])
            self.assertIn('-verboseR', calls[-1])
            record = json.loads((self.root / 'data/workbench/mactex.json').read_text())
            self.assertEqual(record['backend'], 'official-pkg')
            calls.clear()
            self.assertEqual(mactex.install_package(self.root, SPEC), 0)
            self.assertNotIn('/usr/bin/curl', [args[0] for args in calls])
            calls.clear()
            self.assertEqual(mactex.install_package(self.root, SPEC, update=True), 0)
            self.assertEqual(calls, [])

    def test_bad_checksum_prevents_signature_and_privileged_install(self):
        metadata, calls, command = self.prepare_download(bad=True)
        with patch.object(mactex.subprocess, 'run', return_value=metadata), \
             patch.object(mactex, 'stream_command', side_effect=command):
            with self.assertRaisesRegex(RuntimeError, 'SHA512 不匹配'):
                mactex.install_package(self.root, SPEC)
        self.assertEqual(len(calls), 1)
        self.assertFalse(list(self.root.rglob('*.pkg.part')))
        self.assertFalse((self.root / 'data/workbench/mactex.json').exists())

    def test_failed_signature_prevents_admin_and_keeps_verified_download(self):
        metadata, calls, command = self.prepare_download()

        def fail_signature(args, **kwargs):
            command(args, **kwargs)
            return 4 if args[0] == '/usr/sbin/pkgutil' else 0

        with patch.object(mactex.subprocess, 'run', return_value=metadata), \
             patch.object(mactex, 'stream_command', side_effect=fail_signature):
            self.assertEqual(mactex.install_package(self.root, SPEC), 4)
        self.assertEqual([args[0] for args in calls], ['/usr/bin/curl', '/usr/sbin/pkgutil'])
        self.assertTrue(list(self.root.rglob('*.pkg')))

    def test_download_failure_keeps_partial_for_next_attempt(self):
        metadata, calls, command = self.prepare_download()

        def fail_download(args, **kwargs):
            command(args, **kwargs)
            return 28

        with patch.object(mactex.subprocess, 'run', return_value=metadata), \
             patch.object(mactex, 'stream_command', side_effect=fail_download):
            self.assertEqual(mactex.install_package(self.root, SPEC), 28)
        self.assertEqual(len(calls), 1)
        partial = next(self.root.rglob('*.pkg.part'))
        self.assertTrue(partial.is_file())
        self.assertIn('重跑可续传', self.output.getvalue())
        self.assertFalse((self.root / 'data/workbench/mactex.json').exists())

    def test_bad_metadata_never_starts_download(self):
        result = subprocess.CompletedProcess([], 0, '<html>upstream error</html>', '')
        with patch.object(mactex.subprocess, 'run', return_value=result), \
             patch.object(mactex, 'stream_command') as run:
            with self.assertRaisesRegex(RuntimeError, '校验文件格式无效'):
                mactex.install_package(self.root, SPEC)
            run.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_gatekeeper_failure_never_requests_admin_install(self):
        metadata, calls, command = self.prepare_download()

        def fail_gatekeeper(args, **kwargs):
            command(args, **kwargs)
            return 3 if args[0] == '/usr/sbin/spctl' else 0

        with patch.object(mactex.subprocess, 'run', return_value=metadata), \
             patch.object(mactex, 'stream_command', side_effect=fail_gatekeeper):
            self.assertEqual(mactex.install_package(self.root, SPEC), 3)
        self.assertNotIn('/usr/bin/sudo', [args[0] for args in calls])


if __name__ == '__main__':
    unittest.main()
