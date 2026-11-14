import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from mihomo_health import GroupHealth, fresh_failure
from steam_routing import configure, STEAM_GROUP, STEAM_FALLBACK


def failed(now, url='https://steam.test'):
    return {'extra': {url: {'alive': False, 'history': [
        {'time': dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), 'delay': 0}]}}}


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {'proxies': [{'name': n} for n in ('a', 'b', 'c', 'unrelated')],
            'proxy-groups': [{'name': 'scoped', 'type': 'fallback', 'proxies': ['a', 'b', 'c'],
                             'url': 'https://steam.test', 'interval': 180},
                            {'name': 'games', 'type': 'select', 'proxies': ['scoped', 'REJECT']}]}
        self.proxies = {n: failed(1000) for n in ('a', 'b', 'c')}
        self.proxies['games'] = {'now': 'scoped'}
        def request(method, path, data=None):
            if path == '/proxies': return {'proxies': copy.deepcopy(self.proxies)}
            if method == 'PUT': self.proxies['games']['now'] = data['name']
            if path == '/connections': return {'connections': [
                {'id': 'affected', 'chains': ['scoped', 'games']},
                {'id': 'keep', 'chains': ['DIRECT']}]}
        self.api = mock.Mock()
        self.api.request.side_effect = request
        self.health = GroupHealth(self.api, self.config, self.tmp.name)
        self.health.allowed = True
        self.health.test = mock.Mock(return_value=False)

    def test_missing_stale_and_endpoint_mismatched_history_are_not_failures(self):
        for proxy in ({}, failed(1), failed(1000, 'https://other.test')):
            self.assertFalse(fresh_failure(proxy, 'https://steam.test', 1030, 360))
        self.assertTrue(fresh_failure(failed(1000), 'https://steam.test', 1030, 360))
        del self.proxies['b']
        self.health.tick(1000)
        self.health.tick(1031)
        self.assertEqual(self.proxies['games']['now'], 'scoped')
        self.health.test.assert_not_called()

    def test_second_endpoint_success_prevents_false_circuit_break(self):
        self.health.test.return_value = True
        self.health.tick(1000)
        self.health.tick(1031)
        self.assertEqual(self.proxies['games']['now'], 'scoped')
        self.assertFalse(self.health.records['games'].get('blocked'))

    def block(self):
        self.health.tick(1000)
        self.assertEqual(self.proxies['games']['now'], 'scoped')
        self.health.tick(1031)
        self.assertEqual(self.proxies['games']['now'], 'REJECT')

    def test_breaker_backoff_is_bounded_and_does_not_leave_scope(self):
        self.block()
        self.api.request.assert_any_call('DELETE', '/connections/affected')
        self.assertNotIn(mock.call('DELETE', '/connections/keep'), self.api.request.call_args_list)
        self.assertEqual(self.health.records['games']['next'], 1091)
        self.health.test.reset_mock()
        self.health.tick(1090)
        self.health.test.assert_not_called()
        self.health.tick(1091)
        self.assertEqual(self.health.test.call_count, 3)
        self.assertEqual(self.health.records['games']['next'], 1211)
        self.health.tick(1211)
        self.assertEqual(self.health.records['games']['next'], 1511)
        self.assertTrue(all(call.args[0] in ('a', 'b', 'c') for call in self.health.test.call_args_list))
        self.assertEqual(json.loads(self.health.file.read_text())['games']['group'], 'scoped')

    def test_recovery_requires_two_successes_and_respects_manual_selection(self):
        self.block()
        self.health.test.return_value = True
        self.health.tick(1091)
        self.assertEqual(self.proxies['games']['now'], 'REJECT')
        self.health.tick(1151)
        self.assertEqual(self.proxies['games']['now'], 'scoped')
        self.assertEqual(json.loads(self.health.file.read_text()), {})
        self.proxies['games']['now'] = 'scoped'
        self.health.test.return_value = False
        self.block()
        self.proxies['games']['now'] = 'manual-other-group'
        self.health.tick(1091)
        self.assertEqual(self.proxies['games']['now'], 'manual-other-group')
        self.assertNotIn('games', self.health.records)

    def test_disconnect_restores_intent_without_probes(self):
        self.block()
        self.health.test.reset_mock()
        self.health.pause()
        self.assertEqual(self.proxies['games']['now'], 'scoped')
        self.assertFalse(self.health.allowed)
        self.health.test.assert_not_called()


class SteamRoutingTests(unittest.TestCase):
    def test_steam_scope_is_small_separate_and_china_precedes_steam(self):
        config = {'proxies': [{'name': n} for n in ('hk1', 'hk2', 'hk3', 'hk4', 'hk5', 'jp1')],
            'proxy-groups': [{'name': '🎮 游戏平台', 'type': 'select', 'proxies': ['jp1']},
                             {'name': '🇭🇰 香港节点', 'type': 'select', 'proxies': ['hk1', 'hk2', 'hk3', 'hk4', 'hk5']} ]}
        original = copy.deepcopy(config)
        with tempfile.TemporaryDirectory() as tmp:
            rules = configure(config, Path(tmp))
        self.assertEqual(config['proxy-groups'][:2], original['proxy-groups'])
        fallback = next(g for g in config['proxy-groups'] if g['name'] == STEAM_FALLBACK)
        self.assertEqual(fallback['proxies'], ['hk1', 'hk2', 'hk3', 'hk4'])
        self.assertLess(rules.index('GEOSITE,steam@cn,DIRECT'), rules.index('GEOSITE,steam,' + STEAM_GROUP))
        self.assertEqual(rules[0], 'DOMAIN-SUFFIX,steamconnecttest.com,DIRECT')


if __name__ == '__main__':
    unittest.main()
