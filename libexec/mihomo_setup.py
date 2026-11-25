"""Subscription-free dashboard bootstrap, shared by macOS and Steam Deck."""
import fcntl
from functools import wraps
import os

PENDING = 'pending-subscription'


def configuration_lock(function):
    @wraps(function)
    def locked(root, *args, **kwargs):
        folder = root / 'config/mihomo'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(folder / '.configuration.lock', 'a', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            return function(root, *args, **kwargs)
    return locked


def has_subscription(folder):
    source = folder / 'subscription.url'
    return source.exists() and any(line.strip() and not line.lstrip().startswith('#')
                                   for line in source.read_text().splitlines())


def prepare_subscription_file(folder):
    from deck_network import write
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    source = folder / 'subscription.url'
    if not source.exists():
        write(source, '# 每行一个 Clash YAML 订阅链接；保存后运行 wb mihomo update。\n', 0o600)
    else:
        source.chmod(0o600)


def load_optional(folder, refresh=False):
    from mihomo_subscriptions import load
    if has_subscription(folder) or any((folder / name).exists() for name in ('provider.yaml', 'subscriptions.json')):
        return load(folder, refresh=refresh and has_subscription(folder))
    if (folder / 'config.yaml').exists() and not (folder / PENDING).exists():
        raise RuntimeError('Existing Mihomo configuration has no subscription source; restore subscription.url or provider.yaml.')
    return []


def controller(root, address='127.0.0.1', listen='127.0.0.1:9090'):
    return {'external-controller': listen, 'external-ui': str(root / 'apps/metacubexd/current'),
            'secret': '', 'external-controller-cors': {
                'allow-origins': list(dict.fromkeys(['http://127.0.0.1:9090', 'http://localhost:9090',
                                                     f'http://{address}:9090'])),
                'allow-private-network': True}}


def pending_config(root, address='127.0.0.1', listen='127.0.0.1:9090'):
    # No TUN, DNS interception, probes, provider downloads or imported overrides.
    return {**controller(root, address, listen), 'mixed-port': 2080, 'allow-lan': False,
            'bind-address': '127.0.0.1', 'mode': 'direct', 'log-level': 'info',
            'tun': {'enable': False}, 'dns': {'enable': False}, 'geo-auto-update': False,
            'proxies': [], 'proxy-groups': [{'name': '待配置订阅', 'type': 'select', 'proxies': ['DIRECT']}],
            'rules': ['MATCH,DIRECT']}


def mark_subscription_state(folder, records):
    from deck_network import write
    marker = folder / PENDING
    if records:
        marker.unlink(missing_ok=True)
    else:
        write(marker, 'Waiting for subscription.url\n', 0o600)
        print('Mihomo 已安装，当前为直连待配置模式（TUN 关闭）。')
        print('请编辑 ' + str(folder / 'subscription.url') + '，然后运行 wb mihomo update。')
