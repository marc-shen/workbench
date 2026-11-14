"""Runtime checks, including native DNS and Steam container certificate trust."""
import hashlib
import json
from pathlib import Path
import subprocess as sp
import time
from mihomo_runtime import API


def dns_issues():
    if Path('/etc/resolv.conf').resolve() != Path('/run/systemd/resolve/stub-resolv.conf'):
        return ['DNS must use the systemd-resolved stub: workbench mihomo apply']
    return []


def tailscale_issues():
    issues = dns_issues()
    try:
        result = sp.run(['/opt/tailscale/tailscale', 'status', '--json'], capture_output=True, text=True, timeout=8)
        data = json.loads(result.stdout)
        if data.get('BackendState') != 'Running':
            issues.append('Tailscale is not logged in/running')
        issues += ['Tailscale: ' + msg for msg in data.get('Health') or []]
    except (OSError, ValueError, sp.TimeoutExpired):
        issues.append('Cannot read Tailscale health')
    return issues


def steam_tls_issues():
    bundles = {}
    issues = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process/'comm').read_text().strip() not in ('steam', 'steamwebhelper'):
                continue
            if '#S302' in (process/'root/etc/hosts').read_text():
                issues.append('Steam container still has 302 hosts entries; restart Steam after returning 302 to standby')
            ca = process/'root/etc/ssl/certs/ca-certificates.crt'
            bundles.setdefault(hashlib.sha256(ca.read_bytes()).digest(), ca)
        except OSError:
            pass
    for ca in bundles.values():
        for host in ('login.steampowered.com', 'steamcommunity.com'):
            result = sp.run(['curl', '--noproxy', '*', '--cacert', str(ca), '-sS',
                '--connect-timeout', '3', '--max-time', '5', '-o', '/dev/null', 'https://' + host + '/'],
                capture_output=True, timeout=7)
            if result.returncode:
                issues.append(f'Steam runtime HTTPS failed: {host} (curl {result.returncode})')
    return list(dict.fromkeys(issues))


def mihomo_issues():
    from mihomo_network import state_path
    issues = dns_issues()
    try:
        config = API().request('GET', '/configs')
        state = json.loads(state_path().read_text())
        tun = Path('/sys/class/net/mihomo-tun').exists()
        if bool(config['tun']['enable']) != tun:
            issues.append('TUN API state disagrees with the actual interface')
        if state['phase'] in ('failed', 'stopped', 'routing-pending'):
            issues.append('Network supervisor: ' + state['phase'])
        if state['phase'] in ('offline', 'portal', 'limited') and tun:
            issues.append('Standby network unexpectedly has an active TUN')
        if state['phase'] in ('online', 'manual'):
            issues.extend(steam_tls_issues())
    except (OSError, ValueError, KeyError, sp.TimeoutExpired):
        issues.append('Controller or network supervisor state is unavailable')
    return issues


def controller_ready():
    """A working standby controller is healthy even without a physical network."""
    from mihomo_network import state_path
    config = API().request('GET', '/configs', timeout=2)
    state = json.loads(state_path().read_text())
    phase = state['phase']
    tun = bool(config['tun']['enable'])
    if tun != Path('/sys/class/net/mihomo-tun').exists():
        return False
    if phase in ('offline', 'portal', 'limited') or (
            phase == 'checking' and state['reason'] == 'networkmanager-unavailable'):
        return config['mode'] == 'direct' and not tun
    if phase == 'online':
        return config['mode'] == state['desired']['mode'] and tun == state['desired']['tun']['enable']
    return False


def wait_ready(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if controller_ready():
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(1)
    raise RuntimeError('Mihomo controller/routing did not become healthy')
