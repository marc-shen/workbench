"""Read physical connectivity and wait for NM/logind events without busy polling."""
from dataclasses import dataclass
import hashlib
import os
import subprocess as sp
import threading
import time
import urllib.error
import urllib.request

UPLINK_TYPES = {'wifi', 'ethernet', 'gsm', 'cdma', 'bluetooth', 'wimax'}


@dataclass(frozen=True)
class Link:
    state: str
    identity: str = ''


def nmcli(*args):
    return sp.check_output(['nmcli', '-e', 'no', *args], text=True,
        stderr=sp.DEVNULL, timeout=4, env=dict(os.environ, LC_ALL='C'))


def snapshot():
    try:
        devices = []
        for line in nmcli('-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status').splitlines():
            fields = line.split(':', 2)
            if len(fields) == 3 and fields[1] in UPLINK_TYPES and fields[2].startswith('connected'):
                devices.append(fields[0])
        if not devices:
            return Link('offline')
        identities, connectivity = [], []
        for device in sorted(devices):
            details = nmcli('-t', '-f', 'GENERAL.CON-UUID,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,IP6.ADDRESS,IP6.GATEWAY,IP6.DNS',
                            'device', 'show', device)
            # A connected LAN without a gateway is not an Internet uplink.
            if any(line.startswith(('IP4.GATEWAY:', 'IP6.GATEWAY:')) and line.split(':', 1)[1] not in ('', '--')
                   for line in details.splitlines()):
                identities.append(device + '\n' + details)
                connectivity.extend(nmcli('-g', 'GENERAL.IP4-CONNECTIVITY,GENERAL.IP6-CONNECTIVITY',
                                          'device', 'show', device).splitlines())
        if not identities:
            return Link('offline')
        identity = hashlib.sha256('\n'.join(identities).encode()).hexdigest()[:16]
        # Ignore connectivity advertised by virtual interfaces. A working Ethernet
        # connection may remain usable even when a secondary Wi-Fi needs login.
        state = 'ready' if any('(full)' in x for x in connectivity) else (
                'portal' if any('(portal)' in x for x in connectivity) else 'checking')
        return Link(state, identity)
    except (OSError, sp.SubprocessError):
        return Link('unknown')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_underlay():
    """Only run while TUN is disabled: use DHCP DNS and no proxy environment."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    portal = False
    for url, expected in (
        ('http://ping.archlinux.org/nm-check.txt', b'NetworkManager is online'),
        ('http://www.msftconnecttest.com/connecttest.txt', b'Microsoft Connect Test'),
    ):
        try:
            with opener.open(url, timeout=4) as response:
                body = response.read(1024).strip()
                if response.status == 200 and body == expected:
                    return 'ready'
                portal |= response.status == 200 and b'<html' in body.lower()
        except urllib.error.HTTPError as error:
            portal |= 300 <= error.code < 400 or error.code == 511
        except (OSError, ValueError):
            pass
    return 'portal' if portal else 'limited'


class NetworkEvents:
    """Monitor events; a 30-second watchdog covers lost events and manual UI changes."""
    def __init__(self):
        self.changed = threading.Event()
        self.resumed = False
        self.children = []
        self.threads = []
        self.closed = False
        self.next_start = 0
        self.start()

    def start(self):
        commands = [(['nmcli', 'monitor'], False),
                    (['dbus-monitor', '--system',
                      "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"], True)]
        if self.children and all(p.poll() is None for p in self.children):
            return
        if time.monotonic() < self.next_start:
            return
        # Missing D-Bus permissions or an exiting monitor must not cause a busy loop.
        self.next_start = time.monotonic() + 60
        self.close_children()
        for command, sleep_monitor in commands:
            try:
                child = sp.Popen(command, stdout=sp.PIPE, stderr=sp.DEVNULL, text=True,
                                 env=dict(os.environ, LC_ALL='C'))
            except OSError:
                continue
            self.children.append(child)
            def read_events(process=child, sleeping=sleep_monitor):
                for line in process.stdout:
                    if sleeping:
                        if 'boolean false' not in line:
                            continue
                        self.resumed = True
                    self.changed.set()
                if not self.closed:
                    self.changed.set()
            thread = threading.Thread(target=read_events, daemon=True)
            self.threads.append(thread)
            thread.start()

    def wait(self, timeout):
        self.changed.wait(timeout)
        self.changed.clear()

    def close_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except sp.TimeoutExpired:
                    child.kill()
                    child.wait()
        for thread in self.threads:
            thread.join(timeout=1)
        self.children.clear()
        self.threads.clear()

    def close(self):
        self.closed = True
        self.close_children()
