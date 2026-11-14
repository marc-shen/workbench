#!/usr/bin/env python3
"""Local-only Clash subscription composition and Steam Deck proxy management."""
import copy
import gzip
import json
import os
from pathlib import Path
import pwd
import subprocess as sp
import tarfile
import urllib.request

from config_data import read as read_toml
REPO = Path(__file__).resolve().parents[1]


def compose(provider, root, address):
    config = copy.deepcopy(provider)
    if not isinstance(config, dict) or not config.get('rules') or not config.get('proxy-groups'):
        raise RuntimeError('Expected a complete Clash configuration with rules and proxy groups')
    # Preserve vendor routes and groups; opt-in local policies add scoped groups.
    bypass = [f'PROCESS-NAME,{p},DIRECT' for p in ('tailscaled','tailscale','steamcommunity_302.cli','steamcommunity_302.caddy','caddy')]
    mirrors = read_toml(REPO / 'catalog/mirrors.toml')['proxy']
    bypass += [f'DOMAIN-SUFFIX,{domain},DIRECT' for domain in mirrors['direct_domains']]
    bypass += [f'DOMAIN-REGEX,{pattern},DIRECT' for pattern in mirrors.get('direct_regex', [])]
    bypass += ['DOMAIN-SUFFIX,ts.net,DIRECT','DOMAIN-SUFFIX,tailscale.com,DIRECT','DOMAIN-SUFFIX,tailscale.io,DIRECT',
               'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve','IP-CIDR6,fd7a:115c:a1e0::/48,DIRECT,no-resolve',
               'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve','IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
               'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve','IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
               'IP-CIDR,169.254.0.0/16,DIRECT,no-resolve','IP-CIDR,224.0.0.0/4,DIRECT,no-resolve',
               'IP-CIDR6,::1/128,DIRECT,no-resolve','IP-CIDR6,fc00::/7,DIRECT,no-resolve',
               'IP-CIDR6,fe80::/10,DIRECT,no-resolve','IP-CIDR6,ff00::/8,DIRECT,no-resolve']
    from steam_routing import configure as configure_steam
    config['rules'] = bypass + configure_steam(config, root) + config['rules']
    for key in ('external-controller-tls','external-controller-unix','external-controller-pipe','external-ui-url','listeners','redir-port','tproxy-port','port','socks-port'):
        config.pop(key, None)
    config.update({'mixed-port':2080,'allow-lan':False,'bind-address':'127.0.0.1','mode':'rule',
                   'external-controller':'0.0.0.0:9090','external-ui':str(root/'apps/metacubexd/current'),
                   'secret':'','external-controller-cors':{'allow-origins':list(dict.fromkeys(['http://'+address+':9090','http://127.0.0.1:9090','http://localhost:9090'])),'allow-private-network':True},
                   'find-process-mode':'always','log-level':'info'})
    config['tun']={'enable':True,'device':'mihomo-tun','stack':'mixed','auto-route':True,'auto-redirect':True,
                   'auto-detect-interface':True,'strict-route':False,'dns-hijack':['any:53','tcp://any:53'],
                   'route-exclude-address':['100.64.0.0/10','fd7a:115c:a1e0::/48','127.0.0.0/8','::1/128','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16',
                                            '169.254.0.0/16','224.0.0.0/4','fc00::/7','fe80::/10','ff00::/8'],
                   'exclude-interface':['tailscale0']}
    dns=config.setdefault('dns',{})
    dns.update({'enable':True,'listen':'127.0.0.1:1053','use-hosts':True,'use-system-hosts':True})
    dns.setdefault('nameserver-policy',{})['+.ts.net']='100.100.100.100'
    # DNS bootstrap is explicit, independent of a proxy node or system's TUN DNS.
    dns.setdefault('nameserver', ['https://doh.pub/dns-query', 'https://dns.alidns.com/dns-query'])
    dns.setdefault('default-nameserver', ['223.5.5.5', '119.29.29.29'])
    dns.setdefault('proxy-server-nameserver', ['https://dns.alidns.com/dns-query', 'https://doh.pub/dns-query'])
    dns['fake-ip-filter']=list(dict.fromkeys(dns.get('fake-ip-filter',[])+['*.ts.net','*.tailscale.com','*.tailscale.io']))
    config.setdefault('profile',{})['store-selected']=True
    policy = root/'config/mihomo/fallbacks.toml'
    if policy.exists():
        from mihomo_overrides import apply_fallbacks
        apply_fallbacks(config, read_toml(policy))
    # Only explicit user-facing wrappers may be temporarily rejected by the breaker.
    fallbacks = {g['name'] for g in config['proxy-groups'] if g['type'] == 'fallback'}
    for group in config['proxy-groups']:
        if group['type'] == 'select' and any(n in fallbacks for n in group.get('proxies', [])):
            if 'REJECT' not in group['proxies']:
                group['proxies'].append('REJECT')
    # Direct mode alone does not suspend health checks. Idle groups/providers
    # must skip periodic checks once no proxy traffic touches them.
    for group in config.get('proxy-groups', []):
        group['lazy'] = True
    for provider in config.get('proxy-providers', {}).values():
        provider.setdefault('health-check', {})['lazy'] = True
    return config


