#!/usr/bin/env python3
"""macOS network components: a native Mihomo LaunchDaemon and official Tailscale app."""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shlex
import subprocess as sp
import sys
import tarfile
import time
import urllib.parse
import urllib.request

from config_data import read
from deck_network import archive, write, link

REPO = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('SOFTWARE_HOME', Path.home() / 'Software'))
LABEL = 'io.workbench.mihomo'
SYSTEM = Path('/Library/Application Support/Workbench/mihomo')
DAEMON = Path('/Library/LaunchDaemons') / (LABEL + '.plist')
VERGE_LABEL = 'io.github.clash-verge-rev.clash-verge-rev'
VERGE = Path.home() / 'Library/Application Support' / VERGE_LABEL
TSAPP = Path('/Applications/Tailscale.app')
TSBIN = TSAPP / 'Contents/MacOS/Tailscale'


def run(args, **kwargs):
    return sp.run(list(map(str, args)), check=True, **kwargs)


def admin(args):
    command = shlex.join(list(map(str, args)))
    # argv avoids interpolating paths into AppleScript source. macOS owns the password UI.
    run(['/usr/bin/osascript', '-e', 'on run argv\n do shell script (item 1 of argv) with administrator privileges\nend run', command])


def api(root, method='GET', path='/configs', data=None):
    request = urllib.request.Request('http://127.0.0.1:9090' + path,
        data=None if data is None else json.dumps(data, ensure_ascii=False).encode(), method=method,
        headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as reply:
        body = reply.read()
        return json.loads(body) if body else None


def ensure_yaml(root, service, action):
    try:
        import yaml
        return True
    except ImportError:
        helper = root / 'data/mihomo-helper-3.12/bin/python'
        if not helper.exists():
            run([root/'bin/uv', 'venv', '--python', root/'apps/python/current/bin/python3', helper.parent.parent])
        if sp.run([helper, '-c', 'import yaml'], stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode:
            run([root/'bin/uv', 'pip', 'install', '--python', helper, 'PyYAML==6.0.2'])
        run([helper, __file__, '--prefix', root, service, action])
        return False


def dump_yaml(path, data):
    import yaml
    write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False), 0o600)


def import_verge(root):
    """Import once; subscription, custom overrides and selections never enter Git."""
    import yaml
    folder = root / 'config/mihomo'
    if (folder/'provider.yaml').exists() or (folder/'subscription.url').exists():
        return
    if not (VERGE/'profiles.yaml').exists():
        return
    profiles = yaml.safe_load((VERGE/'profiles.yaml').read_text())
    item = next((p for p in profiles['items'] if p.get('uid') == profiles['current']), None)
    if not item or not item.get('file'):
        raise RuntimeError('Clash Verge has no selected profile to import')
    source = (VERGE/'profiles'/item['file']).resolve()
    if not source.is_relative_to((VERGE/'profiles').resolve()):
        raise RuntimeError('Profile path must remain inside the Clash Verge profile directory')
    provider = yaml.safe_load(source.read_text())
    effective = yaml.safe_load((VERGE/'clash-verge.yaml').read_text())
    # Only additive rule edits can be safely replayed against later subscriptions.
    extra = [r for r in effective['rules'] if r not in provider['rules']]
    if [r for r in effective['rules'] if r not in extra] != provider['rules']:
        raise RuntimeError('Clash Verge reordered/deleted subscription rules; explicit migration is needed')
    if effective['rules'] != extra + provider['rules']:
        raise RuntimeError('Custom rules are not a prefix; refusing to silently change their priority')
    old_groups = {g['name']: g for g in provider['proxy-groups']}
    groups = [g for g in effective['proxy-groups'] if g != old_groups.get(g['name'])]
    overrides = {'prepend-rules': extra, 'proxy-groups': groups,
                 'dns': effective.get('dns', {}), 'ipv6': effective.get('ipv6', False)}
    dump_yaml(folder/'overrides.yaml', overrides)
    dump_yaml(folder/'provider.yaml', provider)
    if item.get('url'):
        write(folder/'subscription.url', item['url'].strip()+'\n', 0o600)
    write(folder/'imported-selections.json', json.dumps(item.get('selected', []), ensure_ascii=False), 0o600)
    print('Imported selected Clash Verge subscription, custom rules/groups and selections locally.')


