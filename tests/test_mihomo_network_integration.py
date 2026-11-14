"""Opt-in real-core check: MIHOMO_TEST_BINARY=/path/to/mihomo python3 -m unittest ..."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import mihomo_network as network
from mihomo_runtime import API, Runtime, private_json
from network_link import Link


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('MIHOMO_TEST_BINARY'), 'requires an explicit Mihomo binary')
class RealCoreTests(unittest.TestCase):
    def test_dashboard_and_pid_survive_offline_while_checks_become_idle(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(204)
                self.end_headers()
            do_HEAD = do_GET
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port, mixed = free_port(), free_port()
        url = f'http://127.0.0.1:{server.server_port}'
        api = API(f'http://127.0.0.1:{port}')
        online, children, stop = [False], [], threading.Event()
        class Events:
            changed = threading.Event()
            resumed = False
            def start(self): pass
            def close(self): pass
            def wait(self, timeout): self.changed.wait(timeout); self.changed.clear()
        events = Events()
        def spawn(command):
            child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.append(child)
            return child
        def wait_for(predicate):
            end = time.monotonic() + 12
            while time.monotonic() < end:
                try:
                    if predicate():
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(.1)
            self.fail('real-core condition timed out')
        def traffic():
            conn = http.client.HTTPConnection('127.0.0.1', mixed, timeout=3)
            try:
                conn.request('GET', url + '/traffic')
                self.assertEqual(conn.getresponse().status, 204)
            finally:
                conn.close()
        with tempfile.TemporaryDirectory(prefix='workbench-mihomo-test-') as tmp:
            folder = Path(tmp)
            (folder/'ui').mkdir()
            (folder/'ui/index.html').write_text('dashboard-test')
            config = {'mode': 'rule', 'log-level': 'silent', 'external-ui': str(folder/'ui'),
                'external-controller': f'127.0.0.1:{port}', 'mixed-port': mixed,
                'bind-address': '127.0.0.1', 'allow-lan': False, 'tun': {'enable': False},
                'profile': {'store-selected': True},
                'dns': {'enable': False}, 'proxies': [{'name': 'test-node', 'type': 'direct'}],
                'proxy-groups': [{'name': 'test-fallback🎮', 'type': 'fallback',
                    'proxies': ['test-node'], 'url': url+'/health', 'interval': 1, 'lazy': True},
                    {'name': 'test-wrapper', 'type': 'select', 'proxies': ['test-fallback🎮', 'DIRECT', 'REJECT']}],
                'rules': ['MATCH,test-wrapper']}
            (folder/'config.yaml').write_text(json.dumps(config))
            runtime = Runtime(config, folder, api)
            private_json(folder/'standby.json', runtime.initial_config())
            worker = threading.Thread(target=network.supervise, args=([
                os.environ['MIHOMO_TEST_BINARY'], '-d', tmp, '-f', str(folder/'standby.json')], runtime), kwargs={
                'state_file': folder/'state.json', 'probe': lambda: Link('ready', 'test') if online[0] else Link('offline'),
                'stop': stop, 'watchdog': .1, 'settle': .1, 'disconnect': .1, 'spawn': spawn, 'events': events})
            worker.start()
            try:
                wait_for(lambda: api.request('GET', '/configs')['mode'] == 'direct')
                time.sleep(2.2)
                self.assertNotIn('/health', hits, 'cold offline boot must not probe nodes')
                self.assertEqual(api.request('GET', '/proxies')['proxies']['test-fallback🎮']['type'], 'Selector')
                online[0] = True
                events.changed.set()
                wait_for(lambda: runtime.online)
                traffic()
                wait_for(lambda: '/health' in hits)
                pid = children[0].pid
                online[0] = False
                events.changed.set()
                wait_for(lambda: not runtime.online)
                self.assertEqual(api.request('GET', '/configs')['mode'], 'direct')
                with api.opener.open(api.endpoint+'/ui/', timeout=3) as reply:
                    self.assertEqual(reply.read(), b'dashboard-test')
                traffic()  # Direct traffic must not keep the fallback health checks active.
                time.sleep(2.2)
                before = hits.count('/health')
                time.sleep(2.2)
                self.assertEqual(hits.count('/health'), before)
                self.assertEqual(len(children), 1)
                self.assertEqual(children[0].pid, pid)
                self.assertIsNone(children[0].poll())
                online[0] = True
                events.changed.set()
                wait_for(lambda: runtime.online)
                self.assertEqual(api.request('GET', '/configs')['mode'], 'rule')
                self.assertEqual(api.request('GET', '/proxies')['proxies']['test-wrapper']['now'], 'test-fallback🎮')
                self.assertEqual(api.request('GET', '/proxies')['proxies']['test-fallback🎮']['type'], 'Fallback')
                traffic()
                wait_for(lambda: hits.count('/health') > before)
            finally:
                stop.set()
                events.changed.set()
                worker.join(timeout=25)
            self.assertFalse(worker.is_alive())
            self.assertTrue(all(child.poll() is not None for child in children))
