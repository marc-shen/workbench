"""Opt-in isolated core integration; no live proxy settings are modified.
Run with WORKBENCH_TEST_MIHOMO=1 and the managed mihomo-helper Python.
"""
import os
from pathlib import Path
import unittest

@unittest.skipUnless(os.getenv('WORKBENCH_TEST_MIHOMO') == '1' and
    (Path.home()/'Software/apps/mihomo/current/mihomo').is_file(), 'Opt-in real Mihomo integration')
class SubscriptionIntegrationTests(unittest.TestCase):
    def test_two_http_sources_and_provider_selection(self):
        import sys,tempfile,json,subprocess as s,os,time,threading
        from pathlib import Path
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
        import yaml
        from mihomo_subscriptions import load,attach,ALL_GROUP
        from mihomo_rules import expanded
        base={'proxies':[{'name':'same-node','type':'socks5','server':'127.0.0.1','port':9}],
              'proxy-groups':[{'name':'region','type':'select','proxies':['same-node']},
                              {'name':'main','type':'select','proxies':['region','same-node','DIRECT']}],
              'rules':['DOMAIN,overlap.test,REJECT','DOMAIN,primary.test,DIRECT','MATCH,DIRECT']}
        extra={'proxies':[{'name':'same-node','type':'socks5','server':'127.0.0.1','port':10}],
               'proxy-groups':[{'name':'main','type':'select','proxies':['REJECT','DIRECT','same-node']}],
               'rule-providers':{'extra-domains':{'type':'inline','behavior':'domain','payload':['native.test']}},
               'rules':['DOMAIN,override.test,REJECT','DOMAIN,overlap.test,DIRECT','DOMAIN,second.test,main',
                        'RULE-SET,extra-domains,REJECT','MATCH,REJECT']}
        class Handler(BaseHTTPRequestHandler):
         def do_GET(self):
          body=(yaml.safe_dump(base if self.path=='/primary' else extra).encode()
                if self.path in ('/primary','/extra') else b'fixture response')
          self.send_response(200);self.end_headers();self.wfile.write(body)
         def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        import urllib.request,urllib.parse
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        import socket
        def free_port():
         with socket.socket() as sock:
          sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
        control_port=free_port();proxy_port=free_port()
        with tempfile.TemporaryDirectory(prefix='workbench-subscriptions-') as tmp:
         root=Path(tmp);folder=root/'config/mihomo';folder.mkdir(parents=True)
         (folder/'subscription.url').write_text(f'# main rules\nhttp://127.0.0.1:{server.server_port}/primary\n\nhttp://127.0.0.1:{server.server_port}/extra\n')
         records=load(folder,True)
         import copy
         composed=copy.deepcopy(records[0]['document'])
         composed['rules'][:0]=['DOMAIN,override.test,DIRECT','IP-CIDR,100.64.0.0/10,DIRECT,no-resolve']
         config=attach(composed,records,folder)
         config.update({'tun':{'enable':False},'external-controller':f'127.0.0.1:{control_port}',
                        'mixed-port':proxy_port,'dns':{'enable':False},'mode':'rule',
                        'hosts':{name: '127.0.0.1' for name in ('override.test','overlap.test','primary.test','second.test','default.test','native.test')}})
         candidate=folder/'config.yaml';candidate.write_text(yaml.safe_dump(config,allow_unicode=True));candidate.chmod(0o600)
         binary=Path.home()/'Software/apps/mihomo/current/mihomo';env=dict(os.environ,SAFE_PATHS=str(root))
         with (folder/'test.log').open('w') as log:
          expanded_path=folder/'expanded.yaml';expanded_path.write_text(yaml.safe_dump(expanded(config),allow_unicode=True))
          for path in (expanded_path,candidate):
           s.run([binary,'-t','-d',folder,'-f',path],stdout=log,stderr=s.STDOUT,check=True,env=env)
          child=s.Popen([binary,'-d',folder,'-f',candidate],stdout=log,stderr=s.STDOUT,env=env)
          def api(path,method='GET',data=None):
           req=urllib.request.Request(f'http://127.0.0.1:{control_port}'+path,method=method,data=None if data is None else json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
           with opener.open(req,timeout=3) as r:
            body=r.read();return json.loads(body) if body else None
          try:
           for _ in range(30):
            try:p=api('/providers/proxies')['providers'];break
            except OSError:time.sleep(.2)
           else:raise RuntimeError('Fixture core failed to start')
           actual={k:v for k,v in p.items() if k.startswith('subscription-')}
           assert len(actual)==2
           nodes=[n['name'] for v in actual.values() for n in v['proxies']]
           assert len(nodes)==len(set(nodes))==2
           assert api('/rules')['rules'][-1]['proxy']=='DIRECT'
           assert api('/proxies')['proxies']['main']['all'][0]=='region'
           assert api('/proxies')['proxies']['main']['now']=='region'
           rule_providers=api('/providers/rules')['providers']
           for name,provider in config['rule-providers'].items():
            assert rule_providers[name]['ruleCount']==len(provider['payload']), name
           # Real HTTP flows verify override priority, source conflicts, secondary
           # rules/groups, native rule providers and the single primary catch-all.
           def status(host):
            result=s.run(['curl','--noproxy','','-sS','--max-time','5','-o',os.devnull,'-w','%{http_code}',
                          '-x',f'http://127.0.0.1:{proxy_port}',f'http://{host}:{server.server_port}/probe'],capture_output=True,text=True)
            return result.stdout
           for host in ('override.test','primary.test','default.test'):
            assert status(host)=='200',host
           for host in ('overlap.test','second.test','native.test'):
            assert status(host)!='200',host
           secondary=next(g['name'] for g in config['proxy-groups'] if g['name'].startswith('[') and g['name'].endswith('main'))
           api('/proxies/'+urllib.parse.quote(secondary,safe=''),'PUT',{'name':'DIRECT'})
           assert status('second.test')=='200'
           for name in actual:api('/providers/proxies/'+name,'PUT')
           api('/proxies/main','PUT',{'name':ALL_GROUP})
           api('/proxies/'+urllib.parse.quote(ALL_GROUP,safe=''),'PUT',{'name':'📦 订阅 2'})
           assert api('/proxies')['proxies'][ALL_GROUP]['now']=='📦 订阅 2'
           print('Real core: two sources, ordered rule providers, global override, conflicting routes, secondary group control, primary fallback and provider selection PASS')
          finally:child.terminate();child.wait(timeout=10)
        server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
