import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import mihomo_network as network
import network_link as link
from mihomo_runtime import Runtime, standby_config


class LinkTests(unittest.TestCase):
    def test_virtual_interfaces_do_not_count_as_uplinks(self):
        with mock.patch.object(link, 'nmcli', return_value=
                'tailscale0:tun:connected (externally)\nmihomo-tun:tun:connected (externally)\n'
                'lo:loopback:connected (externally)\nwlan0:wifi:disconnected'):
            self.assertEqual(link.snapshot().state, 'offline')

    def test_physical_connectivity_and_fingerprint(self):
        fingerprints = []
        for connectivity, expected in [('4 (full)', 'ready'), ('2 (portal)', 'portal'), ('3 (limited)', 'checking')]:
            with mock.patch.object(link, 'nmcli', side_effect=[
                    'wlan0:wifi:connected\ntailscale0:tun:connected (externally)',
                    'GENERAL.CON-UUID:test\nIP4.GATEWAY:192.168.1.1\nIP4.DNS[1]:192.168.1.1', connectivity]):
                result = link.snapshot()
                self.assertEqual(result.state, expected)
                fingerprints.append(result.identity)
        self.assertEqual(len(set(fingerprints)), 1)
        self.assertTrue(fingerprints[0])

    def test_lan_without_gateway_and_unknown(self):
        with mock.patch.object(link, 'nmcli', side_effect=['eth0:ethernet:connected', 'IP4.GATEWAY:--']):
            self.assertEqual(link.snapshot().state, 'offline')
        with mock.patch.object(link, 'nmcli', side_effect=FileNotFoundError()):
            self.assertEqual(link.snapshot().state, 'unknown')

    def test_debounce_and_unknown_reset(self):
        gate = network.StableLink(10)
        for value, now, expected in [(True, 0, None), (True, 9, None), (True, 10, True),
                                     (False, 11, None), (True, 12, None), (True, 22, True),
                                     (False, 23, None), (None, 30, None), (False, 31, None),
                                     (False, 41, False)]:
            self.assertIs(gate.update(value, now), expected)


class MemoryAPI:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.calls = []

    def request(self, method, path, data=None, **kwargs):
        self.calls.append((method, path, data))
        if method == 'GET':
            return copy.deepcopy(self.config)
        if method == 'PUT':
            self.config = json.loads(data['payload'])