def ensure_yaml(root, action):
    try:
        import yaml
        if action == 'prepare':
            helper = root/'data/mihomo-helper-3.12/bin/python'
            if not helper.exists() or sp.run([helper, '-c', 'import yaml'], stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode:
                raise ImportError('service needs a managed YAML runtime')
        return True
    except ImportError:
        runtime=root/'data/mihomo-helper-3.12';python=runtime/'bin/python'
        if not python.exists():sp.run([root/'bin/uv','venv','--python',root/'apps/python/current/bin/python3',runtime],check=True)
        if sp.run([python, '-c', 'import yaml'], stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode:
            sp.run([root/'bin/uv','pip','install','--python',python,'PyYAML==6.0.2'],check=True)
        sp.run([python,REPO/'libexec/deck_network.py','--prefix',root,'mihomo',action],check=True)
        return False


def refresh_config(root, refresh=False):
    """Validate before replacing either local subscription or live configuration."""
    import yaml
    from deck_network import write, tailscale_ip
    folder=root/'config/mihomo'
    data=None
    if refresh or not (folder/'provider.yaml').exists():
        url=(folder/'subscription.url').read_text().strip()
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'clash.meta'})
            with urllib.request.urlopen(request,timeout=60) as reply:data=reply.read().decode()
            provider=yaml.safe_load(data)
            compose(provider,root,tailscale_ip())
        except Exception as error:
            raise RuntimeError('Subscription fetch/validation failed; old configuration retained ('+type(error).__name__+')') from None
    else:
        provider=yaml.safe_load((folder/'provider.yaml').read_text())
    config=compose(provider,root,tailscale_ip())
    candidate=folder/'candidate.yaml';write(candidate,yaml.safe_dump(config,allow_unicode=True,sort_keys=False),0o600)
    from mihomo_runtime import standby_config, private_json
    standby=folder/'standby-candidate.json'
    private_json(standby, standby_config(config, folder))
    env=dict(os.environ,SAFE_PATHS=str(root/'apps'))
    with (folder/'validation.log').open('w') as log:
        for variant in (candidate, standby):
            result=sp.run([root/'apps/mihomo/current/mihomo','-t','-d',folder,'-f',variant],env=env,stdout=log,stderr=sp.STDOUT,timeout=120)
            if result.returncode:raise RuntimeError('Mihomo validation failed; old configuration retained; see local config/mihomo/validation.log')
    standby.unlink()
    target=folder/'config.yaml'
    changed=not target.exists() or target.read_bytes()!=candidate.read_bytes()
    candidate.replace(target)
    if data is not None:write(folder/'provider.yaml',data,0o600)
    print('Prepared:',len(provider.get('proxies',[])),'nodes,',len(provider['proxy-groups']),'vendor groups,',len(provider['rules']),'vendor rules')
    return changed


def install_update_timer(root):
    from deck_network import write
    units=Path.home()/'.config/systemd/user'
    write(units/'mihomo-subscription-update.service',f'''[Unit]
Description=Refresh Mihomo subscription and apply validated configuration
ConditionPathExists={root / 'config/mihomo/subscription.url'}
[Service]
Type=oneshot
UMask=0077
ExecStart="{root / 'bin/workbench'}" mihomo update
TimeoutStartSec=8min
''')
    write(units/'mihomo-subscription-update.timer','''[Unit]
Description=Daily Mihomo subscription update
[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=10min
Persistent=true
[Install]
WantedBy=timers.target
''')
    sp.run(['systemctl','--user','daemon-reload'],check=True)
    sp.run(['systemctl','--user','enable','--now','mihomo-subscription-update.timer'],check=True)


