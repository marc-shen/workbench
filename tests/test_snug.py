import io
from contextlib import nullcontext
import json
from pathlib import Path
import plistlib
import shutil
import subprocess as sp
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'libexec'), str(REPO / 'lib')]
import snug
from workbench.cli import workflow
from workbench.engine import plan
from workbench.privilege import needs_admin
from workbench.registry import discover


def release():
    return dict(tag_name='v2.9.1', assets=[dict(name='Snug-2.9.1.dmg', digest='sha256:' + 'a' * 64,
        browser_download_url='https://github.com/zephyr4123/snug/releases/download/v2.9.1/Snug-2.9.1.dmg')])


class SnugTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='snug test ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / 'Applications/Snug.app'
        self.app.parent.mkdir()
        for context in (patch.object(snug, 'APP', self.app), patch('sys.stdout', io.StringIO())):
            context.start(); self.addCleanup(context.stop)

    def bundle(self, app, version='2.9.1', identity=snug.BUNDLE_ID):
        executable = app / 'Contents/MacOS/Snug'
        executable.parent.mkdir(parents=True)
        executable.write_text('test executable'); executable.chmod(0o755)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=identity,
            CFBundleExecutable='Snug', CFBundleShortVersionString=version)))

    def test_universal_asset_and_untrusted_metadata(self):
        asset, checksum = snug.select_asset(release())
        self.assertEqual(asset['name'], 'Snug-2.9.1.dmg')
        self.assertEqual(checksum, 'a' * 64)
        for field, value in [('digest', None), ('browser_download_url', 'https://example.com/Snug.dmg')]:
            data = release(); data['assets'][0][field] = value
            with self.assertRaises(RuntimeError): snug.select_asset(data)
        for data in (dict(release(), tag_name='../outside'), dict(release(), prerelease=True),
                     dict(release(), assets=[])):
            with self.assertRaises(RuntimeError): snug.select_asset(data)

    def test_only_supported_macs_can_install(self):
        for system, machine, version, accepted in [('Darwin', 'arm64', '14.0', True),
                ('Darwin', 'x86_64', '15.0', True), ('Darwin', 'arm64', '13.6', False),
                ('Linux', 'x86_64', '', False)]:
            with patch.object(snug.platform, 'system', return_value=system), \
                    patch.object(snug.platform, 'machine', return_value=machine), \
                    patch.object(snug.platform, 'mac_ver', return_value=(version, '', '')):
                if accepted: snug.compatible()
                else:
                    with self.assertRaises(RuntimeError): snug.compatible()

    def test_identity_and_signatures_checked_before_copy(self):
        self.bundle(self.app, identity='unrelated.app')
        with patch.object(snug, 'run') as run:
            with self.assertRaises(RuntimeError): snug.verify(self.app)
        run.assert_not_called()

    def test_existing_install_and_newer_version_do_not_download(self):
        self.bundle(self.app, '3.0.0')
        with patch.object(snug, 'compatible'), patch.object(snug, 'verify'), \
                patch.object(snug, 'latest', return_value=release()) as latest, patch.object(snug, 'download') as download:
            snug.install(self.root)
            latest.assert_not_called()
            snug.install(self.root, update=True)
        latest.assert_called_once_with(snug.UPSTREAM, 'Snug'); download.assert_not_called()

    def test_running_app_is_not_replaced(self):
        with patch.object(snug, 'compatible'), patch.object(snug, 'latest', return_value=release()), \
                patch.object(snug.sp, 'run', return_value=Mock(returncode=0)), patch.object(snug, 'download') as download:
            with self.assertRaisesRegex(RuntimeError, '退出 Snug'): snug.install(self.root)
        download.assert_not_called()
        self.assertFalse((self.root / 'apps/snug/current').exists())

    def test_dmg_always_detached_after_verification_failure(self):
        with patch.object(snug, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'signature'):
                with snug.mounted(self.root / 'Snug.dmg'):
                    raise RuntimeError('signature')
        self.assertIn('attach', run.call_args_list[0].args[0])
        self.assertIn('detach', run.call_args_list[-1].args[0])

    def transaction_admin(self, command, **kwargs):
        # Emulate only file operations inside this test; never run sudo.
        if command[0] == '/usr/bin/mktemp':
            stage = Path(tempfile.mkdtemp(prefix='.workbench-snug.', dir=self.app.parent))
            return Mock(stdout=str(stage) + '\n')
        target = Path(command[-1])
        self.assertTrue(target.is_relative_to(self.root))
        if command[0] == '/usr/bin/ditto': shutil.copytree(command[1], target)
        elif command[0] == '/bin/mv': Path(command[1]).rename(target)
        elif command[0] == '/bin/rm': shutil.rmtree(target)

    def test_atomic_bundle_replacement_and_rollback(self):
        source = self.root / 'source/Snug.app'; self.bundle(source)
        self.bundle(self.app, '2.8.0')
        def fail_installed(app, expected):
            if app == self.app: raise RuntimeError('installed verification failed')
        with patch.object(snug, 'admin', side_effect=self.transaction_admin), \
                patch.object(snug, 'verify', side_effect=fail_installed):
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                snug.replace_app(source, '2.9.1')
        self.assertEqual(snug.app_version(self.app), '2.8.0')
        self.assertEqual(list(self.app.parent.glob('.workbench-snug.*')), [])
        with patch.object(snug, 'admin', side_effect=self.transaction_admin), patch.object(snug, 'verify'):
            snug.replace_app(source, '2.9.1')
        self.assertEqual(snug.app_version(self.app), '2.9.1')

    def test_failure_to_restore_retains_old_app(self):
        source = self.root / 'source/Snug.app'; self.bundle(source)
        self.bundle(self.app, '2.8.0')
        def admin(command, **kwargs):
            if command[0] == '/bin/mv' and Path(command[1]).name == 'previous.app':
                raise RuntimeError('rollback failed')
            return self.transaction_admin(command, **kwargs)
        def verify(app, expected):
            if app == self.app: raise RuntimeError('bad installed copy')
        with patch.object(snug, 'admin', side_effect=admin), patch.object(snug, 'verify', side_effect=verify), \
                patch('sys.stderr', io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'rollback failed'): snug.replace_app(source, '2.9.1')
        previous = next(self.app.parent.glob('.workbench-snug.*/previous.app'))
        self.assertEqual(snug.app_version(previous), '2.8.0')

    def test_success_records_only_verified_install(self):
        source = self.root / 'source/Snug.app'; self.bundle(source)
        with patch.object(snug, 'compatible'), patch.object(snug, 'latest', return_value=release()), \
                patch.object(snug.sp, 'run', return_value=Mock(returncode=1)), patch.object(snug, 'download'), \
                patch.object(snug, 'mounted', return_value=nullcontext(source)), patch.object(snug, 'verify'), \
                patch.object(snug, 'replace_app') as replace:
            snug.install(self.root)
        replace.assert_called_once_with(source, '2.9.1')
        record = json.loads((self.root / 'apps/snug/current/receipt.json').read_text())
        self.assertEqual(record['version'], '2.9.1')
        self.assertEqual(record['sha256'], 'a' * 64)

    def test_rejected_signature_does_not_replace_app_or_record_success(self):
        source = self.root / 'source/Snug.app'; self.bundle(source)
        with patch.object(snug, 'compatible'), patch.object(snug, 'latest', return_value=release()), \
                patch.object(snug.sp, 'run', return_value=Mock(returncode=1)), patch.object(snug, 'download'), \
                patch.object(snug, 'mounted', return_value=nullcontext(source)), \
                patch.object(snug, 'verify', side_effect=sp.CalledProcessError(1, ['codesign'])), \
                patch.object(snug, 'replace_app') as replace:
            with self.assertRaises(sp.CalledProcessError): snug.install(self.root)
        replace.assert_not_called()
        self.assertFalse((self.root / 'apps/snug').exists())

    def test_all_full_profiles_include_snug_only_on_macos(self):
        components = discover(REPO, self.root)
        for host in ('macos', 'steamdeck', 'fedora', 'ubuntu', 'debian'):
            profiles = ['workstation', *(['macos'] if host == 'macos' else ['scientific']),
                        *(['steamdeck'] if host == 'steamdeck' else [])]
            for profile in profiles:
                for action in ('install', 'update', 'check'):
                    steps = workflow(components, action, [profile], REPO, self.root, host)
                    operation = 'update' if action == 'update' else action
                    count = sum(s['component'] == 'snug' and s['action'] == operation for s in steps)
                    self.assertEqual(count, int(host == 'macos'), (host, profile, action))
        for operation in ('install', 'update'):
            self.assertTrue(needs_admin(plan(components, 'snug', operation, [], REPO, self.root, 'macos')))
        with self.assertRaises(RuntimeError): plan(components, 'snug', 'install', [], REPO, self.root, 'fedora')


if __name__ == '__main__': unittest.main()
