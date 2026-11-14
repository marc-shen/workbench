#!/usr/bin/env python3
"""Event-driven Steam Deck routing: standby first, native Wi-Fi authentication."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import threading
import time

from network_link import NetworkEvents, snapshot, check_underlay
from mihomo_runtime import Runtime, private_json


def state_path():
    return Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'workbench/mihomo-network.json'


def network_available():
    link = snapshot()
    if link.state == 'ready':
        return True, 'uplink-ready'
    if link.state in ('offline', 'portal'):
        return False, link.state
    return None, link.state


class StableLink:
    def __init__(self, delay=10):
        self.delay, self.sample, self.since = delay, None, 0

    def update(self, value, now):
        if value is None or value != self.sample:
            self.sample, self.since = value, now
        if value is not None and now - self.since >= self.delay:
            return value
        return None


def stop_child(child):
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=15)
    except sp.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def supervise(command, runtime, *, state_file=None, probe=snapshot, underlay=check_underlay,
              stop=None, events=None, watchdog=30, settle=3, disconnect=10, spawn=sp.Popen,
              health=None):
    stop = stop if stop is not None else threading.Event()
    state_file = state_file or state_path()
    events = events if events is not None else NetworkEvents()
    child, last, exit_code = None, None, 0
    identity, cached_check, next_check, check_delay = '', 'checking', 0, 30
    last_link_state = None
    ready_gate, offline_gate = StableLink(settle), StableLink(disconnect)
    last_sleep_gap = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic() if hasattr(time, 'CLOCK_BOOTTIME') else 0

    def publish(phase, reason):
        nonlocal last
        current = (phase, reason, child.pid if child else None, identity, runtime.desired)
        if current == last:
            return
        private_json(state_file, dict(phase=phase, reason=reason, core_pid=current[2],
                     network=identity, desired=runtime.desired, updated_at=time.time()))
        print(f'Mihomo network: {phase} ({reason})', flush=True)
        last = current

    def standby():
        if health:
            health.pause()
        runtime.standby()

    try:
        # command points at a standby config: no TUN or automatic probes at boot.
        child = spawn(command)
        threading.Thread(target=lambda: (child.wait(), events.changed.set()), daemon=True).start()
        while not stop.is_set():
            wait = watchdog
            if child.poll() is not None:
                publish('failed', f'core-exited-{child.returncode}')
                exit_code = 1
                break
            try:
                events.start()
                link = probe()
                if stop.is_set():
                    break
                now = time.monotonic()
                sleep_gap = time.clock_gettime(time.CLOCK_BOOTTIME) - now if hasattr(time, 'CLOCK_BOOTTIME') else 0
                resumed = events.resumed or sleep_gap - last_sleep_gap > 2
                events.resumed = False
                last_sleep_gap = sleep_gap
                if resumed or (link.identity and identity != link.identity):
                    standby()
                    identity, cached_check, next_check, check_delay = link.identity, 'checking', 0, 30
                    last_link_state = None
                    ready_gate, offline_gate = StableLink(settle), StableLink(disconnect)
                runtime.observe_manual()
                if link.state == 'unknown':
                    publish('online' if runtime.online else 'checking', 'networkmanager-unavailable')
                elif link.state == 'offline':
                    ready_gate.update(None, now)
                    if not runtime.online or offline_gate.update(False, now) is False:
                        standby()
                        identity, cached_check, next_check = '', 'checking', 0
                        publish('offline', 'no-physical-uplink')
                    else:
                        publish('checking', 'disconnect-grace')
                        wait = min(watchdog, 1)
                else:
                    offline_gate.update(None, now)
                    if link.state == 'portal' and last_link_state != 'portal':
                        standby()
                        ready_gate.update(None, now)
                        cached_check, next_check, check_delay = 'portal', 0, 30
                    verdict = link.state
                    if verdict != 'ready' and not runtime.online:
                        if now >= next_check:
                            cached_check = underlay()  # TUN is disabled here.
                            next_check = time.monotonic() + check_delay
                            check_delay = 30 if cached_check == 'portal' else min(check_delay * 2, 120)
                        verdict = cached_check
                    if runtime.online:
                        # A native HTTP success may precede NM's next cached portal
                        # update. Do not bounce TUN until NM reports a new transition.
                        # One blocked NM probe on an established network is not an outage.
                        publish('online', 'ready' if verdict == 'ready' else 'connectivity-unconfirmed')
                    elif verdict == 'ready':
                        if ready_gate.update(True, now) is True:
                            runtime.activate()
                            publish('online', 'uplink-ready')
                        else:
                            publish('checking', 'uplink-settling')
                            wait = min(watchdog, 1)
                    else:
                        ready_gate.update(None, now)
                        publish('portal' if verdict == 'portal' else 'limited', 'awaiting-native-connectivity')
                last_link_state = link.state
                if health and runtime.online:
                    health.schedule()
            except (OSError, ValueError, KeyError, RuntimeError, sp.SubprocessError) as error:
                # Startup and API failures remain recoverable, without a core restart loop.
                publish('routing-pending', type(error).__name__)
                wait = min(watchdog, 3)
            events.wait(wait)
    finally:
        if health:
            health.close()
        stop_child(child)
        events.close()
        child = None
        if not exit_code:
            publish('stopped', 'supervisor-stopped')
    return exit_code


def status():
    try:
        data = json.loads(state_path().read_text())
        return f"Network supervisor: {data['phase']} ({data['reason']})"
    except (OSError, ValueError, KeyError):
        return 'Network supervisor: no runtime state yet'


def main():
    import yaml
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--core', required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    runtime = Runtime(config, args.config.parent)
    initial = args.config.with_name('standby.json')
    private_json(initial, runtime.initial_config())
    events, stop = NetworkEvents(), threading.Event()
    def shutdown(*_):
        stop.set()
        events.changed.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)
    from mihomo_health import GroupHealth
    health = GroupHealth(runtime.api, config, args.config.parent)
    return supervise([args.core, '-d', str(args.config.parent), '-f', str(initial)],
                     runtime, stop=stop, events=events, health=health)


if __name__ == '__main__':
    raise SystemExit(main())
