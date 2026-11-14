"""Circuit breaking for explicitly scoped fallback groups; no global DIRECT fallback."""
import datetime as dt
import json
from pathlib import Path
import threading
import time
from urllib.parse import quote, urlencode
from mihomo_runtime import private_json


def fresh_failure(proxy, url, now, max_age):
    record = proxy.get('extra', {}).get(url, {})
    history = record.get('history', [])
    if record.get('alive') is not False or not history:
        return False
    try:
        tested = dt.datetime.fromisoformat(history[-1]['time'].replace('Z', '+00:00')).timestamp()
        return 0 <= now - tested <= max_age and history[-1]['delay'] == 0
    except (KeyError, ValueError, TypeError):
        return False


class GroupHealth:
    def __init__(self, api, config, folder):
        self.api = api
        self.file = Path(folder) / 'group-recovery.json'
        nodes = {p['name'] for p in config.get('proxies', [])}
        self.groups = {g['name']: g for g in config.get('proxy-groups', [])
                       if g.get('type') == 'fallback' and g.get('proxies')
                       and all(p in nodes for p in g['proxies'])}
        self.wrappers = {g['name']: g for g in config.get('proxy-groups', [])
                         if g.get('type') == 'select' and 'REJECT' in g.get('proxies', [])}
        self.records = {}
        try:
            self.records = {k: v for k, v in json.loads(self.file.read_text()).items()
                            if k in self.wrappers and isinstance(v, dict) and v.get('group') in self.groups and v.get('blocked')}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.lock = threading.Lock()
        self.allowed = False
        self.worker = None
        self.next_tick = 0
        self.last_saved = None

    def save(self):
        # Node/group names stay in the local private file, never in logs or Git.
        data = {k: v for k, v in self.records.items() if v.get('blocked')}
        serialized = json.dumps(data, sort_keys=True)
        if serialized != self.last_saved:
            private_json(self.file, data)
            self.last_saved = serialized

    def select(self, wrapper, name):
        self.api.request('PUT', '/proxies/' + quote(wrapper, safe=''), {'name': name})

    def close_connections(self, wrapper):
        for connection in self.api.request('GET', '/connections').get('connections') or []:
            if wrapper in connection.get('chains', []):
                self.api.request('DELETE', '/connections/' + quote(connection['id'], safe=''))

    def test(self, name, url):
        try:
            result = self.api.request('GET', '/proxies/' + quote(name, safe='') + '/delay?' +
                urlencode({'url': url, 'timeout': 3000}), timeout=5)
            return isinstance(result.get('delay'), (float, int)) and result['delay'] > 0
        except (OSError, ValueError, KeyError):
            return False

    def tick(self, now=None):
        now = time.time() if now is None else now
        proxies = self.api.request('GET', '/proxies')['proxies']
        for wrapper in self.wrappers:
            if not self.allowed:
                return
            chosen = proxies.get(wrapper, {}).get('now')
            record = self.records.get(wrapper, {})
            if record.get('blocked'):
                if chosen != 'REJECT':
                    self.records.pop(wrapper, None)  # User choice always wins.
                    continue
                if now < record.get('next', 0):
                    continue
                group = self.groups[record['group']]
                members = group['proxies']
                cursor = record.get('cursor', 0) % len(members)
                candidates = [record['candidate']] if record.get('candidate') in members else (
                    [members[(cursor + i) % len(members)] for i in range(min(3, len(members)))])
                good = next((n for n in candidates if self.allowed and self.test(n, group['url'])), None)
                if not self.allowed:
                    return
                if good and good == record.get('candidate'):
                    self.select(wrapper, record['group'])
                    self.records.pop(wrapper, None)
                    print('Mihomo recovery: a scoped group recovered after two successful checks.', flush=True)
                else:
                    record['candidate'] = good
                    record['cursor'] = cursor + 3
                    step = record.get('step', 0)
                    record['next'] = now + (60 if good else (60, 120, 300)[min(step, 2)])
                    record['step'] = step + 1
                continue
            if chosen not in self.groups:
                self.records.pop(wrapper, None)
                continue
            group = self.groups[chosen]
            all_failed = all(fresh_failure(proxies.get(n, {}), group['url'], now,
                                          max(60, group.get('interval', 180) * 2)) for n in group['proxies'])
            if not all_failed:
                self.records.pop(wrapper, None)
                continue
            if record.get('group') != chosen:
                self.records[wrapper] = {'group': chosen, 'first_failure': now}
                continue
            if now - record['first_failure'] < 30:
                continue
            # Do not reject a whole group merely because Steam's test endpoint failed.
            if any(self.test(n, 'https://www.gstatic.com/generate_204') for n in group['proxies'][:2]):
                record['first_failure'] = now
                continue
            if not self.allowed:
                return
            record.update(blocked=True, next=now + 60, step=1, cursor=0, candidate=None)
            self.save()  # Recovery intent survives a crash between selection and next tick.
            self.select(wrapper, 'REJECT')
            self.close_connections(wrapper)
            print('Mihomo recovery: a failed scoped group is paused; retrying with backoff.', flush=True)
        self.save()

    def schedule(self):
        self.allowed = True
        if self.worker is not None and self.worker.is_alive():
            return
        if time.monotonic() < self.next_tick:
            return
        self.next_tick = time.monotonic() + 30
        def run():
            with self.lock:
                try:
                    self.tick()
                except (OSError, ValueError, KeyError):
                    print('Mihomo recovery: controller unavailable; retry deferred.', flush=True)
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def pause(self):
        self.allowed = False
        with self.lock:
            if any(r.get('blocked') for r in self.records.values()):
                proxies = self.api.request('GET', '/proxies')['proxies']
                for wrapper, record in self.records.items():
                    if record.get('blocked') and proxies.get(wrapper, {}).get('now') == 'REJECT':
                        self.select(wrapper, record['group'])
            self.records.clear()
            self.save()
            self.next_tick = 0

    def close(self):
        try:
            self.pause()
        except (OSError, ValueError, KeyError):
            pass  # Persisted recovery record allows the next process to recover.
