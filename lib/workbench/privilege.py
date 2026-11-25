"""Keep the terminal's sudo ticket alive for one installation workflow."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading

from .ui import Console


def needs_admin(steps):
    for step in steps:
        name, action = step['component'], step['action']
        if (name in ('platform', 'emacs', 'texlive', 'crossdesk', 'snug') and action in ('install', 'update', 'offer')
                or name == 'tailscale' and action in ('install', 'update', 'apply', 'start', 'stop', 'restart', 'set')
                or name == 'mihomo' and action in ('apply', 'start', 'stop', 'restart')):
            return True
    return False


@contextmanager
def sudo_session(steps, platform, root=None):
    if platform == 'macos' and any(step['component'] == 'tailscale' for step in steps):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'libexec'))
        import macos_tailscale_mode as variants
        root = root if root is not None else Path(os.environ.get('SOFTWARE_HOME', Path.home() / 'Software'))
        if variants.mode(root) == 'personal':
            # The app's LocalAPI runs as the logged-in user. Only installation
            # (including apply on a fresh machine) needs an administrator ticket.
            steps = [step for step in steps if step['component'] != 'tailscale'
                     or step['action'] in ('install', 'update', 'apply')]
    if platform != 'macos' or not sys.stdin.isatty() or not needs_admin(steps):
        yield
        return
    Console().print('管理员验证：请在终端输入密码；本次任务期间自动维持 sudo 授权。')
    # sudo reads the password from the terminal. Never pipe, capture or store it.
    if subprocess.run(['/usr/bin/sudo', '-v']).returncode:
        raise RuntimeError('管理员验证失败，安装尚未开始。')
    stop = threading.Event()

    def renew():
        while not stop.wait(45):
            try:
                result = subprocess.run(['/usr/bin/sudo', '-n', '-v'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if result.returncode:
                    return
            except (OSError, subprocess.TimeoutExpired):
                return

    worker = threading.Thread(target=renew, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=11)

