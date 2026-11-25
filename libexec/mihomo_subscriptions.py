"""Validated multi-subscription snapshots with ordered rules and isolated groups."""
import copy
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ALL_GROUP = '📚 全部订阅'
CACHE = 'subscriptions.json'


def urls_from_text(text):
    urls=[]
    for number,line in enumerate(text.splitlines(),1):
        url=line.strip()
        if not url or url.startswith('#'):continue
        try:
            parsed=urllib.parse.urlsplit(url)
            valid=parsed.scheme in ('https','http') and parsed.hostname and not any(c.isspace() for c in url)
        except ValueError:valid=False
        if not valid:raise RuntimeError(f'subscription.url line {number}: expected one HTTP(S) subscription URL')
        if url not in urls:urls.append(url)
    if not urls:raise RuntimeError('subscription.url contains no subscription URLs')
    return urls


def validate_document(document, index):
    if not isinstance(document,dict) or not isinstance(document.get('proxies'),list) or not document['proxies']:
        raise RuntimeError(f'Subscription {index}: expected Clash YAML with a nonempty proxies list')
    names=[]
    for proxy in document['proxies']:
        if not isinstance(proxy,dict) or not isinstance(proxy.get('name'),str) or not proxy['name'] or not proxy.get('type'):
            raise RuntimeError(f'Subscription {index}: invalid proxy entry')
        names.append(proxy['name'])
    if len(set(names))!=len(names):raise RuntimeError(f'Subscription {index}: duplicate node names within one source')
    if index==1 and (not document.get('rules') or not document.get('proxy-groups')):
        raise RuntimeError('First subscription must include Clash rules and proxy-groups')


def load(folder, refresh=False):
    import yaml
    source=folder/'subscription.url'
    from mihomo_setup import has_subscription
    urls=urls_from_text(source.read_text()) if has_subscription(folder) else []
    cache=folder/CACHE
    records=None
    if not refresh and cache.exists():
        saved=json.loads(cache.read_text())
        if [r['url'] for r in saved]==(urls or [None]):records=saved
    if records is None and not refresh and not cache.exists() and len(urls)<=1 and (folder/'provider.yaml').exists():
        records=[{'url':urls[0] if urls else None,'document':yaml.safe_load((folder/'provider.yaml').read_text())}]
    if records is None:
        if not urls:raise RuntimeError('Put subscription URLs in config/mihomo/subscription.url, one per line')
        records=[]
        for index,url in enumerate(urls,1):
            try:
                request=urllib.request.Request(url,headers={'User-Agent':'clash.meta'})
                with urllib.request.urlopen(request,timeout=60) as reply:document=yaml.safe_load(reply.read())
            except Exception as error:
                # Download/parser errors may embed credentials. Only report the ordinal/type.
                raise RuntimeError(f'Subscription {index} fetch failed ({type(error).__name__}); old configuration retained') from None
            records.append({'url':url,'document':document})
    for index,record in enumerate(records,1):validate_document(record['document'],index)
    return records


def save(folder, records):
    from mihomo_runtime import private_json
    private_json(folder/CACHE,records)


def exact_filter(names):
    # Mihomo preserves the order of backtick-separated filter expressions.
    return '`'.join('^'+re.escape(name).replace('`',r'\x60')+'$' for name in names)


