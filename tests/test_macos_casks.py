"""Native apps and Homebrew installation receipts are independent state."""
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from workbench import brew, builtin, provision


class CaskTests(unittest.TestCase):
    def result(self, installed=None, name='wezterm'):
        return subprocess.CompletedProcess([], 0, json.dumps({
            'casks': [{'token': name, 'installed': installed}]}), '')

    def test_update_preserves_unmanaged_app_and_reports_skip(self):
        output = io.StringIO()
        with patch.object(brew.subprocess, 'run', return_value=self.result()), \
                patch.object(brew, 'stream_command') as install, patch('sys.stdout', output):
            self.assertEqual(brew.ensure_cask('wezterm', 'update', available=True), 0)
            install.assert_not_called()
        self.assertIn('[SKIP]', output.getvalue())
        self.assertIn('未检查或安装更新', output.getvalue())

    def test_update_installs_missing_app_and_upgrades_managed_app(self):
        for installed, operation in [(None, 'install'), ('1.0', 'upgrade')]:
            with self.subTest(installed=installed), \
                    patch.object(brew.subprocess, 'run', return_value=self.result(installed)), \
                    patch.object(brew, 'stream_command', return_value=0) as run:
                self.assertEqual(brew.ensure_cask('wezterm', 'update'), 0)
                self.assertEqual(run.call_args.args[0], ['brew', operation, '--cask', 'wezterm'])

    def test_query_failure_does_not_trigger_install_or_claim_success(self):
        with patch.object(brew.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7, '', 'offline')), \
                patch.object(brew, 'stream_command') as run, patch('sys.stdout', io.StringIO()):
            self.assertEqual(brew.ensure_cask('wezterm', 'update', available=True), 7)
            run.assert_not_called()

    def test_reinstall_does_not_overwrite_unmanaged_app(self):
        with patch.object(brew.subprocess, 'run', return_value=self.result()), \
                patch.object(brew, 'stream_command') as run:
            with self.assertRaisesRegex(RuntimeError, '不由 Homebrew 管理'):
                brew.ensure_cask('wezterm', 'install', available=True, reinstall=True)
            run.assert_not_called()

    def test_managed_reinstall_and_upgrade_errors_propagate(self):
        with patch.object(brew.subprocess, 'run', return_value=self.result('1.0')), \
                patch.object(brew, 'stream_command', return_value=9) as run:
            self.assertEqual(brew.ensure_cask('wezterm', 'install', available=True, reinstall=True), 9)
            self.assertEqual(run.call_args.args[0], ['brew', 'reinstall', '--cask', 'wezterm'])

    def test_wezterm_and_emacs_update_use_receipt_aware_handler(self):
        with patch.object(builtin, 'detect', return_value='macos'), \
                patch.object(builtin.os, 'access', return_value=True), \
                patch.object(provision, 'native_installed', return_value=True), \
                patch.object(brew, 'ensure_cask', return_value=0) as run:
            self.assertEqual(builtin.main('wezterm', ['update']), 0)
            run.assert_called_with('wezterm', 'update', available=True, reinstall=False)
            self.assertEqual(builtin.main('emacs', ['update']), 0)
            run.assert_called_with('emacs-app', 'update', available=True)


if __name__ == '__main__':
    unittest.main()
