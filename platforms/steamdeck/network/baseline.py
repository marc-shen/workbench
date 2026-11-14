#!/usr/bin/env python3
"""One-time root setup for native DNS; 302 is managed by its official GUI."""
import argparse
import os
from pathlib import Path
import subprocess as sp


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.workbench-new')
    temporary.write_text(content)
    temporary.chmod(mode)
    temporary.replace(path)


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
    parser.parse_args()
    if os.geteuid() != 0 or '\nID=steamos\n' not in '\n' + Path('/etc/os-release').read_text():
        parser.error('run as root on SteamOS')
    native_dns()


if __name__ == '__main__':
    main()
