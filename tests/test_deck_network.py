import copy
import json
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import subprocess
import tempfile
import sys
import os
import re


import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
SPEC = importlib.util.spec_from_file_location(
    'mihomo_deck', Path(__file__).resolve().parents[1] / 'libexec/mihomo_deck.py')
mihomo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mihomo)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import deck_network as network


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.provider = {
            'proxies': [{'name': 'example', 'type': 'ss'}],
            'proxy-groups': [{'name': 'select', 'type': 'select', 'proxies': ['example']}],
            'rules': ['DOMAIN,example.com,select', 'MATCH,select'],
            'dns': {'nameserver': ['https://dns.example/dns-query']},
        }

    def compose(self):
        return mihomo.compose(self.provider, Path('/home/deck/Software'), '100.100.1.4')

    def test_vendor_rules_groups_and_input_preserved(self):
        original = copy.deepcopy(self.provider)
        result = self.compose()
        self.assertEqual(self.provider, original)
        self.assertEqual(result['rules'][-2:], original['rules'])
        self.assertEqual(result['proxy-groups'], [dict(g, lazy=True) for g in original['proxy-groups']])
        self.assertEqual(result['proxies'], original['proxies'])
        self.assertEqual(result['dns']['nameserver'], original['dns']['nameserver'])

    def test_tailscale_and_302_bypass_vendor_catchall(self):
        result = self.compose()
        self.assertIn('IP-CIDR,100.64.0.0/10,DIRECT,no-resolve', result['rules'][:-2])
        self.assertIn('PROCESS-NAME,steamcommunity_302.caddy,DIRECT', result['rules'][:-2])
        self.assertIn('tailscale0', result['tun']['exclude-interface'])
        self.assertIn('100.64.0.0/10', result['tun']['route-exclude-address'])
        self.assertTrue(result['dns']['use-system-hosts'])
        self.assertEqual(result['dns']['nameserver-policy']['+.ts.net'], '100.100.100.100')

    def test_idle_health_checks_apply_to_groups_and_providers(self):
        self.provider['proxy-groups'][0]['lazy'] = False
        self.provider['proxy-providers'] = {'test': {'type': 'inline', 'payload': [],
            'health-check': {'enable': True, 'interval': 180, 'lazy': False}}}
        result = self.compose()
        self.assertTrue(result['proxy-groups'][0]['lazy'])
        self.assertEqual(result['proxy-providers']['test']['health-check'],
                         {'enable': True, 'interval': 180, 'lazy': True})

    def test_mirror_domains_bypass_vendor_rules_and_subscription_refresh(self):
        result = self.compose()
        for domain in ('tuna.tsinghua.edu.cn', 'mirrors.ustc.edu.cn', 'mirror.sjtu.edu.cn', 's3.jcloud.sjtu.edu.cn'):
            self.assertIn('DOMAIN-SUFFIX,' + domain + ',DIRECT', result['rules'][:-2])
        self.assertEqual(result['rules'][-2:], self.provider['rules'])

    def test_dispatcher_member_hosts_are_direct_without_bypassing_entire_universities(self):
        rules = self.compose()['rules'][:-2]
        patterns = [r.split(',')[1] for r in rules if r.startswith('DOMAIN-REGEX,')]
        self.assertIn('DOMAIN-SUFFIX,mirrors.cernet.edu.cn,DIRECT', rules)
        for host in ('cernet.mirrors.ustc.edu.cn', 'mirrors.zju.edu.cn', 'mirror.iscas.ac.cn', 'mirrors.bfsu.edu.cn'):
            self.assertTrue(any(re.fullmatch(pattern, host) for pattern in patterns), host)
        for host in ('www.tsinghua.edu.cn', 'mail.zju.edu.cn', 'mirrors.zju.edu.cn.example.com'):
            self.assertFalse(any(re.fullmatch(pattern, host) for pattern in patterns), host)

    def test_provider_cannot_open_extra_listeners(self):
        self.provider.update({'listeners': [{'port': 9999}], 'external-controller-tls': ':9998',
                              'external-ui-url': 'https://example.invalid/', 'allow-lan': True,
                              'external-controller': '0.0.0.0:9090', 'redir-port': 9997})
        result = self.compose()
        for key in ('listeners', 'external-controller-tls', 'external-ui-url', 'redir-port'):
            self.assertNotIn(key, result)
        self.assertFalse(result['allow-lan'])
        self.assertEqual(result['external-controller'], '0.0.0.0:9090')
        self.assertIn('http://127.0.0.1:9090', result['external-controller-cors']['allow-origins'])
        self.assertIn('http://100.100.1.4:9090', result['external-controller-cors']['allow-origins'])

    def test_incomplete_subscription_rejected(self):
        with self.assertRaises(RuntimeError):
            mihomo.compose({'proxies': []}, Path('/tmp/example'), '127.0.0.1')


class DailyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'config/mihomo'
        self.folder.mkdir(parents=True)
        for name in ('config.yaml', 'provider.yaml'):
            (self.folder / name).write_bytes(b'previous config')
        patcher = mock.patch.object(mihomo, 'ensure_yaml', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        connectivity = mock.patch('mihomo_network.network_available', return_value=(True, 'uplink-connected'))
        connectivity.start()
        self.addCleanup(connectivity.stop)

    def test_offline_update_retains_cache_without_fetch_or_restart(self):
        with mock.patch('mihomo_network.network_available', return_value=(False, 'no-physical-uplink')), \
             mock.patch.object(mihomo, 'refresh_config') as refresh, \
             mock.patch.object(mihomo.sp, 'run') as run:
            mihomo.daily_update(self.root)
        refresh.assert_not_called()
        run.assert_not_called()
        self.assert_old_config()

    def changed_config(self, *args):
        for name in ('config.yaml', 'provider.yaml'):
            (self.folder / name).write_bytes(b'new config')
        return True

    def assert_old_config(self):
        for name in ('config.yaml', 'provider.yaml'):
            self.assertEqual((self.folder / name).read_bytes(), b'previous config')

    def test_fetch_or_validation_failure_keeps_service_running(self):
        with mock.patch.object(mihomo, 'refresh_config', side_effect=RuntimeError('invalid subscription')):
            with mock.patch.object(mihomo.sp, 'run', return_value=mock.Mock(returncode=0)) as run:
                with self.assertRaises(RuntimeError):
                    mihomo.daily_update(self.root)
        self.assert_old_config()
        self.assertEqual(run.call_count, 1)  # Only queried service status.

    def test_unchanged_subscription_does_not_restart(self):
        with mock.patch.object(mihomo, 'refresh_config', return_value=False):
            with mock.patch.object(mihomo.sp, 'run', return_value=mock.Mock(returncode=0)) as run:
                mihomo.daily_update(self.root)
        self.assertEqual(run.call_count, 1)

    def test_disabled_service_is_not_started(self):
        with mock.patch.object(mihomo, 'refresh_config', side_effect=self.changed_config):
            with mock.patch.object(mihomo.sp, 'run', return_value=mock.Mock(returncode=3)) as run:
                mihomo.daily_update(self.root)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((self.folder / 'config.yaml').read_bytes(), b'new config')

    def test_network_loss_after_update_waits_without_rolling_back(self):
        with mock.patch('mihomo_network.network_available', side_effect=[
                (True, 'uplink-connected'), (False, 'no-physical-uplink')]), \
             mock.patch.object(mihomo, 'refresh_config', side_effect=self.changed_config), \
             mock.patch('network_diagnostics.wait_ready') as healthy_standby, \
             mock.patch.object(mihomo.sp, 'run', return_value=mock.Mock(returncode=0)) as run:
            mihomo.daily_update(self.root)
        self.assertEqual(run.call_count, 2)  # Status and one restart, no recovery loop.
        healthy_standby.assert_called_once()
        self.assertEqual((self.folder / 'config.yaml').read_bytes(), b'new config')

    def test_failed_restart_restores_both_files_and_recovers(self):
        responses = [mock.Mock(returncode=0), subprocess.CalledProcessError(1, ['systemctl']), mock.Mock(returncode=0)]
        with mock.patch.object(mihomo, 'refresh_config', side_effect=self.changed_config):
            with mock.patch.object(mihomo.sp, 'run', side_effect=responses) as run:
                with self.assertRaisesRegex(RuntimeError, 'previous configuration restored'):
                    mihomo.daily_update(self.root)
        self.assert_old_config()
        self.assertEqual(run.call_count, 3)


class UnifiedCommandTests(unittest.TestCase):
    def test_same_actions_for_every_target(self):
        parser = network.build_parser()
        for target in ('all',) + network.SERVICES:
            for action in network.ACTIONS:
                args = parser.parse_args([target, action])
                self.assertEqual((args.service, args.action), (target, action))

    def test_mihomo_update_does_not_touch_other_clients_or_desktops(self):
        with mock.patch.object(network, 'is_steamdeck', return_value=True), \
             mock.patch.object(network.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(network.os, 'geteuid', return_value=1000), \
             mock.patch.object(network, 'dispatch', return_value=0) as dispatch, \
             mock.patch.object(network, 'deploy') as deploy:
            self.assertEqual(network.main(['--prefix', '/tmp/software', 'mihomo', 'update']), 0)
        dispatch.assert_called_once_with(Path('/tmp/software'), 'mihomo', 'update')
        deploy.assert_not_called()

    def test_ssh_batch_rejected_before_any_service_changes(self):
        with mock.patch.dict(os.environ, {'SSH_CONNECTION': 'test'}), \
             mock.patch.object(network, 'is_steamdeck', return_value=True), \
             mock.patch.object(network.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(network.os, 'geteuid', return_value=1000), \
             mock.patch.object(network, 'dispatch') as dispatch, \
             mock.patch.object(network, 'deploy') as deploy:
            for action in ('update', 'stop', 'restart', 'activate'):
                with self.assertRaisesRegex(RuntimeError, 'local Konsole'):
                    network.main(['all', action])
        dispatch.assert_not_called()
        deploy.assert_not_called()

    def test_all_checks_report_each_service_even_if_one_fails(self):
        with mock.patch.object(network, 'is_steamdeck', return_value=True), \
             mock.patch.object(network.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(network.os, 'geteuid', return_value=1000), \
             mock.patch.object(network, 'dispatch', side_effect=[2, 0, 0]) as dispatch:
            self.assertEqual(network.main(['all', 'check']), 2)
        self.assertEqual([call.args[1] for call in dispatch.call_args_list], list(network.SERVICES))

    def test_launchers_and_timer_use_only_unified_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Software'
            with mock.patch.object(Path, 'home', return_value=Path(tmp)), \
                 mock.patch.object(network, 'link'), \
                 mock.patch.object(mihomo.sp, 'run'):
                network.deploy(root)
                mihomo.install_update_timer(root)
            desktop = Path(tmp) / '.local/share/applications'
            self.assertIn('workbench" mihomo open', (desktop / 'mihomo-web.desktop').read_text())
            self.assertIn('workbench" steamcommunity302 open', (desktop / 'Steamcommunity_302.desktop').read_text())
            unit = Path(tmp) / '.config/systemd/user/mihomo-subscription-update.service'
            self.assertIn('workbench" mihomo update', unit.read_text())
            self.assertFalse((root / 'config/network-launchers/steamcommunity302').exists())
            for name in ('mihomo-deck', 'steamcommunity302', 'deck-network'):
                p = root / 'bin' / name
                p.parent.mkdir(exist_ok=True)
                p.write_text('legacy')
            network.retire_legacy(root, network.SERVICES)
            self.assertFalse((root / 'bin/mihomo-deck').exists())
            self.assertFalse((root / 'bin/steamcommunity302').exists())
            self.assertFalse((root / 'bin/deck-network').exists())


class ControllerReadinessTests(unittest.TestCase):
    def test_standby_needs_real_api_and_online_accepts_intentionally_disabled_tun(self):
        import network_diagnostics as diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.json'
            with mock.patch('mihomo_network.state_path', return_value=state), \
                 mock.patch.object(diagnostics, 'API') as api, \
                 mock.patch.object(Path, 'exists', return_value=False):
                state.write_text(json.dumps({'phase': 'offline'}))
                api.return_value.request.side_effect = OSError('not listening')
                with self.assertRaises(OSError): diagnostics.controller_ready()
                api.return_value.request.side_effect = None
                api.return_value.request.return_value = {'mode': 'direct', 'tun': {'enable': False}}
                self.assertTrue(diagnostics.controller_ready())
                state.write_text(json.dumps({'phase': 'online', 'desired': {'mode': 'rule', 'tun': {'enable': False}}}))
                api.return_value.request.return_value = {'mode': 'rule', 'tun': {'enable': False}}
                self.assertTrue(diagnostics.controller_ready())
                state.write_text(json.dumps({'phase': 'checking', 'reason': 'uplink-settling'}))
                self.assertFalse(diagnostics.controller_ready())


class Official302OwnershipTests(unittest.TestCase):
    def test_apply_delegates_to_the_official_gui(self):
        with mock.patch.object(network, 'run') as run:
            network.dispatch(Path('/test/Software'), 'sc302', 'activate')
        run.assert_called_once_with([Path('/test/Software/apps/steamcommunity302/current/Steamcommunity302.AppImage')])

    def test_direct_service_switches_never_stop_the_control_daemon(self):
        with mock.patch.object(network, 'run') as run:
            for action in ('start', 'stop', 'restart'):
                with self.assertRaisesRegex(RuntimeError, 'official GUI'):
                    network.dispatch(Path('/test/Software'), 'sc302', action)
        run.assert_not_called()

    def test_enabled_official_control_daemon_is_healthy(self):
        with mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(network.sp, 'run', return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(network.service_pending(Path('/test/Software'), 'sc302'), [])
        self.assertEqual(run.call_args_list, [mock.call(
            ['systemctl', 'is-active', '--quiet', network.service_unit('sc302')],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)])

    def test_bulk_service_switches_leave_302_to_its_gui(self):
        with mock.patch.object(network, 'is_steamdeck', return_value=True), \
             mock.patch.object(network.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(network.os, 'geteuid', return_value=1000), \
             mock.patch.object(network, 'preflight'), \
             mock.patch.object(network, 'dispatch', return_value=0) as dispatch:
            for action in ('start', 'stop', 'restart'):
                dispatch.reset_mock()
                self.assertEqual(network.main(['all', action]), 0)
                self.assertEqual({call.args[1] for call in dispatch.call_args_list}, {'mihomo', 'tailscale'})


if __name__ == '__main__':
    unittest.main()
