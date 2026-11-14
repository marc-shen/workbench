#!/usr/bin/env python3
"""Internal Steam Deck network backend for workbench network ACTION SERVICE."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import shlex
import shutil
import subprocess as sp
import sys
import tempfile
from config_data import network_sources

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / 'platforms/steamdeck/network'
SERVICES = ('mihomo', 'tailscale')
ACTIONS = ('prepare', 'activate', 'update', 'start', 'stop', 'restart', 'status', 'check', 'open', 'logs')
CAPS = ('cap_net_admin', 'cap_net_raw', 'cap_net_bind_service', 'cap_sys_ptrace', 'cap_dac_read_search')


def is_steamdeck():
    p = Path('/etc/os-release')
    return p.exists() and any(line.startswith('ID=') and line.split('=', 1)[1].strip('"\'') == 'steamos'
                              for line in p.read_text().splitlines())


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def run(args, **kwargs):
    return sp.run(list(map(str, args)), check=True, **kwargs)


def text(args):
    return sp.check_output(list(map(str, args)), text=True, stderr=sp.DEVNULL).strip()


def write(path, content, mode=0o644):
    """Replace a managed text file only when different; return whether changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text() == content:
        path.chmod(mode)
        return False
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(content); temporary.chmod(mode); temporary.replace(path)
    return True


def link(source, target, root):
    # Shared helper preserves replaced files/links in Software/backups.
    from software import atomic_link
    atomic_link(source, target, root)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive(root, spec, candidates=()):
    from software import fetch
    path = root / 'downloads/archives' / spec['url'].rsplit('/', 1)[1]
    if path.exists() and digest(path) == spec['sha256']:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        if candidate.is_file() and digest(candidate) == spec['sha256']:
            shutil.copy2(candidate, path)
            return path
    fetch(spec['url'], path)
    if digest(path) != spec['sha256']:
        raise RuntimeError('SHA256 mismatch: ' + str(path))
    return path


def install_tailscale(root):
    upstream = network_sources(REPO)['deck-tailscale']; checkout = root / 'apps/deck-tailscale' / upstream['revision']
    if not (checkout / '.git').exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='deck-tailscale-', dir=checkout.parent) as staging:
            run(['git', 'init', '-q', staging])
            run(['git', '-C', staging, 'fetch', '-q', '--depth', '1', upstream['repository'], upstream['revision']])
            run(['git', '-C', staging, 'checkout', '-q', '--detach', 'FETCH_HEAD'])
            Path(staging).rename(checkout)
    if text(['git', '-C', checkout, 'rev-parse', 'HEAD']) != upstream['revision']:
        raise RuntimeError('Unexpected deck-tailscale checkout')
    run(['git', '-C', checkout, 'diff', '--exit-code', 'HEAD', '--', 'tailscale.sh', 'override.conf'], stdout=sp.DEVNULL)
    link(checkout, root / 'apps/deck-tailscale/current', root)


def tailscale_ip():
    binary = Path('/opt/tailscale/tailscale')
    if binary.exists():
        try:
            for line in text([binary, 'ip', '-4']).splitlines():
                ip = ipaddress.ip_address(line)
                if ip in ipaddress.ip_network('100.64.0.0/10'):
                    return str(ip)
        except (ValueError, sp.CalledProcessError):
            pass
    return '127.0.0.1'


def dashboard_url():
    address = '127.0.0.1'  # Local UI must not depend on the virtual network being online.
    return f'http://{address}:9090/ui/?hostname={address}&port=9090&http=true'


def deploy(root, services=SERVICES):
    root.mkdir(parents=True, exist_ok=True)
    link(REPO, root / 'config/workbench', root)
    desktop = Path.home() / '.local/share/applications'
    entries = {
        'mihomo': ('mihomo-web.desktop', 'Mihomo - MetaCubeXD', root / 'apps/metacubexd/current/favicon.svg', ''),
    }
    for service in services:
        if service not in entries: continue
        filename, name, icon, wmclass = entries[service]
        command = '"' + str(root / 'bin/workbench') + '" ' + service + ' open'
        write(desktop / filename, f'[Desktop Entry]\nType=Application\nName={name}\nExec={command}\nIcon={icon}\nTerminal=false\nCategories=Network;\n'
              + (f'StartupWMClass={wmclass}\n' if wmclass else ''))