def compose(provider, root, overrides=None):
    from mihomo_deck import compose as shared_compose
    config = shared_compose(provider, root, '127.0.0.1', steam=False, overrides=overrides)
    config['external-controller'] = '127.0.0.1:9090'
    config['secret'] = ''
    config['tun'].update(device='utun1024', stack='gvisor', **{'dns-mode': 'native'})
    config['tun'].pop('auto-redirect', None)
    config['tun'].pop('exclude-interface', None)
    # Network Extension process names differ from the Linux daemon.
    config['rules'][:0] = ['PROCESS-NAME,Tailscale,DIRECT',
                          'PROCESS-NAME,io.tailscale.ipn.macsys.network-extension,DIRECT']
    # Bootstrap must never recurse through the TUN's native DNS setting.
    # Bootstrap must work on networks that block individual public resolvers.
    # Keep independent operators; never recurse through native TUN system DNS.
    config['dns']['default-nameserver'] = ['223.6.6.6', '8.8.8.8', '1.1.1.1']
    config['dns']['proxy-server-nameserver'] = list(dict.fromkeys(
        config['dns'].get('proxy-server-nameserver', []) + ['8.8.8.8', '1.1.1.1']))
    if not config.get('ipv6', False):
        config['dns']['ipv6'] = False
    config['dns']['fake-ip-filter'] += ['captive.apple.com', '*.local', '*.lan']
    config['rules'].insert(0, 'DOMAIN,captive.apple.com,DIRECT')
    return config


def install_assets(root):
    spec = read(REPO/'components/mihomo/component.toml')['install']
    for name, asset in [('mihomo', spec['macos'][platform.machine()]), ('metacubexd', spec['assets']['metacubexd'])]:
        app = root/'apps'/name/asset['version']
        app.mkdir(parents=True, exist_ok=True)
        executable = app/('mihomo' if name == 'mihomo' else 'index.html')
        if not executable.exists():
            source = archive(root, asset)
            if name == 'mihomo':
                executable.write_bytes(gzip.decompress(source.read_bytes())); executable.chmod(0o755)
                run([executable, '-v'], stdout=sp.DEVNULL)
            else:
                with tarfile.open(source) as stream: stream.extractall(app, filter='data')
        link(app, root/'apps'/name/'current', root)
    write(root/'apps/metacubexd/current/config.js',
          'window.__METACUBEXD_CONFIG__ = {defaultBackendURL: window.location.origin};\n')


def refresh(root, update=False):
    import yaml
    folder = root/'config/mihomo'
    from mihomo_subscriptions import load, attach, save, prune
    records=load(folder,refresh=update)
    provider=records[0]['document']
    overrides = yaml.safe_load((folder/'overrides.yaml').read_text()) if (folder/'overrides.yaml').exists() else {}
    config = attach(compose(provider, root, overrides), records, folder)
    candidate = folder/'candidate.yaml'
    dump_yaml(candidate, config)
    from mihomo_rules import expanded
    expanded_path=folder/'expanded-candidate.yaml'
    dump_yaml(expanded_path,expanded(config))
    logpath = folder/'validation.log'
    with logpath.open('w') as log:
        logpath.chmod(0o600)
        for variant in (expanded_path,candidate):
            result = sp.run([root/'apps/mihomo/current/mihomo', '-t', '-d', folder, '-f', variant],
                            env=dict(os.environ, SAFE_PATHS=str(root/'apps')), stdout=log, stderr=sp.STDOUT, timeout=120)
            if result.returncode:
                raise RuntimeError('Mihomo rejected candidate; old configuration retained. See config/mihomo/validation.log')
    expanded_path.unlink()
    try: current = api(root)
    except OSError: current = None
    if current is not None:
        # Preserve deliberate dashboard choices on both install and update.
        config['mode'] = current['mode']; config['tun']['enable'] = current['tun']['enable']
    previous=yaml.safe_load((folder/'config.yaml').read_text()) if (folder/'config.yaml').exists() else {}
    if update:
        if current is not None:
            old = yaml.safe_load((folder/'config.yaml').read_text())
            old['mode'] = current['mode']; old['tun']['enable'] = current['tun']['enable']
            try:
                api(root, 'PUT', '/configs?force=true', {'payload': json.dumps(config, ensure_ascii=False)})
                actual = api(root)
                if actual['tun']['enable'] != config['tun']['enable']:
                    raise RuntimeError('TUN state did not apply')
            except Exception:
                api(root, 'PUT', '/configs?force=true', {'payload': json.dumps(old, ensure_ascii=False)})
                raise RuntimeError('Reload failed; previous runtime configuration restored') from None
    dump_yaml(candidate, config); candidate.replace(folder/'config.yaml')
    dump_yaml(folder/'provider.yaml', provider)
    save(folder,records);prune(folder,config,previous)
    print('Validated configuration:',len(records),'subscriptions,',sum(len(r['document']['proxies']) for r in records),'nodes,',len(config['rules']),'rules.')


