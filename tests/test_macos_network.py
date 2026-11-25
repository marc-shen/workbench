import copy
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'libexec'))
import macos_network as mac


class UnconfiguredMacNetworkTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        (self.root/'config/mihomo').mkdir(parents=True)
        for name,value in [('VERGE',self.root/'verge'),('DAEMON',self.root/'system/mihomo.plist')]:
            patch=mock.patch.object(mac,name,value);patch.start();self.addCleanup(patch.stop)
        self.output=io.StringIO()
        patch=mock.patch('sys.stdout',self.output);patch.start();self.addCleanup(patch.stop)

    def test_missing_empty_or_commented_subscription_still_prepares_everything(self):
        source=self.root/'config/mihomo/subscription.url'
        for content in (None, '', ' \n # add subscription here\n'):
            source.unlink(missing_ok=True)
            if content is not None:source.write_text(content)
            with self.subTest(content=content), mock.patch.object(mac,'install_assets') as assets, \
                 mock.patch.object(mac,'import_verge'), mock.patch.object(mac,'refresh') as refresh, \
                 mock.patch.object(mac,'install_user_entries') as entries:
                mac.prepare(self.root)
                for call in (assets,refresh,entries):call.assert_called_once_with(self.root)
                self.assertTrue(source.exists())
                self.assertEqual(source.stat().st_mode & 0o777,0o600)
        self.assertNotIn('[SKIP]',self.output.getvalue())

    def test_apply_without_preparation_reports_missing_configuration(self):
        with mock.patch.object(mac,'admin') as admin:
            with self.assertRaisesRegex(RuntimeError,'No validated configuration'):mac.apply(self.root)
            admin.assert_not_called()

    def test_adding_subscription_resumes_normal_preparation(self):
        source=self.root/'config/mihomo/subscription.url'
        source.write_text('https://example.invalid/private-token\n')
        with mock.patch.object(mac,'install_assets') as assets, \
             mock.patch.object(mac,'import_verge') as migrate, \
             mock.patch.object(mac,'refresh') as refresh, \
             mock.patch.object(mac,'install_user_entries') as entries:
            mac.prepare(self.root)
            for call in (assets,migrate,refresh,entries):call.assert_called_once_with(self.root)
        self.assertNotIn('private-token',self.output.getvalue())

    def test_bad_subscription_is_not_treated_as_absent(self):
        (self.root/'config/mihomo/subscription.url').write_text('not-a-subscription-url')
        with mock.patch.object(mac,'install_assets'),mock.patch.object(mac,'import_verge'), \
             mock.patch.object(mac,'refresh',side_effect=RuntimeError('invalid subscription')) as refresh, \
             mock.patch.object(mac,'install_user_entries') as entries:
            with self.assertRaisesRegex(RuntimeError,'invalid subscription'):mac.prepare(self.root)
            refresh.assert_called_once();entries.assert_not_called()

    def test_existing_config_without_url_still_reports_service_failure(self):
        (self.root/'config/mihomo/config.yaml').write_text('existing config')
        with mock.patch.object(mac.sp,'run',return_value=mock.Mock(returncode=1)), \
             mock.patch.object(mac,'dashboard_shared',return_value=False), \
             mock.patch.object(mac,'api',side_effect=OSError('not running')):
            self.assertEqual(mac.status(self.root,'mihomo',True),1)
        self.assertNotIn('[SKIP]',self.output.getvalue())