def attach(config, records, folder):
    """Convert explicit nodes to immutable file providers after platform composition.

    Native file providers can be inspected/refreshed/tested from MetaCubeXD. Only
    Workbench fetches remote subscriptions, so a rule/group update cannot race an
    independent provider download. Existing fallback scopes retain their members.
    """
    import yaml
    from mihomo_rules import mapped_reference
    result=copy.deepcopy(config)
    nodes={p['name'] for p in result.get('proxies',[])}
    original_groups=result.get('proxy-groups',[])
    primary_groups=list(original_groups)
    reserved=nodes|{g['name'] for g in original_groups}|set(result.get('proxy-providers',{}))
    names=[];payloads=[];groups=[];node_providers={};mappings=[];group_providers={}
    for index,record in enumerate(records,1):
        identity=hashlib.sha256((record['url'] or 'local-primary').encode()).hexdigest()[:12]
        name='subscription-'+identity
        group_name=f'📦 订阅 {index}'
        if name in reserved or group_name in reserved or ALL_GROUP in reserved:
            raise RuntimeError('Subscription configuration conflicts with managed provider/group names')
        reserved.update((name,group_name));names.append(name);groups.append(group_name)
        proxies=copy.deepcopy(result['proxies'] if index==1 else record['document']['proxies'])
        rename={p['name']:p['name'] if index==1 else f'[{identity}] '+p['name'] for p in proxies}
        source_groups=primary_groups if index==1 else copy.deepcopy(record['document'].get('proxy-groups',[]))
        if index>1:
            if record['document'].get('proxy-providers'):
                raise RuntimeError(f'Subscription {index}: dynamic proxy providers require an explicit merge')
            group_names=[g['name'] for g in source_groups]
            if len(set(group_names))!=len(group_names) or set(group_names)&set(rename):
                raise RuntimeError(f'Subscription {index}: duplicate group/node names')
            rename.update({n:f'[{identity}] '+n for n in group_names})
            for group in source_groups:
                if group.get('use') or any(group.get(k) for k in ('include-all','include-all-proxies','include-all-providers')):
                    raise RuntimeError(f'Subscription {index}: dynamic group selection requires an explicit merge')
                group['name']=rename[group['name']]
                group['proxies']=[mapped_reference(n,rename,index) for n in group.get('proxies',[])]
                group['lazy']=True
            for mapped in rename.values():
                if mapped in reserved:raise RuntimeError('Subscription groups/nodes collide after source prefixing')
                reserved.add(mapped)
            original_groups.extend(source_groups)
        mappings.append(rename)
        group_providers.update({g['name']:name for g in source_groups})
        for proxy in proxies:
            original=proxy['name'];proxy['name']=rename[original]
            if proxy.get('dialer-proxy'):
                proxy['dialer-proxy']=mapped_reference(proxy['dialer-proxy'],rename,index) if index>1 else rename.get(proxy['dialer-proxy'],proxy['dialer-proxy'])
            if proxy['name'] in node_providers:raise RuntimeError('Subscription node names collide after source prefixing')
            node_providers[proxy['name']]=name
        payloads.append({'proxies':proxies})
    from mihomo_rules import merge, compile_providers, split_rule
    sections=merge(result,records,mappings,folder)
    nodes=set(node_providers)
    # Every config generation references immutable files; validation/reload failures
    # cannot overwrite a provider used by the live core or the previous config.
    generation=hashlib.sha256(json.dumps(payloads,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    directory=folder/'subscriptions'/generation
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    providers=result.setdefault('proxy-providers',{})
    from deck_network import write
    for name,payload in zip(names,payloads):
        path=directory/(name+'.yaml')
        write(path,yaml.safe_dump(payload,allow_unicode=True,sort_keys=False),0o600)
        providers[name]={'type':'file','path':str(path),'payload':payload['proxies'],
            'health-check':{'enable':False,'url':'https://www.gstatic.com/generate_204','lazy':True,'interval':0}}
    scoped=[]
    for group in original_groups:
        direct=[n for n in group.get('proxies',[]) if n in nodes]
        if direct:
            if group.get('use') or group.get('filter') or group.get('include-all') or group.get('include-all-providers') or group.get('include-all-proxies'):
                raise RuntimeError('Cannot safely convert a group mixing explicit nodes with dynamic provider selection')
            if group['type']=='fallback' and len(direct)==len(group['proxies']):scoped.append(copy.deepcopy(group))
            ordered=list(group['proxies'])
            group['proxies']=[n for n in group['proxies'] if n not in nodes]
            if not group['proxies']:group.pop('proxies')
            group['use']=[group_providers[group['name']]];group['filter']=exact_filter(ordered)
            group['empty-fallback']='REJECT'
    # Direct references in rules/dialer-proxy still need a globally addressable
    # proxy. A hidden one-node group preserves those references without duplicate nodes.
    references=set()
    for rule in result.get('rules',[]):
        _,target,_=split_rule(rule)
        if target in nodes:references.add(target)
    for payload in payloads:
        for proxy in payload['proxies']:
            if proxy.get('dialer-proxy') in node_providers:references.add(proxy['dialer-proxy'])
    original_groups.extend({'name':n,'type':'select','use':[node_providers[n]],
        'filter':exact_filter([n]),'hidden':True,'empty-fallback':'REJECT'} for n in sorted(references))
    # Expose additional subscriptions as explicit opt-in choices. Never broaden a
    # fallback/url-test pool or a wrapper whose regional failure policy is managed.
    gateways=[g for g in primary_groups if g['type']=='select' and g.get('proxies')
              and not g.get('hidden') and 'REJECT' not in g['proxies']]
    for group in gateways:
        group['proxies'].append(ALL_GROUP)
    if not gateways and len(names)>1:
        # A minimal subscription may have a single node-only selector. Extend
        # that manual selector after its original choices, never a fallback pool.
        target=next((g for g in original_groups if g['type']=='select' and g.get('use')==[names[0]] and not g.get('hidden')),None)
        if target is None:raise RuntimeError('Main subscription needs a manual selector for additional subscriptions')
        target['use'].extend(names[1:])
        for payload in payloads[1:]:target['filter']+='`'+exact_filter([p['name'] for p in payload['proxies']])
    original_groups.extend({'name':name,'type':'select','use':[provider],'lazy':True,'empty-fallback':'REJECT'}
                           for name,provider in zip(groups,names))
    original_groups.append({'name':ALL_GROUP,'type':'select','proxies':groups})
    result['proxies']=[]
    result['x-workbench-scoped-fallbacks']=scoped
    result['x-workbench-node-providers']=node_providers
    compile_providers(result,sections)
    return result


def prune(folder, current, previous=None):
    """Keep current and previous provider generations for reload rollback."""
    keep=set()
    for config in (current,previous or {}):
        for provider in config.get('proxy-providers',{}).values():
            path=Path(provider.get('path',''))
            if path.parent.parent==folder/'subscriptions':keep.add(path.parent.name)
    import shutil
    for path in (folder/'subscriptions').glob('*'):
        if re.fullmatch('[0-9a-f]{64}',path.name) and path.name not in keep and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