class RuntimeTests(unittest.TestCase):
    def test_standby_blocks_automatic_work_and_restores_intent(self):
        config = {'mode': 'rule', 'tun': {'enable': True}, 'geo-auto-update': True,
                  'proxy-groups': [{'name': 'test', 'type': 'fallback', 'proxies': ['a'], 'url': 'https://test', 'interval': 1}],
                  'proxy-providers': {'p': {'type': 'http', 'path': 'missing.yaml', 'url': 'https://private'}}}
        original = copy.deepcopy(config)
        with tempfile.TemporaryDirectory() as tmp:
            api = MemoryAPI(standby_config(config, Path(tmp)))
            runtime = Runtime(config, tmp, api)
            self.assertEqual(api.config['proxy-groups'][0]['type'], 'select')
            self.assertNotIn('url', api.config['proxy-groups'][0])
            self.assertEqual(api.config['proxy-providers']['p']['type'], 'inline')
            self.assertNotIn('url', api.config['proxy-providers']['p'])
            self.assertFalse(api.config['geo-auto-update'])
            runtime.observe_manual()
            runtime.activate()
            self.assertEqual(api.config, config)
            api.config['mode'] = 'global'
            api.config['tun']['enable'] = False
            runtime.observe_manual()
            runtime.standby()
            runtime.activate()
            self.assertEqual(runtime.current(), {'mode': 'global', 'tun': {'enable': False}})
            runtime.standby()
            restarted = Runtime(config, tmp, api)
            restarted.activate()
            self.assertEqual(restarted.current(), {'mode': 'global', 'tun': {'enable': False}})
            self.assertEqual(config, original)

    def test_offline_dashboard_intent_does_not_enable_tun_before_authentication(self):
        config = {'mode': 'rule', 'tun': {'enable': True}}
        with tempfile.TemporaryDirectory() as tmp:
            api = MemoryAPI(standby_config(config, Path(tmp)))
            runtime = Runtime(config, tmp, api)
            api.config['mode'] = 'global'
            runtime.observe_manual()
            self.assertFalse(runtime.online)
            self.assertEqual(runtime.current(), {'mode': 'direct', 'tun': {'enable': False}})
            runtime.activate()
            self.assertEqual(runtime.current(), {'mode': 'global', 'tun': {'enable': True}})

    def test_partial_activation_failure_returns_to_standby(self):
        config = {'mode': 'rule', 'tun': {'enable': True}}
        with tempfile.TemporaryDirectory() as tmp:
            api = MemoryAPI(standby_config(config, Path(tmp)))
            runtime = Runtime(config, tmp, api)
            load = runtime.load
            def fail(config):
                load(config)
                if config['tun']['enable']:
                    raise RuntimeError('TUN failed')
            with mock.patch.object(runtime, 'load', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    runtime.activate()
            self.assertFalse(runtime.online)
            self.assertFalse(runtime.current()['tun']['enable'])


class SupervisorTests(unittest.TestCase):
    def scenario(self, sequence, checks=(), resume_at=None):
        stop = threading.Event()
        children, states, online_states, index, clock = [], [], [], [0], [100.0]
        underlay = mock.Mock(side_effect=checks)
        class Events:
            changed = threading.Event()
            resumed = False
            def start(self): pass
            def close(self): pass
            def wait(self, timeout):
                states.append(json.loads(state.read_text())['phase'])
                online_states.append(runtime.online)
                index[0] += 1
                clock[0] += 35
                self.resumed = index[0] == resume_at
                if index[0] == len(sequence): stop.set()
        def spawn(command):
            child = subprocess.Popen(command)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.json'
            config = {'mode': 'rule', 'tun': {'enable': True}}
            runtime = Runtime(config, tmp, MemoryAPI(standby_config(config, Path(tmp))))
            with mock.patch.object(network.time, 'monotonic', side_effect=lambda: clock[0]), \
                 mock.patch.object(network.time, 'clock_gettime', create=True, side_effect=lambda _: clock[0] + 10):
                result = network.supervise([sys.executable, '-c', 'import time; time.sleep(60)'], runtime,
                    state_file=state, probe=lambda: sequence[index[0]], underlay=underlay, stop=stop,
                    events=Events(), watchdog=.01, settle=0, disconnect=0, spawn=spawn)
            self.assertEqual(result, 0)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
        return states, online_states, underlay

    def test_cold_boot_offline_and_reconnect_share_one_core(self):
        states, _, checks = self.scenario([link.Link('offline'), link.Link('ready', 'wifi'),
            link.Link('offline'), link.Link('ready', 'wifi')])
        self.assertEqual(states, ['offline', 'online', 'offline', 'online'])
        checks.assert_not_called()

    def test_portal_can_recover_before_nm_cached_status_updates(self):
        states, online, _ = self.scenario([link.Link('portal', 'wifi')] * 4,
                                         checks=['portal', 'ready'])
        self.assertEqual(states, ['portal', 'online', 'online', 'online'])
        self.assertEqual(online, [False, True, True, True])

    def test_limited_new_wifi_waits_but_existing_network_does_not_flap(self):
        states, _, checks = self.scenario([link.Link('checking', 'wifi'), link.Link('ready', 'wifi'),
            link.Link('checking', 'wifi'), link.Link('checking', 'new-wifi')], checks=['limited', 'limited'])
        self.assertEqual(states, ['limited', 'online', 'online', 'limited'])
        self.assertEqual(checks.call_count, 2)

    def test_resume_rechecks_native_connectivity_before_restoring_tun(self):
        states, _, checks = self.scenario([link.Link('ready', 'wifi'), link.Link('checking', 'wifi')],
                                         checks=['portal'], resume_at=1)
        self.assertEqual(states, ['online', 'portal'])
        checks.assert_called_once()

    def test_core_crash_reports_failure_without_internal_restart(self):
        child = mock.Mock(pid=42, returncode=1)
        child.poll.return_value = 1
        events = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.json'
            runtime = Runtime({'mode': 'rule', 'tun': {'enable': False}}, tmp)
            result = network.supervise(['unused'], runtime, state_file=state,
                events=events, spawn=lambda command: child)
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(state.read_text())['phase'], 'failed')


if __name__ == '__main__':
    unittest.main()
