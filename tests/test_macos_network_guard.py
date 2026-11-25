import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import macos_network_guard as guard


class GuardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = self.root / 'config/mihomo/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text('')

    def test_reads_live_tun_instead_of_stale_yaml_and_ignores_proxy_environment(self):
        for enabled in (True, False):
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps({'tun': {'enable': enabled}}).encode())
            with patch.object(guard.urllib.request, 'build_opener', return_value=opener) as build:
                self.assertEqual(guard.tun_enabled(self.root), enabled)
            self.assertEqual(build.call_args.args[0].proxies, {})
            opener.open.assert_called_once_with('http://127.0.0.1:9090/configs', timeout=3)

    def test_unmanaged_mihomo_does_not_query_local_api(self):
        (self.root / 'config/mihomo/config.yaml').unlink()
        with patch.object(guard.urllib.request, 'build_opener') as build:
            guard.require_exit_compatible(self.root)
        build.assert_not_called()

    def test_unreachable_api_does_not_claim_loaded_service_tun_is_disabled(self):
        with patch.object(guard.urllib.request, 'build_opener', side_effect=OSError('unavailable')):
            with patch.object(guard.sp, 'run', return_value=Mock(returncode=0)):
                with self.assertRaisesRegex(RuntimeError, '无法确认'):
                    guard.require_exit_compatible(self.root)
            with patch.object(guard.sp, 'run', return_value=Mock(returncode=1)):
                guard.require_exit_compatible(self.root)

    def test_only_active_exit_node_needs_compatibility_check(self):
        for prefs, checked in [({'ExitNodeID': 'node'}, True), ({'ExitNodeIP': '100.64.0.1'}, True),
                               ({'ExitNodeID': ''}, False), ({'ExitNodeID': 'node', 'WantRunning': False}, False)]:
            with patch('tailscale_settings.preferences', return_value=prefs), \
                    patch.object(guard, 'require_exit_compatible') as require:
                guard.check(self.root, ['/fake/tailscale'])
            self.assertEqual(require.called, checked)

    def test_conflict_explains_subnet_alternative(self):
        with patch.object(guard, 'tun_enabled', return_value=True):
            with self.assertRaisesRegex(RuntimeError, '--accept-routes=true --exit-node=false'):
                guard.require_exit_compatible(self.root)


if __name__ == '__main__':
    unittest.main()
