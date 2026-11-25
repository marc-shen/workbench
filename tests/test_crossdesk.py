import hashlib
import io
import json
import os
from pathlib import Path
import subprocess as sp
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'libexec'))
sys.path.insert(0, str(REPO / 'lib'))
import crossdesk as cd
from workbench.cli import load_profile, workflow
from workbench.registry import discover
from workbench.engine import plan
from workbench.privilege import needs_admin


def release():
    tag = 'v1.7.7-20261124'
    names = [f'crossdesk-{p}-{a}-{tag}.{ext}' for p, a, ext in (
        ('macos', 'arm64', 'pkg'), ('macos', 'x64', 'pkg'),
        ('linux', 'amd64', 'deb'), ('linux', 'arm64', 'deb'))]
    names.insert(0, f'crossdesk-virtual-desktop-xfce-linux-amd64-{tag}.deb')
    return dict(tag_name=tag, assets=[dict(name=n, digest='sha256:' + 'a' * 64,
        browser_download_url=f'https://github.com/{cd.UPSTREAM}/releases/download/{tag}/{n}') for n in names])


def deb(path, entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:xz') as archive:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(content))
    payload = buffer.getvalue()
    header = f'{"data.tar.xz/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(payload):<10}`\n'.encode()
    path.write_bytes(b'!<arch>\n' + header + payload + (b'\n' if len(payload) % 2 else b''))


class CrossDeskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crossdesk test ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'Software with spaces'
        self.root.mkdir()

    def test_selects_standard_official_package_for_each_platform_architecture(self):
        for host in ('macos', 'steamdeck', 'fedora', 'ubuntu', 'debian'):
            for arch in ('x86_64', 'arm64', 'aarch64'):
                asset, checksum = cd.select_asset(release(), host, arch)
                self.assertNotIn('virtual', asset['name'])
                self.assertTrue(asset['name'].endswith('.pkg' if host == 'macos' else '.deb'))
                self.assertIn('arm64' if arch != 'x86_64' else 'x64' if host == 'macos' else 'amd64', asset['name'])
                self.assertEqual(checksum, 'a' * 64)

    def test_invalid_asset_metadata_fails_closed(self):
        for key, value in [('digest', None), ('browser_download_url', 'https://example.com/test.pkg')]:
            data = release()
            data['assets'][1][key] = value
            with self.assertRaises(RuntimeError):
                cd.select_asset(data, 'macos', 'arm64')
        with self.assertRaises(RuntimeError):
            cd.select_asset(release(), 'ubuntu', 'riscv64')
        data = release(); data['tag_name'] = '../escape'
        with self.assertRaises(RuntimeError):
            cd.select_asset(data, 'macos', 'arm64')

    def test_latest_falls_back_to_authenticated_gh_without_using_stale_cache(self):
        results = [sp.CompletedProcess([], 22, '', 'HTTP 403'),
                   sp.CompletedProcess([], 0, json.dumps(release()), '')]
        with patch.object(cd.sp, 'run', side_effect=results) as run, patch.object(cd.shutil, 'which', return_value='/bin/gh'):
            self.assertEqual(cd.latest()['tag_name'], release()['tag_name'])
        self.assertEqual(run.call_args_list[1].args[0], ['gh', 'api', 'repos/kunkundi/crossdesk/releases/latest'])

    def test_failed_checksum_removes_partial_and_never_publishes_package(self):
        asset, expected = cd.select_asset(release(), 'macos', 'arm64')
        def download(command):
            Path(command[command.index('--output') + 1]).write_bytes(b'corrupt')
        with patch.object(cd, 'run', side_effect=download):
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                cd.download(self.root, asset, expected)
        self.assertEqual(list((self.root / 'downloads/crossdesk').iterdir()), [])

    def test_valid_download_cache_is_rehashed_without_network(self):
        asset, _ = cd.select_asset(release(), 'macos', 'arm64')
        package = self.root / 'downloads/crossdesk' / asset['name']
        package.parent.mkdir(parents=True); package.write_bytes(b'valid')
        with patch.object(cd, 'run') as run:
            self.assertEqual(cd.download(self.root, asset, hashlib.sha256(b'valid').hexdigest()), package)
        run.assert_not_called()

    def test_deb_extracts_real_ar_format_and_rejects_path_traversal(self):
        package = self.root / 'test.deb'
        deb(package, {'./usr/lib/crossdesk/crossdesk-bin': b'binary'})
        cd.extract_deb(package, self.root / 'payload')
        self.assertEqual((self.root / 'payload/usr/lib/crossdesk/crossdesk-bin').read_bytes(), b'binary')
        deb(package, {'../outside': b'bad'})
        with self.assertRaises(tarfile.FilterError):
            cd.extract_deb(package, self.root / 'payload')
        self.assertFalse((self.root / 'outside').exists())

    def test_install_skips_existing_and_update_checks_latest_without_downloading_same_version(self):
        with patch.object(cd, 'check', return_value=True), patch.object(cd, 'latest', return_value=release()) as latest, \
                patch.object(cd, 'version', return_value='1.7.7-20261124'), patch.object(cd, 'download') as download:
            cd.install(self.root, 'macos')
            latest.assert_not_called()
            cd.install(self.root, 'macos', update=True)
            latest.assert_called_once()
            download.assert_not_called()

    def test_pkg_verifies_before_terminal_sudo_and_records_only_success(self):
        package = self.root / 'CrossDesk.pkg'
        with patch.object(cd, 'check', side_effect=[False, True]), patch.object(cd, 'latest', return_value=release()), \
                patch.object(cd, 'download', return_value=package), patch.object(cd, 'run') as run, \
                patch.object(cd, 'version', return_value=release()['tag_name']):
            cd.install(self.root, 'macos')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:2], ['/usr/sbin/pkgutil', '--check-signature'])
        self.assertEqual(commands[1][:2], ['/usr/sbin/spctl', '--assess'])
        self.assertEqual(commands[2][:2], ['/usr/bin/sudo', '/usr/sbin/installer'])
        record = json.loads((self.root / 'apps/crossdesk/current/receipt.json').read_text())
        self.assertEqual(record['backend'], 'official-pkg')

    def test_native_install_failure_keeps_existing_receipt(self):
        old = self.root / 'apps/crossdesk/old'; old.mkdir(parents=True)
        (old / 'receipt.json').write_text('old')
        (old.parent / 'current').symlink_to('old')
        with patch.object(cd, 'check', return_value=False), patch.object(cd, 'latest', return_value=release()), \
                patch.object(cd, 'download', return_value=self.root / 'test.deb'), \
                patch.object(cd, 'run', side_effect=sp.CalledProcessError(1, ['apt-get'])):
            with self.assertRaises(sp.CalledProcessError):
                cd.install(self.root, 'ubuntu', update=True)
        self.assertEqual((old.parent / 'current/receipt.json').read_text(), 'old')

    def test_debian_installs_dependencies_without_large_optional_cuda_recommendation(self):
        with patch.object(cd, 'check', side_effect=[False, True]), patch.object(cd, 'latest', return_value=release()), \
                patch.object(cd, 'download', return_value=self.root / 'test.deb'), patch.object(cd, 'run') as run, \
                patch.object(cd, 'version', return_value=release()['tag_name']):
            cd.install(self.root, 'debian')
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['sudo', 'apt-get', 'install'])
        self.assertIn('--no-install-recommends', command)

    def test_successful_installer_with_stale_binary_does_not_write_receipt(self):
        with patch.object(cd, 'check', side_effect=[False, True]), patch.object(cd, 'latest', return_value=release()), \
                patch.object(cd, 'download', return_value=self.root / 'test.pkg'), patch.object(cd, 'run'), \
                patch.object(cd, 'version', return_value='1.0.0'):
            with self.assertRaisesRegex(RuntimeError, '实际版本'):
                cd.install(self.root, 'macos')
        self.assertFalse((self.root / 'apps').exists())

    def test_portable_dependency_failure_does_not_switch_current_or_create_launcher(self):
        old = self.root / 'apps/crossdesk/old'; old.mkdir(parents=True)
        (old.parent / 'current').symlink_to('old')
        app = old.parent / 'new'; app.mkdir()
        package = self.root / 'test.deb'
        deb(package, {'usr/lib/crossdesk/crossdesk-bin': b'binary'})
        with patch.object(cd, 'resolve', return_value=(self.root, [])), \
                patch.object(cd, 'install_environment', return_value=app), \
                patch.object(cd, 'verify_linux', side_effect=RuntimeError('missing library')):
            with self.assertRaisesRegex(RuntimeError, 'missing library'):
                cd.portable(self.root, package, 'v2', 'a' * 64)
        self.assertEqual((old.parent / 'current').resolve(), old)
        self.assertFalse((app / 'launch').exists())

    def test_portable_launcher_handles_spaces_and_uses_private_runtime(self):
        package = self.root / 'test.deb'
        program = b'#!/bin/sh\nprintf "%s\\n%s\\n" "$LD_LIBRARY_PATH" "$1"\n'
        deb(package, {'usr/lib/crossdesk/crossdesk-bin': program,
                      'usr/share/applications/crossdesk.desktop': b'[Desktop Entry]\nExec=/usr/bin/crossdesk\nIcon=crossdesk\n'})
        app = self.root / 'apps/crossdesk/new'; app.mkdir(parents=True)
        with patch.object(cd, 'resolve', return_value=(self.root, [])), \
                patch.object(cd, 'install_environment', return_value=app), patch.object(cd, 'verify_linux'):
            cd.portable(self.root, package, 'v1', 'a' * 64)
        result = sp.run([app / 'launch/crossdesk', 'argument with spaces'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [cd.runtime_env(app)['LD_LIBRARY_PATH'], 'argument with spaces'])
        with patch.dict(os.environ, XDG_DATA_HOME=str(self.root / 'desktop data')):
            cd.publish_portable(self.root, app)
        desktop = (self.root / 'desktop data/applications/crossdesk.desktop').read_text()
        self.assertIn('Exec="' + str(self.root / 'bin/crossdesk') + '"', desktop)
        self.assertTrue((self.root / 'bin/crossdesk').is_file())

    def test_missing_dynamic_library_is_an_error_even_if_ldd_exits_zero(self):
        with patch.object(cd.os, 'access', return_value=True), patch.object(cd.sp, 'run', return_value=sp.CompletedProcess([], 0, 'libexample.so => not found', '')):
            with self.assertRaisesRegex(RuntimeError, 'libexample'):
                cd.verify_linux(self.root)

    def test_list_and_missing_check_are_read_only(self):
        with patch.object(cd, 'MAC_APP', self.root / 'missing.app'), patch.object(cd, 'latest') as latest:
            self.assertFalse(cd.installed(self.root, 'macos'))
            self.assertFalse(cd.check(self.root, 'macos'))
        latest.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_all_workstation_platforms_include_install_check_and_update_with_correct_providers(self):
        profile = load_profile(REPO, 'workstation')
        for action in ('install', 'check'):
            self.assertIn('crossdesk', [step['component'] for step in profile[action]])
        components = discover(REPO, self.root)
        for host in ('macos', 'steamdeck', 'fedora', 'ubuntu', 'debian'):
            profiles = ['workstation', *(['macos'] if host == 'macos' else ['scientific']),
                        *(['steamdeck'] if host == 'steamdeck' else [])]
            for profile_name in profiles:
                for operation in ('install', 'update', 'check'):
                    steps = workflow(components, operation, [profile_name], REPO, self.root, host)
                    expected = 'update' if operation == 'update' else operation
                    self.assertEqual(sum(s['component'] == 'crossdesk' and s['action'] == expected for s in steps), 1)
            for action in ('install', 'update'):
                steps = plan(components, 'crossdesk', action, [], REPO, self.root, host)
                self.assertEqual(steps[-1]['component'], 'crossdesk')
                self.assertEqual(any(s['component'] == 'providers' for s in steps), host in ('steamdeck', 'fedora'))
                self.assertTrue(needs_admin(steps))


if __name__ == '__main__':
    unittest.main()