def plist_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(data)); path.chmod(0o600)


def install_user_entries(root):
    label = LABEL + '.subscription'
    agent = Path.home()/'Library/LaunchAgents'/(label+'.plist')
    log = root/'logs/mihomo-subscription.log'
    log.parent.mkdir(parents=True, exist_ok=True);log.touch(mode=0o600, exist_ok=True)
    plist_write(agent, {'Label': label, 'ProgramArguments': [str(root/'bin/workbench'), 'mihomo', 'update'],
        'StartCalendarInterval': {'Hour': 4, 'Minute': 10}, 'ProcessType': 'Background',
        'StandardOutPath': str(log), 'StandardErrorPath': str(log)})
    domain = 'gui/'+str(os.getuid())
    sp.run(['launchctl', 'bootout', domain+'/'+label], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    run(['launchctl', 'bootstrap', domain, agent])
    app = Path.home()/'Applications/Mihomo.app/Contents'
    executable = app/'MacOS/mihomo-web'
    write(executable, '#!/bin/sh\nexec '+shlex.quote(str(root/'bin/workbench'))+' mihomo open\n', 0o755)
    plist_write(app/'Info.plist', {'CFBundleIdentifier': 'io.workbench.mihomo.dashboard', 'CFBundleName': 'Mihomo',
        'CFBundleDisplayName': 'Mihomo · MetaCubeXD', 'CFBundleExecutable': 'mihomo-web',
        'CFBundlePackageType': 'APPL', 'LSUIElement': True, 'CFBundleIconFile': 'AppIcon'})
    # Reuse the upstream dashboard's raster icon for the native launcher.
    icons = list((root/'apps/metacubexd/current').glob('*192*.png'))
    if icons:
        resources=app/'Resources';resources.mkdir(exist_ok=True)
        with __import__('tempfile').TemporaryDirectory() as temporary:
            iconset=Path(temporary)/'AppIcon.iconset';iconset.mkdir()
            for size in (16,32,128,256,512):
                for scale in (1,2):
                    target=iconset/f'icon_{size}x{size}{"@2x" if scale==2 else ""}.png'
                    run(['sips','-z',str(size*scale),str(size*scale),icons[0],'--out',target],stdout=sp.DEVNULL)
            run(['iconutil','-c','icns',iconset,'-o',resources/'AppIcon.icns'])


def prepare(root):
    folder=root/'config/mihomo';folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
    install_assets(root);import_verge(root)
    if (folder/'provider.yaml').exists() or (folder/'subscription.url').exists():refresh(root)
    else:print('Set config/mihomo/subscription.url (mode 600), then run workbench mihomo install again.')
    install_user_entries(root)
    print('Prepared Mihomo and MetaCubeXD. Run workbench mihomo apply to install the system daemon.')


def prepare_daemon(root):
    log=root/'logs/mihomo.log';log.parent.mkdir(parents=True,exist_ok=True);log.touch(mode=0o600,exist_ok=True)
    staged=root/'data/macos-network'/DAEMON.name
    plist_write(staged, {'Label': LABEL, 'ProgramArguments': [str(SYSTEM/'mihomo'), '-d',str(root/'config/mihomo'),
        '-f',str(root/'config/mihomo/config.yaml')], 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 10, 'Umask': 63, 'EnvironmentVariables': {'SAFE_PATHS': str(root/'apps')},
        'StandardOutPath': str(log), 'StandardErrorPath': str(log)})
    return staged


def clear_verge_system_proxy():
    """Remove only the old loopback endpoint after the TUN migration."""
    import yaml
    source=VERGE/'clash-verge.yaml'
    if not source.exists():return
    port=str((yaml.safe_load(source.read_text()) or {}).get('mixed-port',''))
    if not port:return
    services=run(['/usr/sbin/networksetup','-listallnetworkservices'],capture_output=True,text=True).stdout.splitlines()[1:]
    for service in services:
        if not service or service.startswith('*'):continue
        for kind in ('webproxy','securewebproxy','socksfirewallproxy'):
            result=run(['/usr/sbin/networksetup','-get'+kind,service],capture_output=True,text=True)
            fields=dict(line.split(': ',1) for line in result.stdout.splitlines() if ': ' in line)
            if fields.get('Enabled')=='Yes' and fields.get('Server') in ('127.0.0.1','localhost') and fields.get('Port')==port:
                run(['/usr/sbin/networksetup','-set'+kind+'state',service,'off'])


def apply(root):
    if not (root/'config/mihomo/config.yaml').exists():
        raise RuntimeError('No validated configuration. Run workbench mihomo install first.')
    if DAEMON.exists() and (SYSTEM/'mihomo').exists():
        installed=hashlib.sha256((SYSTEM/'mihomo').read_bytes()).digest()
        prepared=hashlib.sha256((root/'apps/mihomo/current/mihomo').read_bytes()).digest()
        if installed == prepared:
            try:
                api(root)
                print('Mihomo is already configured; use restart for an explicit restart.');return
            except OSError:pass
    staged=prepare_daemon(root)
    import yaml
    desired=yaml.safe_load((root/'config/mihomo/config.yaml').read_text())
    admin(['/bin/bash',REPO/'platforms/macos/network/mihomo-privileged.sh',root,staged,
           '1' if desired['tun']['enable'] else '0'])
    for _ in range(45):
        try:
            api(root);break
        except OSError:time.sleep(1)
    else:raise RuntimeError('Mihomo did not become ready; see logs/mihomo.log')
    selections=root/'config/mihomo/imported-selections.json'
    if selections.exists():
        skipped=0
        proxies=api(root,path='/proxies')['proxies']
        for selected in json.loads(selections.read_text()):
            name=selected.get('name');now=selected.get('now')
            if proxies.get(name,{}).get('type')=='Selector' and now in proxies[name].get('all',[]):
                api(root,'PUT','/proxies/'+urllib.parse.quote(name,safe=''),{'name':now})
            else:skipped+=1
        if skipped:print('Skipped',skipped,'stale/unavailable saved selections; group defaults retained.')
        selections.unlink()
    # Persist the old GUI's autostart preference only after successful activation.
    import yaml
    if (VERGE/'verge.yaml').exists():
        cfg=yaml.safe_load((VERGE/'verge.yaml').read_text());cfg['enable_auto_launch']=False
        dump_yaml(VERGE/'verge.yaml',cfg)
    if desired['tun']['enable']:clear_verge_system_proxy()
    print('Mihomo system daemon is ready; Clash Verge background service has been disabled.')


def tailscale_installed():
    if not TSBIN.exists():return False
    with (TSAPP/'Contents/Info.plist').open('rb') as f:info=plistlib.load(f)
    if info.get('CFBundleIdentifier')!='io.tailscale.ipn.macsys':
        raise RuntimeError('Existing Tailscale is not the official standalone variant; do not overwrite it automatically')
    result=sp.run(['codesign','-dv',TSAPP],capture_output=True,text=True)
    if result.returncode or 'TeamIdentifier=W5364U7YZB' not in result.stderr:
        raise RuntimeError('Unexpected Tailscale signing identity')
    verification=sp.run(['codesign','--verify','--deep','--strict',TSAPP],capture_output=True)
    if verification.returncode:raise RuntimeError('Existing Tailscale app signature verification failed')
    return True


def install_tailscale(root, update=False):
    installed=tailscale_installed()
    if installed and not update:
        print('Official Tailscale standalone app already installed; preserving login and VPN preferences.')
        return
    spec=read(REPO/'components/tailscale/component.toml')['install']['macos']
    with urllib.request.urlopen(spec['metadata'],timeout=30) as reply:metadata=json.load(reply)
    filename=metadata['MacZips']['universal-package']
    if '/' in filename or not filename.endswith('-macos.pkg'):raise RuntimeError('Unexpected official package filename')
    if installed:
        with (TSAPP/'Contents/Info.plist').open('rb') as f:version=plistlib.load(f)['CFBundleShortVersionString']
        if tuple(map(int,version.split('.'))) >= tuple(map(int,metadata['MacZipsVersion'].split('.'))):
            print('Tailscale is already at or above the official stable version.');return
    package=root/'downloads/archives'/filename;package.parent.mkdir(parents=True,exist_ok=True)
    from software import fetch
    fetch(spec['base_url']+filename,package)
    result=run(['pkgutil','--check-signature',package],capture_output=True,text=True)
    if 'Developer ID Installer: Tailscale Inc. ('+spec['team_id']+')' not in result.stdout:
        raise RuntimeError('Official Tailscale package signing identity mismatch')
    run(['spctl','--assess','--type','install',package],capture_output=True)
    admin(['/usr/sbin/installer','-pkg',package,'-target','/'])
    if not tailscale_installed():raise RuntimeError('Tailscale installation could not be verified')
    run(['open',TSAPP])
    print('Official PKG installed. Complete macOS network-extension approval/login if prompted.')


def status(root, service, check=False):
    issues=[]
    if service=='tailscale':
        if not tailscale_installed():issues.append('Official standalone Tailscale is not installed')
        else:
            result=sp.run([TSBIN,'status','--json'],capture_output=True,text=True,timeout=15)
            try:data=json.loads(result.stdout)
            except ValueError:data={};issues.append('Cannot read Tailscale status')
            print('Tailscale:',data.get('BackendState','unavailable'))
            issues.extend(data.get('Health') or [])
            if data.get('BackendState')=='NeedsLogin':issues.append('Open Tailscale to sign in')
    else:
        service_result=sp.run(['launchctl','print','system/'+LABEL],capture_output=True,text=True)
        if service_result.returncode:issues.append('LaunchDaemon is not loaded: workbench mihomo apply')
        try:
            cfg=api(root);print('Mihomo:',cfg['mode'],'TUN='+str(cfg['tun']['enable']))
            if cfg.get('external-controller') not in (None,'127.0.0.1:9090'):issues.append('Controller is not loopback-only')
        except (OSError,ValueError):issues.append('Mihomo controller is unavailable')
        if not (root/'apps/metacubexd/current/index.html').exists():issues.append('MetaCubeXD is missing')
        if sp.run(['launchctl','print',f'gui/{os.getuid()}/{LABEL}.subscription'],stdout=sp.DEVNULL,stderr=sp.DEVNULL).returncode:
            issues.append('Subscription LaunchAgent is not loaded')
    for issue in issues:print('PENDING:',issue)
    if not issues:print(service+': OK')
    return int(bool(issues)) if check else 0


def pending(root):
    issues=[]
    for service in ('mihomo','tailscale'):
        result=sp.run([sys.executable,__file__,'--prefix',str(root),service,'check'],capture_output=True,text=True)
        if result.returncode:issues.append(service+': run workbench '+service+' check')
    return issues


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prefix',type=Path,default=ROOT)
    p.add_argument('service',choices=['mihomo','tailscale']);p.add_argument('action',choices=['install','update','apply','start','stop','restart','status','check','logs','open'])
    a=p.parse_args();root=a.prefix.resolve();os.umask(0o077)
    if platform.system()!='Darwin':p.error('This backend requires macOS')
    if a.service=='tailscale':
        if a.action in ('install','update'):install_tailscale(root,a.action=='update')
        elif a.action in ('status','check'):return status(root,a.service,a.action=='check')
        elif a.action in ('apply','open'):run(['open',TSAPP])
        elif a.action=='start':run([TSBIN,'up'])
        elif a.action=='stop':run([TSBIN,'down'])
        elif a.action=='restart':run([TSBIN,'down']);run([TSBIN,'up'])
        elif a.action=='logs':run(['log','show','--last','10m','--style','compact','--predicate','subsystem BEGINSWITH "io.tailscale"'])
        return 0
    if a.action in ('install','apply','update') and not ensure_yaml(root,a.service,a.action):return 0
    if a.action=='install':prepare(root)
    elif a.action=='apply':apply(root)
    elif a.action=='update':refresh(root,True)
    elif a.action in ('status','check'):return status(root,a.service,a.action=='check')
    elif a.action=='open':
        query=urllib.parse.urlencode({'hostname':'127.0.0.1','port':'9090','http':'true'})
        run(['open','http://127.0.0.1:9090/ui/?'+query])
    elif a.action=='logs':run(['tail','-n','80',root/'logs/mihomo.log'])
    elif a.action=='start':
        if sp.run(['launchctl','print','system/'+LABEL],stdout=sp.DEVNULL,stderr=sp.DEVNULL).returncode:
            admin(['/bin/launchctl','bootstrap','system',DAEMON])
    elif a.action=='stop':admin(['/bin/launchctl','bootout','system/'+LABEL])
    elif a.action=='restart':
        loaded=sp.run(['launchctl','print','system/'+LABEL],stdout=sp.DEVNULL,stderr=sp.DEVNULL).returncode==0
        admin(['/bin/launchctl','kickstart','-k','system/'+LABEL] if loaded else ['/bin/launchctl','bootstrap','system',DAEMON])
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,sp.SubprocessError) as error:
        # HTTP errors can contain subscription URLs; never print their repr.
        print('ERROR:',str(error) if isinstance(error,RuntimeError) else type(error).__name__,file=sys.stderr)
        sys.exit(1)
