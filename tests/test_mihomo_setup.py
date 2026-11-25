"""A fully installed dashboard must not depend on a subscription URL."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

REPO=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(REPO/'libexec'),str(REPO/'lib')]
import yaml
import mihomo_setup as setup
import macos_network as mac
import mihomo_deck as deck


class SetupFixture:
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.folder=self.root/'config/mihomo'
        setup.prepare_subscription_file(self.folder)
        self.provider={'proxies':[{'name':'node','type':'socks5','server':'example.com','port':1080}],
                       'proxy-groups':[{'name':'select','type':'select','proxies':['node']}], 'rules':['MATCH,select']}


class SetupTests(SetupFixture, unittest.TestCase):
    def test_pending_config_is_complete_and_never_intercepts_traffic(self):
        for builder in (mac.refresh,deck.refresh_config):
            with self.subTest(builder=builder.__name__), patch.object(mac.sp,'run',return_value=Mock(returncode=0)), \
                    patch.object(mac,'api',side_effect=OSError),patch('deck_network.tailscale_ip',return_value='100.100.3.4'):
                builder(self.root)
                config=yaml.safe_load((self.folder/'config.yaml').read_text())
                self.assertEqual(config['mode'],'direct')
                self.assertFalse(config['tun']['enable']);self.assertFalse(config['dns']['enable'])
                self.assertEqual(config['rules'],['MATCH,DIRECT'])
                self.assertTrue((self.folder/setup.PENDING).exists())
                self.assertFalse((self.folder/'provider.yaml').exists())
                self.assertFalse((self.folder/'subscriptions.json').exists())
                self.assertEqual(config['external-ui'],str(self.root/'apps/metacubexd/current'))

    def test_first_subscription_enables_mac_tun_only_after_successful_reload(self):
        old=setup.pending_config(self.root)
        mac.dump_yaml(self.folder/'config.yaml',old)
        setup.mark_subscription_state(self.folder,[])
        (self.folder/'subscription.url').write_text('https://example.invalid/sub')
        records=[{'url':'https://example.invalid/sub','document':self.provider}]
        current={'mode':'direct','tun':{'enable':False}}
        def api(root,method='GET',path='/configs',data=None):
            if method=='PUT':
                config=json.loads(data['payload']);current.update(mode=config['mode'],tun=config['tun'])
            return copy.deepcopy(current)
        with patch.object(mac,'load_optional',return_value=records), \
                patch.object(mac.sp,'run',return_value=Mock(returncode=0)),patch.object(mac,'api',side_effect=api):
            mac.refresh(self.root,True)
        self.assertEqual(current['mode'],'rule');self.assertTrue(current['tun']['enable'])
        self.assertFalse((self.folder/setup.PENDING).exists())

    def test_bad_subscription_or_existing_broken_source_never_becomes_pending(self):
        (self.folder/'subscription.url').write_text('bad URL')
        with self.assertRaises(RuntimeError):setup.load_optional(self.folder)
        (self.folder/'subscription.url').write_text('# empty')
        (self.folder/'config.yaml').write_text('existing')
        with self.assertRaisesRegex(RuntimeError,'no subscription source'):setup.load_optional(self.folder)

    def test_deck_pending_update_needs_no_network_or_service_restart(self):
        setup.mark_subscription_state(self.folder,[])
        with patch('mihomo_network.network_available') as online,patch.object(deck.sp,'run') as run:
            deck.daily_update(self.root)
            online.assert_not_called();run.assert_not_called()

    def test_deck_first_subscription_clears_pending_intent(self):
        setup.mark_subscription_state(self.folder,[])
        (self.folder/'network-intent.json').write_text('{"mode":"direct","tun":{"enable":false}}')
        with patch.object(deck,'load_optional',return_value=[{'url':None,'document':self.provider}]), \
                patch.object(deck.sp,'run',return_value=Mock(returncode=0)),patch('deck_network.tailscale_ip',return_value='100.100.3.4'):
            deck.refresh_config(self.root)
        self.assertFalse((self.folder/'network-intent.json').exists())
        self.assertFalse((self.folder/setup.PENDING).exists())

    def test_deck_prepare_without_url_writes_service_and_timer(self):
        home=self.root/'home';home.mkdir()
        for name,filename in [('mihomo','mihomo'),('metacubexd','index.html')]:
            app=self.root/'apps'/name/'test';app.mkdir(parents=True)
            (app/filename).touch()
        def link(source,target,root):target.symlink_to(source)
        with patch.object(deck,'ensure_yaml',return_value=True), \
                patch.object(deck,'read_toml',return_value={'install':{'assets':{
                    'mihomo':{'version':'test'},'metacubexd':{'version':'test'}}}}), \
                patch('deck_network.link',side_effect=link),patch.object(deck,'refresh_config') as refresh, \
                patch.object(Path,'home',return_value=home),patch.object(deck.sp,'run',return_value=Mock(returncode=0)):
            deck.prepare(self.root)
        refresh.assert_called_once_with(self.root)
        unit=(home/'.config/systemd/user/mihomo.service').read_text()
        self.assertIn('profile-mihomo-firewall.service',unit)
        self.assertIn('mihomo_network.py',unit)
        self.assertTrue((home/'.config/systemd/user/mihomo-subscription-update.timer').exists())


class TailnetTests(SetupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        binary=self.root/'Tailscale';binary.touch()
        context=patch.object(mac,'TSBIN',binary);context.start();self.addCleanup(context.stop)
        self.state={'TCP':{'443':{'HTTPS':True}},'Web':{'other.ts.net:443':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:8080'}}}}}
        self.status={'BackendState':'Running','TailscaleIPs':['100.100.3.4','fd7a:115c:a1e0::1234'],
                     'Self':{'DNSName':'mac.tailnet.ts.net.'}}
        mac.dump_yaml(self.folder/'config.yaml',setup.pending_config(self.root))

    def test_serves_only_tailnet_and_preserves_other_ports_and_runtime(self):
        commands=[];api_calls=[]
        def run(args,**kwargs):
            commands.append(list(map(str,args)))
            if args[1:]==['status','--json']:return Mock(stdout=json.dumps(self.status))
            if args[1:]==['serve','status','--json']:return Mock(stdout=json.dumps(self.state))
            self.assertEqual(args[1:],['serve','--bg','--yes','--tcp=9090','tcp://127.0.0.1:9090'])
            self.state['TCP']['9090']={'TCPForward':'127.0.0.1:9090'}
            return Mock(returncode=0)
        def api(root,method='GET',path='/configs',data=None):
            api_calls.append((method,data))
            return {'mode':'direct','tun':{'enable':False}}
        with patch.object(mac,'run',side_effect=run),patch.object(mac,'api',side_effect=api):
            self.assertTrue(mac.expose_dashboard(self.root))
            count=len(api_calls)
            self.assertTrue(mac.expose_dashboard(self.root))
            self.assertEqual(len(api_calls),count)
        self.assertEqual(self.state['TCP']['443'],{'HTTPS':True})
        config=yaml.safe_load((self.folder/'config.yaml').read_text())
        self.assertFalse(config['tun']['enable'])
        self.assertIn('http://100.100.3.4:9090',config['external-controller-cors']['allow-origins'])
        self.assertIn('http://[fd7a:115c:a1e0::1234]:9090',config['external-controller-cors']['allow-origins'])
        self.assertFalse(any('funnel' in args or 'reset' in args for args in commands))

    def test_existing_port_conflict_is_not_overwritten(self):
        self.state['TCP']['9090']={'TCPForward':'127.0.0.1:8080'}
        with patch.object(mac,'run',return_value=Mock(stdout=json.dumps(self.status))) as run, \
                patch.object(mac,'serve_state',return_value=self.state),patch.object(mac,'api') as api:
            with self.assertRaisesRegex(RuntimeError,'already used'):mac.expose_dashboard(self.root)
            api.assert_not_called();self.assertEqual(run.call_count,1)

    def test_login_pending_does_not_change_controller_or_start_tailscale(self):
        self.status['BackendState']='NeedsLogin'
        before=(self.folder/'config.yaml').read_bytes()
        with patch.object(mac,'run',return_value=Mock(stdout=json.dumps(self.status))) as run:
            self.assertFalse(mac.expose_dashboard(self.root));self.assertEqual(run.call_count,1)
        self.assertEqual((self.folder/'config.yaml').read_bytes(),before)

    def test_unavailable_tailscale_waits_for_system_approval(self):
        with patch.object(mac,'run',side_effect=subprocess.CalledProcessError(1,['Tailscale','status'])) as run:
            self.assertFalse(mac.expose_dashboard(self.root))
            self.assertEqual(run.call_count,1)

    def test_forwarding_failure_does_not_reinstall_running_daemon(self):
        system=self.root/'system';system.mkdir();(system/'mihomo').write_bytes(b'core')
        daemon=system/'mihomo.plist';daemon.touch()
        prepared=self.root/'apps/mihomo/current/mihomo';prepared.parent.mkdir(parents=True);prepared.write_bytes(b'core')
        with patch.object(mac,'SYSTEM',system),patch.object(mac,'DAEMON',daemon), \
                patch.object(mac,'install_tailnet_entry'),patch.object(mac,'api',return_value={}), \
                patch.object(mac,'expose_dashboard',side_effect=OSError('forwarding failed')), \
                patch.object(mac,'admin') as admin:
            with self.assertRaises(OSError):mac.apply(self.root)
            admin.assert_not_called()


if __name__=='__main__':unittest.main()
