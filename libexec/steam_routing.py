"""Native HTTPS Steam routing, independent of game node selection."""
from config_data import read as read_toml

STEAM_GROUP = '🎮 Steam平台'
STEAM_FALLBACK = '🎮 Steam自动回退'


def configure(config, root):
    groups = {g['name']: g for g in config['proxy-groups']}
    if '🎮 游戏平台' not in groups:
        return []  # Do not invent a vendor's domain classification.
    policy = root / 'config/mihomo/steam-platform.toml'
    settings = read_toml(policy) if policy.exists() else {}
    if settings and (settings.get('schema') != 1 or set(settings) - {'schema', 'source', 'preferred', 'max_nodes'}):
        raise ValueError('Invalid steam-platform.toml')
    source = settings.get('source') or next((n for n in groups if '香港' in n), None)
    if source not in groups:
        raise ValueError('Steam platform needs an explicit source group in steam-platform.toml')
    nodes = {p['name'] for p in config['proxies']}
    members = groups[source].get('proxies', [])
    if not members or any(n not in nodes for n in members):
        raise ValueError('Steam source must contain explicit nodes, not nested groups')
    limit = settings.get('max_nodes', 4)
    preferred = settings.get('preferred', [])
    if type(limit) is not int or not 2 <= limit <= 8 or not isinstance(preferred, list) or any(not isinstance(n, str) for n in preferred):
        raise ValueError('Steam platform needs 2..8 nodes and a preferred string list')
    members = list(dict.fromkeys([n for n in preferred if n in members] + members))[:limit]
    if STEAM_GROUP in groups or STEAM_FALLBACK in groups:
        raise ValueError('Subscription conflicts with managed Steam group names')
    config['proxy-groups'] += [
        {'name': STEAM_FALLBACK, 'type': 'fallback', 'proxies': members,
         'url': 'https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/',
         'expected-status': '200', 'interval': 180, 'timeout': 5000, 'lazy': True},
        {'name': STEAM_GROUP, 'type': 'select', 'proxies': [STEAM_FALLBACK, 'DIRECT', 'REJECT']},
    ]
    return ['DOMAIN-SUFFIX,steamconnecttest.com,DIRECT',
            'GEOSITE,steam@cn,DIRECT',
            'GEOSITE,steam,' + STEAM_GROUP,
            r'DOMAIN-REGEX,^steamcloud(?:-[a-z0-9-]+)?\.storage\.googleapis\.com$,' + STEAM_GROUP]
