"""Keep a live dashboard while switching between standby and normal routing."""
import copy
import json
import os
from pathlib import Path
import urllib.request


def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(data, stream, ensure_ascii=False)
    temporary.chmod(0o600)
    temporary.replace(path)


def standby_config(config, folder):
    result = copy.deepcopy(config)
    result['mode'] = 'direct'
    result.setdefault('tun', {})['enable'] = False
    result['geo-auto-update'] = False
    result.setdefault('ntp', {})['enable'] = False
    # Preserve names and choices in the offline dashboard, with no automatic tests.
    for group in result.get('proxy-groups', []):
        if group.get('type') in ('fallback', 'url-test', 'load-balance'):
            group['type'] = 'select'
        group.pop('url', None)
        group['interval'] = 0
        group['lazy'] = True
    for kind in ('proxy-providers', 'rule-providers'):
        for provider in result.get(kind, {}).values():
            provider['interval'] = 0
            if kind == 'proxy-providers':
                provider['health-check'] = {'enable': False, 'url': '', 'interval': 0, 'lazy': True}
            if provider.get('type') == 'http':
                cached = folder / provider.get('path', '__missing_cache__')
                if cached.is_file():
                    provider['type'] = 'file'
                    provider['path'] = str(cached)
                else:
                    # No remote fetch is allowed in standby, even on a fresh installation.
                    provider['type'] = 'inline'
                    provider['payload'] = []
                provider.pop('url', None)
    return result


class API:
    def __init__(self, endpoint='http://127.0.0.1:9090'):
        self.endpoint = endpoint
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, data=None, timeout=5):
        request = urllib.request.Request(self.endpoint + path, method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=timeout) as reply:
            body = reply.read()
            return json.loads(body) if body else None


class Runtime:
    def __init__(self, config, folder, api=None):
        self.config, self.folder = config, Path(folder)
        self.api = api or API()
        self.online = False
        self.expected = {'mode': 'direct', 'tun': {'enable': False}}
        self.desired = {'mode': config.get('mode', 'rule'),
                        'tun': {'enable': config.get('tun', {}).get('enable', False)}}
        self.intent = self.folder / 'network-intent.json'
        try:
            saved = json.loads(self.intent.read_text())
            if saved['mode'] in ('rule', 'global', 'direct') and type(saved['tun']['enable']) is bool:
                self.desired = saved
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def initial_config(self):
        return standby_config(self.config, self.folder)

    def current(self):
        config = self.api.request('GET', '/configs')
        return {'mode': config['mode'], 'tun': {'enable': config['tun']['enable']}}

    def remember(self, state):
        if state != self.desired or not self.intent.exists():
            self.desired = state
            private_json(self.intent, state)

    def load(self, config):
        self.api.request('PUT', '/configs?force=true', {'payload': json.dumps(config)}, timeout=30)
        self.expected = {'mode': config['mode'], 'tun': {'enable': config['tun']['enable']}}
        actual = self.current()
        if actual != self.expected:
            raise RuntimeError('core did not apply requested mode/TUN')
        if config['tun']['enable'] and config['tun'].get('device') == 'mihomo-tun':
            if not Path('/sys/class/net/mihomo-tun').exists():
                raise RuntimeError('core did not create TUN')

    def standby(self):
        if self.online:
            self.remember(self.current())
            self.load(self.initial_config())
            self.online = False
            self.api.request('DELETE', '/connections')

    def activate(self):
        if not self.online:
            config = copy.deepcopy(self.config)
            config['mode'] = self.desired['mode']
            config['tun']['enable'] = self.desired['tun']['enable']
            try:
                self.load(config)
            except (OSError, ValueError, RuntimeError):
                # A partially applied config must not leave a broken TUN behind.
                self.load(self.initial_config())
                raise
            self.online = True

    def observe_manual(self):
        actual = self.current()
        if actual != self.expected:
            desired = copy.deepcopy(self.desired)
            for key in actual:
                if actual[key] != self.expected[key]:
                    desired[key] = actual[key]
            self.remember(desired)
            if not self.online:
                # Save dashboard intent, but do not let it capture a captive portal.
                self.load(self.initial_config())
            else:
                self.expected = actual
        elif self.online:
            self.remember(actual)
