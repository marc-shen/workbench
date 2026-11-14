#!/usr/bin/env python3
"""One-time root setup: native DNS and optional, disabled-by-default 302."""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess as sp


def without_302_hosts(content):
    # Only remove entries explicitly owned by 302, preserving unrelated hosts.
    return ''.join(line for line in content.splitlines(keepends=True)
                   if '#S302' not in line)


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.workbench-new')
    temporary.write_text(content)
    temporary.chmod(mode)
    temporary.replace(path)


def standby_302(uid):
    unit = f's302-{uid}.service'
    if sp.run(['systemctl', 'cat', unit], stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode == 0:
        sp.run(['systemctl', 'disable', '--now', unit], check=True)
    config = Path('/var/lib/Steamcommunity_302/config.json')
    if config.exists():
        data = json.loads(config.read_text())
        settings = data['settings']
        settings.setdefault('hosts', {})['autoModify'] = False
        settings.setdefault('dns', {})['enabled'] = False
        write(config, json.dumps(data, ensure_ascii=False, indent=2) + '\n', config.stat().st_mode & 0o777)
    hosts = Path('/etc/hosts')
    cleaned = without_302_hosts(hosts.read_text())
    if cleaned != hosts.read_text():
        write(hosts, cleaned, hosts.stat().st_mode & 0o777)
    print('302 retained as a manual backup; autostart and managed hosts disabled.')


def native_dns():
    sp.run(['systemctl', 'enable', '--now', 'systemd-resolved'], check=True)
    config = Path('/etc/NetworkManager/conf.d/90-workbench-dns.conf')
    write(config, '[main]\ndns=systemd-resolved\nrc-manager=symlink\n')
    target = Path('/run/systemd/resolve/stub-resolv.conf')
    if not target.exists():
        raise RuntimeError('systemd-resolved stub is not available')
    link = Path('/etc/resolv.conf.workbench-new')
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    link.replace('/etc/resolv.conf')
    sp.run(['nmcli', 'general', 'reload', 'conf'], check=True)
    sp.run(['resolvectl', 'flush-caches'], check=True)
    write(Path('/etc/atomic-update.conf.d/92-workbench-network.conf'),
          '/etc/atomic-update.conf.d/92-workbench-network.conf\n'
          '/etc/NetworkManager/conf.d/90-workbench-dns.conf\n/etc/resolv.conf\n')
    print('DNS uses the resolved stub; NetworkManager and Tailscale were not restarted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--only-302', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or '\nID=steamos\n' not in '\n' + Path('/etc/os-release').read_text():
        parser.error('run as root on SteamOS')
    uid = pwd.getpwnam(args.user).pw_uid
    if uid == 0:
        parser.error('a regular Steam Deck user is required')
    standby_302(uid)
    if not args.only_302:
        native_dns()


if __name__ == '__main__':
    main()