@unittest.skipUnless(importlib.util.find_spec('yaml'), 'Run with the managed mihomo-helper Python (PyYAML)')
class MacNetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);folder=self.root/'config/mihomo';folder.mkdir(parents=True)
        self.provider={'proxies':[{'name':'node','type':'socks5','server':'example.com','port':1080}],
            'proxy-groups':[{'name':'select','type':'select','proxies':['node']}],
            'rules':['MATCH,select']}

    def test_mac_tun_bypasses_tailnet_and_keeps_vendor_rules(self):
        before=copy.deepcopy(self.provider)
        result=mac.compose(self.provider,self.root)
        self.assertEqual(self.provider,before)
        self.assertEqual(result['external-controller'],'127.0.0.1:9090')
        self.assertEqual(result['secret'],'')
        self.assertEqual(result['tun']['device'],'utun1024')
        self.assertNotIn('auto-redirect',result['tun'])
        self.assertNotIn('exclude-interface',result['tun'])
        self.assertIn('100.64.0.0/10',result['tun']['route-exclude-address'])
        self.assertEqual(result['dns']['nameserver-policy']['+.ts.net'],'100.100.100.100')
        self.assertEqual(result['rules'][-1],'MATCH,select')

    def test_local_customizations_survive_new_subscription_nodes(self):
        overrides={'prepend-rules':['DOMAIN,example.org,custom'],
            'proxy-groups':[{'name':'custom','type':'select','proxies':['select','DIRECT']}]}
        result=mac.compose(self.provider,self.root,overrides)
        self.assertIn('DOMAIN,example.org,custom',result['rules'])
        self.assertLess(result['rules'].index('DOMAIN,example.org,custom'),result['rules'].index('MATCH,select'))
        self.assertEqual(result['proxy-groups'][-1]['name'],'custom')

    def test_disabled_ipv6_does_not_leave_node_dns_ipv6_enabled(self):
        self.provider['ipv6']=False
        self.provider['dns']={'ipv6':True,'proxy-server-nameserver':['https://doh.pub/dns-query']}
        result=mac.compose(self.provider,self.root)
        self.assertFalse(result['dns']['ipv6'])
        self.assertIn('8.8.8.8',result['dns']['default-nameserver'])
        self.assertIn('8.8.8.8',result['dns']['proxy-server-nameserver'])
        self.assertNotIn('system',result['dns']['default-nameserver'])

    def test_launchdaemon_uses_root_owned_core_not_user_executable(self):
        staged=mac.prepare_daemon(self.root)
        data=plistlib.loads(staged.read_bytes())
        self.assertEqual(data['ProgramArguments'][0],str(mac.SYSTEM/'mihomo'))
        self.assertTrue(data['KeepAlive'])
        self.assertEqual(data['Umask'],63)

    def test_existing_tailscale_does_not_download_or_reset_login(self):
        with mock.patch.object(mac.macos_tailscale,'install') as install, \
             mock.patch.object(mac.urllib.request,'urlopen') as fetch, mock.patch.object(mac,'admin') as admin:
            mac.install_tailscale(self.root)
        install.assert_called_once_with(self.root,False)
        fetch.assert_not_called();admin.assert_not_called()

    def test_subscribe_failure_retains_config_without_disclosing_url(self):
        secret_url='https://example.invalid/private-subscription-token'
        (self.root/'config/mihomo/subscription.url').write_text(secret_url)
        target=self.root/'config/mihomo/config.yaml';target.write_text('previous')
        with mock.patch.object(mac.urllib.request,'urlopen',side_effect=OSError(secret_url)):
            with self.assertRaises(RuntimeError) as error:mac.refresh(self.root,True)
        self.assertNotIn(secret_url,str(error.exception))
        self.assertEqual(target.read_text(),'previous')

    def test_failed_validation_never_reloads_live_core(self):
        import yaml
        folder=self.root/'config/mihomo'
        (folder/'provider.yaml').write_text(yaml.safe_dump(self.provider))
        (folder/'config.yaml').write_text('previous')
        with mock.patch.object(mac.sp,'run',return_value=mock.Mock(returncode=1)),mock.patch.object(mac,'api') as api:
            with self.assertRaises(RuntimeError):mac.refresh(self.root)
        api.assert_not_called()
        self.assertEqual((folder/'config.yaml').read_text(),'previous')

    def test_failed_reload_restores_previous_runtime_and_keeps_disk(self):
        import yaml
        folder=self.root/'config/mihomo';old=mac.compose(self.provider,self.root)
        (folder/'config.yaml').write_text(yaml.safe_dump(old))
        (folder/'subscription.url').write_text('https://example.invalid/sub')
        response=mock.MagicMock();response.__enter__.return_value.read.return_value=yaml.safe_dump(self.provider).encode()
        current={'mode':'direct','tun':{'enable':False}}
        calls=[]
        def api(root,method='GET',path='/configs',data=None):
            calls.append((method,data))
            if len(calls)==2:raise OSError('failed reload')
            return current
        before=(folder/'config.yaml').read_bytes()
        with mock.patch.object(mac.urllib.request,'urlopen',return_value=response), \
             mock.patch.object(mac.sp,'run',return_value=mock.Mock(returncode=0)),mock.patch.object(mac,'api',side_effect=api):
            with self.assertRaisesRegex(RuntimeError,'restored'):mac.refresh(self.root,True)
        self.assertEqual([x[0] for x in calls],['GET','PUT','PUT'])
        restored=json.loads(calls[-1][1]['payload'])
        self.assertEqual(restored['mode'],'direct');self.assertFalse(restored['tun']['enable'])
        self.assertEqual((folder/'config.yaml').read_bytes(),before)

if __name__=='__main__':unittest.main()
