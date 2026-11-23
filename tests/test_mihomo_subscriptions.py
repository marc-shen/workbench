import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
import mihomo_subscriptions as subscriptions
from mihomo_health import GroupHealth
from mihomo_runtime import standby_config


@unittest.skipUnless(importlib.util.find_spec('yaml'),'Requires managed mihomo-helper Python')
class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.base={'proxies':[{'name':'node.a','type':'socks5','server':'example.org','port':1080},
                              {'name':'node`b','type':'socks5','server':'example.net','port':1080}],
            'proxy-groups':[{'name':'region','type':'url-test','proxies':['node.a','node`b']},
                            {'name':'main','type':'select','proxies':['region','node.a','DIRECT']},
                            {'name':'scoped','type':'fallback','proxies':['node`b','node.a'],'url':'https://steam.test','interval':180},
                            {'name':'steam','type':'select','proxies':['scoped','REJECT']}],
            'rules':['DOMAIN,example.com,main','MATCH,main']}
        self.records=[{'url':'https://one.invalid/private','document':copy.deepcopy(self.base)},
                      {'url':'https://two.invalid/private','document':{'proxies':[copy.deepcopy(self.base['proxies'][0])],
                       'rules':['MATCH,REJECT'],'proxy-groups':[]}}]

    def test_line_comments_blank_lines_and_duplicate_urls(self):
        self.assertEqual(subscriptions.urls_from_text(' # first\nhttps://one.invalid/?secret=x#part\n\nhttps://one.invalid/?secret=x#part\nhttps://two.invalid\n'),
                         ['https://one.invalid/?secret=x#part','https://two.invalid'])
        with self.assertRaisesRegex(RuntimeError,'line 2') as error:
            subscriptions.urls_from_text('# comment\nfile:///private/credential')
        self.assertNotIn('/private/credential',str(error.exception))

    def test_failed_second_download_keeps_active_files_and_hides_url(self):
        (self.folder/'subscription.url').write_text('\n'.join(r['url'] for r in self.records))
        (self.folder/'config.yaml').write_text('old configuration')
        import yaml
        response=mock.MagicMock();response.__enter__.return_value.read.return_value=yaml.safe_dump(self.base).encode()
        with mock.patch.object(subscriptions.urllib.request,'urlopen',side_effect=[response,OSError('https://two.invalid/private')]):
            with self.assertRaisesRegex(RuntimeError,'Subscription 2') as error:subscriptions.load(self.folder,True)
        self.assertNotIn('/private',str(error.exception))
        self.assertEqual((self.folder/'config.yaml').read_text(),'old configuration')
        self.assertFalse((self.folder/subscriptions.CACHE).exists())

    def test_first_subscription_must_supply_rules_additional_only_nodes(self):
        subscriptions.validate_document(self.records[1]['document'],2)
        with self.assertRaisesRegex(RuntimeError,'First subscription'):subscriptions.validate_document(self.records[1]['document'],1)

    def test_node_collisions_are_namespaced_and_primary_match_is_retained(self):
        original=copy.deepcopy(self.base)
        c=subscriptions.attach(self.base,self.records,self.folder)
        self.assertEqual(self.base,original);self.assertEqual(c['x-workbench-expanded-rules'],self.base['rules'])
        self.assertEqual(c['proxies'],[]);self.assertEqual(len(c['proxy-providers']),2)
        payloads=[p['payload'] for p in c['proxy-providers'].values()]
        self.assertEqual(payloads[0][0]['name'],'node.a');self.assertNotEqual(payloads[1][0]['name'],'node.a')
        main=next(g for g in c['proxy-groups'] if g['name']=='main')
        self.assertEqual(main['proxies'][:2],['region','DIRECT']);self.assertIn(subscriptions.ALL_GROUP,main['proxies'])
        self.assertEqual(main['filter'],subscriptions.exact_filter(['region','node.a','DIRECT']))
        self.assertEqual(next(g for g in c['proxy-groups'] if g['name']=='steam')['proxies'],['scoped','REJECT'])
        region=next(g for g in c['proxy-groups'] if g['name']=='region')
        self.assertEqual(len(region['use']),1);self.assertIn(r'\x60',region['filter'])
        self.assertFalse(any('url' in p for p in c['proxy-providers'].values()))

    def test_rules_keep_override_source_and_action_order(self):
        extra=self.records[1]['document']
        extra['proxy-groups']=[{'name':'main','type':'select','proxies':['node.a','DIRECT']}]
        extra['rules']=['DOMAIN,example.com,main','DOMAIN,extra.com,node.a','MATCH,REJECT']
        config=copy.deepcopy(self.base)
        override='IP-CIDR,100.64.0.0/10,DIRECT,no-resolve'
        config['rules'].insert(0,override)
        c=subscriptions.attach(config,self.records,self.folder)
        self.assertEqual(c['x-workbench-expanded-rules'][0],override)
        self.assertEqual(c['x-workbench-expanded-rules'][1],self.base['rules'][0])
        self.assertEqual(c['rules'][-1],'MATCH,main')
        extra_group=next(g for g in c['proxy-groups'] if g['name'].startswith('[') and g['name'].endswith('main'))
        self.assertIn('DOMAIN,example.com,'+extra_group['name'],c['x-workbench-expanded-rules'])
        self.assertNotIn(subscriptions.ALL_GROUP,extra_group.get('proxies',[]))
        self.assertEqual(len(extra_group['use']),1)
        alias=next(g for g in c['proxy-groups'] if g.get('hidden'))
        self.assertTrue(alias['name'].endswith('node.a'));self.assertEqual(alias['use'],extra_group['use'])
        first=c['rules'][0].split(',')[1]
        self.assertIn('global-override',first)
        self.assertEqual(c['rule-providers'][first]['payload'],['IP-CIDR,100.64.0.0/10,no-resolve'])

    def test_nonadjacent_rules_are_not_grouped_by_action(self):
        from mihomo_rules import compile_providers
        rules=['DOMAIN,a.com,DIRECT','DOMAIN,b.com,DIRECT','DOMAIN,a.com,REJECT',
               'DOMAIN,c.com,DIRECT','AND,((NETWORK,UDP),(DST-PORT,443)),REJECT','MATCH,DIRECT']
        config={'rules':rules[:]}
        compile_providers(config,[('source',rules)])
        providers=config['rule-providers']
        self.assertEqual(len(providers),4)
        self.assertEqual(list(providers.values())[0]['payload'],['DOMAIN,a.com','DOMAIN,b.com'])
        self.assertEqual(list(providers.values())[-1]['payload'],['AND,((NETWORK,UDP),(DST-PORT,443))'])
        self.assertEqual(config['rules'][-1],'MATCH,DIRECT')

    def test_secondary_native_rule_providers_are_namespaced(self):
        extra=self.records[1]['document']
        extra['rule-providers']={'domains':{'type':'inline','behavior':'domain','payload':['example.org']}}
        extra['rules']=['RULE-SET,domains,REJECT','MATCH,DIRECT']
        c=subscriptions.attach(self.base,self.records,self.folder)
        names=[n for n in c['rule-providers'] if n.endswith('-domains')]
        self.assertEqual(len(names),1)
        self.assertIn('RULE-SET,'+names[0]+',REJECT',c['rules'])

    def test_unresolved_secondary_policy_cannot_bind_to_primary_group(self):
        self.records[1]['document']['rules']=['DOMAIN,example.com,main','MATCH,DIRECT']
        with self.assertRaisesRegex(RuntimeError,'unresolved policy'):
            subscriptions.attach(self.base,self.records,self.folder)

    def test_nonterminal_match_is_rejected_instead_of_reordering(self):
        self.records[1]['document']['rules']=['MATCH,DIRECT','DOMAIN,example.com,REJECT']
        with self.assertRaisesRegex(RuntimeError,'MATCH must be the final'):
            subscriptions.attach(self.base,self.records,self.folder)

    def test_immutable_snapshots_keep_previous_provider_on_failure(self):
        c=subscriptions.attach(self.base,self.records,self.folder)
        path=Path(next(iter(c['proxy-providers'].values()))['path']);before=path.read_bytes()
        changed=copy.deepcopy(self.records);changed[0]['document']['proxies'][0]['server']='new.example'
        next_base=copy.deepcopy(self.base);next_base['proxies'][0]['server']='new.example'
        newer=subscriptions.attach(next_base,changed,self.folder)
        new_path=Path(next(iter(newer['proxy-providers'].values()))['path'])
        self.assertNotEqual(path,new_path);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        subscriptions.prune(self.folder,newer,c)
        self.assertTrue(path.exists());self.assertTrue(new_path.exists())

    def test_offline_provider_and_group_checks_are_disabled(self):
        c=subscriptions.attach(self.base,self.records,self.folder)
        standby=standby_config(c,self.folder)
        for p in standby['proxy-providers'].values():
            self.assertFalse(p['health-check']['enable']);self.assertEqual(p['interval'],0)
        self.assertTrue(all(g['type']=='select' for g in standby['proxy-groups']))

    def test_scoped_health_uses_provider_node_endpoint(self):
        c=subscriptions.attach(self.base,self.records,self.folder)
        api=mock.Mock();api.request.return_value={'delay':20}
        health=GroupHealth(api,c,self.folder)
        self.assertEqual(health.groups['scoped']['proxies'],['node`b','node.a'])
        self.assertNotIn('region',health.groups)
        self.assertTrue(health.test('node.a','https://steam.test'))
        path=api.request.call_args.args[1]
        self.assertTrue(path.startswith('/providers/proxies/'))
        self.assertIn('/node.a/healthcheck?',path)

    def test_cached_sources_follow_url_order_and_removal(self):
        import yaml
        subscriptions.save(self.folder,self.records)
        (self.folder/'subscription.url').write_text('\n'.join(r['url'] for r in self.records))
        with mock.patch.object(subscriptions.urllib.request,'urlopen') as fetch:
            self.assertEqual(subscriptions.load(self.folder),self.records);fetch.assert_not_called()
        (self.folder/'subscription.url').write_text(self.records[0]['url'])
        response=mock.MagicMock();response.__enter__.return_value.read.return_value=yaml.safe_dump(self.base).encode()
        with mock.patch.object(subscriptions.urllib.request,'urlopen',return_value=response):
            self.assertEqual(len(subscriptions.load(self.folder)),1)

if __name__=='__main__':unittest.main()