def retire_legacy(root, services):
    # Called only after desktop entries and the subscription timer are migrated.
    names = ['deck-network'] if 'mihomo' in services else []
    if 'mihomo' in services: names.extend(('mihomo-deck', 'deck-mihomo'))
    for name in names:
        for folder in (root / 'bin', root / 'config/network-launchers'):
            (folder / name).unlink(missing_ok=True)


def refresh_desktop_cache():
    for command in ('update-desktop-database', 'kbuildsycoca6', 'kbuildsycoca5'):
        binary = shutil.which(command)
        if binary:
            args = [binary, str(Path.home() / '.local/share/applications')] if command == 'update-desktop-database' else [binary, '--noincremental']
            sp.run(args, check=True, stdout=sp.DEVNULL)
            if command.startswith('kbuildsycoca'):break


def service_command(service):
    return ['systemctl', '--user'] if service == 'mihomo' else ['systemctl']


def service_unit(service):
    return {'mihomo': 'mihomo.service', 'tailscale': 'tailscaled.service'}[service]


def service_pending(root, service):
    issues = []
    def require(command, label):
        if sp.run(command, stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode:
            issues.append(label)
    require(service_command(service) + ['is-active', '--quiet', service_unit(service)], service + ' service')
    if service == 'mihomo':
        require(['systemctl','--user','is-enabled','--quiet','mihomo'], 'Mihomo autostart')
        for action in ('is-active', 'is-enabled'):
            require(['systemctl','--user',action,'--quiet','mihomo-subscription-update.timer'], 'Daily subscription timer ' + action)
        require(['systemctl','is-active','--quiet','profile-mihomo-firewall'], 'Mihomo Tailscale firewall')
        core = root / 'apps/mihomo/current/mihomo'
        caps = text(['getcap', core]) if core.exists() and shutil.which('getcap') else ''
        if not all(c in caps for c in CAPS): issues.append('Mihomo privileges: workbench mihomo apply')
        firewall = Path('/etc/profile-mihomo/firewall.nft')
        if not firewall.exists() or 'tcp dport 9090 drop' not in firewall.read_text():
            issues.append('Mihomo 9090 firewall: workbench mihomo apply')
        if not (root / 'config/mihomo/config.yaml').exists(): issues.append('Local Clash subscription/configuration')
        if not Path('/var/lib/systemd/linger', pwd.getpwuid(os.getuid()).pw_name).exists(): issues.append('User linger')
        from network_diagnostics import mihomo_issues
        issues.extend(mihomo_issues())
    elif service == 'tailscale':
        if not Path('/opt/tailscale/tailscale').is_file(): issues.append('Tailscale executable')
        if not Path('/etc/atomic-update.conf.d/90-deck-tailscale-persistence.conf').exists():
            issues.append('Tailscale persistence: workbench tailscale apply')
        from network_diagnostics import tailscale_issues
        issues.extend(tailscale_issues())
    return issues


def pending(root):
    return [issue for service in SERVICES for issue in service_pending(root, service)]


def authorize(root, update=False):
    if os.environ.get('SSH_CONNECTION'):
        raise RuntimeError('Run this command in the Deck local Konsole; upstream may restart your SSH network')
    checkout = root / 'apps/deck-tailscale/current'
    upstream = network_sources(REPO)['deck-tailscale']
    if text(['git', '-C', checkout, 'rev-parse', 'HEAD']) != upstream['revision']:
        raise RuntimeError('Run workbench tailscale install to prepare the pinned installer')
    run(['git', '-C', checkout, 'diff', '--exit-code', 'HEAD', '--', 'tailscale.sh', 'override.conf'], stdout=sp.DEVNULL)
    run(['sudo', '/bin/bash', TEMPLATES / 'tailscale-privileged.sh', root, checkout,
         pwd.getpwuid(os.getuid()).pw_name, 'update-tailscale' if update else 'prepare'])
    if not update:
        result = sp.run(['/opt/tailscale/tailscale', 'status', '--json'], capture_output=True, text=True)
        if json.loads(result.stdout).get('BackendState') == 'NeedsLogin':
            run(['sudo', '/opt/tailscale/tailscale', 'up'])
    print('Tailscale ready. Use workbench tailscale status or open.')


def preflight(services, action):
    # Check the complete batch before doing any work: stopping Tailscale can cut SSH.
    if os.environ.get('SSH_CONNECTION'):
        needs_local = action == 'activate' or ('tailscale' in services and action in ('update', 'stop', 'restart'))
        if needs_local:
            raise RuntimeError('Run this operation in the Deck local Konsole; it needs administrator setup or can interrupt Tailscale SSH.')


def prepare_service(root, service):
    if service == 'tailscale': install_tailscale(root)
    else:
        from mihomo_deck import prepare
        prepare(root)


def dispatch(root, service, action):
    if action == 'prepare':
        prepare_service(root, service)
    elif action == 'activate':
        if service == 'mihomo':
            from mihomo_deck import activate
            activate(root)
        elif service == 'tailscale': authorize(root)
    elif action == 'update':
        if service == 'mihomo':
            from mihomo_deck import daily_update
            daily_update(root)
        elif service == 'tailscale':
            install_tailscale(root)
            authorize(root, update=True)
    elif action in ('start', 'stop', 'restart'):
        command = service_command(service) + [action, service_unit(service)]
        run((['sudo'] if service != 'mihomo' else []) + command)
    elif action == 'status':
        run(service_command(service) + ['show', service_unit(service), '-p', 'ActiveState', '-p', 'SubState', '-p', 'UnitFileState'])
        if service == 'mihomo':
            from mihomo_network import status as network_status
            print(network_status())
            recovery = load(root/'config/mihomo/group-recovery.json', {})
            if isinstance(recovery, dict):
                paused = [name for name, record in recovery.items() if isinstance(record, dict) and record.get('blocked')]
                print('Groups waiting for recovery: ' + (', '.join(paused) if paused else 'none'))
            run(['systemctl','--user','list-timers','mihomo-subscription-update.timer','--no-pager'])
        return sp.run(service_command(service) + ['is-active','--quiet',service_unit(service)]).returncode
    elif action == 'check':
        issues = service_pending(root, service)
        for issue in issues: print('Pending:', issue)
        if not issues: print(service + ': OK')
        return 2 if issues else 0
    elif action == 'open':
        run(['xdg-open', dashboard_url() if service == 'mihomo' else 'https://login.tailscale.com/admin/machines'])
    elif action == 'logs':
        command = ['journalctl'] + (['--user'] if service == 'mihomo' else []) + ['-u',service_unit(service),'-n','50','--no-pager']
        if service == 'mihomo': command += ['-u','mihomo-subscription-update.service']
        run(command)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog='workbench network backend', description=__doc__,
        epilog='prepare: install pinned clients/config; activate: initial local authorization; update: refresh managed content; start/stop/restart: backend service; status/check/logs: inspect; open: GUI/web console. Mihomo update refreshes the subscription; Tailscale update installs upstream stable.')
    parser.add_argument('--prefix', type=Path, default=Path(os.environ.get('SOFTWARE_HOME', str(Path.home() / 'Software'))))
    sub = parser.add_subparsers(dest='service', required=True)
    sub.add_parser('list', help='List services and supported actions without changing anything')
    for service in ('all',) + SERVICES:
        command = sub.add_parser(service, help='All network services' if service == 'all' else service + ' management')
        command.add_argument('action', choices=ACTIONS, help='Supported network service actions')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.service == 'list':
        for service in SERVICES:
            print(service + ': ' + ' '.join({'prepare': 'install', 'activate': 'apply'}.get(a, a) for a in ACTIONS))
        return 0
    if not is_steamdeck() or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise RuntimeError('Run as the regular user on an x86_64 Steam Deck')
    root = args.prefix.expanduser().absolute()
    services = SERVICES if args.service == 'all' else (args.service,)
    preflight(services, args.action)
    # Network comes up before the proxy and goes down after it.
    if args.service == 'all':
        services = ('tailscale', 'mihomo') if args.action in ('start', 'restart') else SERVICES
        if args.action == 'stop': services = ('mihomo', 'tailscale')
        if args.action == 'activate': services = ('mihomo', 'tailscale')
        if args.action == 'open': services = ('mihomo', 'tailscale')
    if args.action == 'prepare': deploy(root, services)
    result = 0
    for service in services:
        print('[' + service + '] ' + args.action, flush=True)
        result = max(result, dispatch(root, service, args.action) or 0)
    if args.action == 'prepare':
        deploy(root, services)
        refresh_desktop_cache()
        if args.action == 'prepare': retire_legacy(root, services)
    return result


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, sp.CalledProcessError, OSError, ValueError) as error:
        print('workbench network:', str(error) if isinstance(error, RuntimeError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