def daily_update(root):
    from mihomo_network import network_available
    online, reason = network_available()
    if online is not True:
        print('Subscription update skipped: ' + reason + '; cached configuration retained.')
        return
    if not ensure_yaml(root,'update'):return
    os.umask(0o077)
    folder=root/'config/mihomo'
    previous={name:(folder/name).read_bytes() if (folder/name).exists() else None
              for name in ('config.yaml','provider.yaml')}
    active=sp.run(['systemctl','--user','is-active','--quiet','mihomo']).returncode==0
    try:
        changed=refresh_config(root,True)
    except Exception:
        restore_config(folder,previous)
        raise
    if not changed or not active:
        print('Subscription checked; configuration unchanged or service intentionally inactive. No restart.')
        return
    try:
        sp.run(['systemctl','--user','restart','mihomo'],check=True,timeout=45)
        from network_diagnostics import wait_ready
        wait_ready()
    except Exception:
        restore_config(folder,previous)
        result=sp.run(['systemctl','--user','restart','mihomo'],timeout=45)
        raise RuntimeError('Update activation failed; previous configuration restored; recovery restart exit='+str(result.returncode)) from None
    print('Subscription updated and Mihomo restarted successfully.')


def restore_config(folder, previous):
    for name,data in previous.items():
        target=folder/name
        if data is None:target.unlink(missing_ok=True)
        else:
            temporary=target.with_suffix('.restore')
            temporary.write_bytes(data);temporary.chmod(0o600);temporary.replace(target)


def prepare(root):
    if not ensure_yaml(root,'prepare'):return
    from deck_network import archive, link, load, write, tailscale_ip
    os.umask(0o077)
    specs=read_toml(REPO/'components/mihomo/component.toml')['install']['assets']
    for name,spec in specs.items():
        app=root/'apps'/name/spec['version'];app.mkdir(parents=True,exist_ok=True)
        if not (app/('mihomo' if name=='mihomo' else 'index.html')).exists():
            source=archive(root,spec)
            if name=='mihomo':
                binary=app/'mihomo';binary.write_bytes(gzip.decompress(source.read_bytes()));binary.chmod(0o755)
                sp.run([binary,'-v'],check=True,stdout=sp.DEVNULL)
            else:
                with tarfile.open(source) as tar:tar.extractall(app,filter='data')
        link(app,root/'apps'/name/'current',root)
    # The dashboard and API share an origin on both localhost and Tailscale.
    write(root/'apps/metacubexd/current/config.js',
          'window.__METACUBEXD_CONFIG__ = {defaultBackendURL: window.location.origin};\n')
    folder=root/'config/mihomo';folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
    if not (folder/'provider.yaml').exists() and not (folder/'subscription.url').exists():
        install_update_timer(root)
        print('Clients installed. Put your subscription URL in config/mihomo/subscription.url (mode 600), then run workbench mihomo install.')
        return
    refresh_config(root)
    unit=f'''[Unit]
Description=Mihomo proxy with MetaCubeXD
[Service]
Type=simple
UMask=0077
Environment="SAFE_PATHS={root / 'apps'}"
ExecStartPre=/usr/bin/systemctl is-active --quiet profile-mihomo-firewall.service
ExecStartPre=/usr/bin/grep -Fq "tcp dport 9090 drop" /etc/profile-mihomo/firewall.nft
ExecStart="{root / 'data/mihomo-helper-3.12/bin/python'}" -u "{REPO / 'libexec/mihomo_network.py'}" --core "{root / 'apps/mihomo/current/mihomo'}" --config "{folder / 'config.yaml'}"
TimeoutStopSec=40
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
'''
    write(Path.home()/'.config/systemd/user/mihomo.service',unit)
    install_update_timer(root)
    if sp.run(['systemctl','--user','is-active','--quiet','mihomo']).returncode:
        print('Run workbench mihomo apply in local Konsole for one-time administrator setup.')
    else:
        print('Running service preserved; restart mihomo when ready to apply configuration changes.')


def activate(root):
    from deck_network import tailscale_ip,write,load
    if os.environ.get('SSH_CONNECTION'):raise RuntimeError('Run activate in the local Deck Konsole')
    sp.run(['sudo','bash',REPO/'platforms/steamdeck/network/mihomo-privileged.sh',root,pwd.getpwuid(os.getuid()).pw_name],check=True)
    sp.run(['sudo','/usr/bin/python3',REPO/'platforms/steamdeck/network/baseline.py',
            '--user',pwd.getpwuid(os.getuid()).pw_name],check=True)
    try:
        sp.run(['systemctl','--user','restart','mihomo'],check=True)
        from network_diagnostics import wait_ready
        wait_ready()
    except Exception:
        sp.run(['systemctl','--user','stop','mihomo'])
        raise
    sp.run(['systemctl','--user','enable','mihomo'],check=True)
    local=root/'config/local/deck-network.json';settings=load(local,{})
    settings['backend']='mihomo';write(local,json.dumps(settings)+'\n',0o600)
    print('Enabled: http://127.0.0.1:9090/ui/ locally, or http://'+tailscale_ip()+':9090/ui/ over Tailscale.')
    from mihomo_network import status
    print(status())
