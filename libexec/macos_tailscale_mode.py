"""Select a macOS Tailscale variant without implicitly migrating a live VPN."""
import json
from pathlib import Path
import subprocess as sp
import sys

import macos_tailscale as server
import tailscale_settings as settings

APP = Path('/Applications/Tailscale.app')
MODES = ('personal', 'server')


def selection_file(root):
    return root / 'config/tailscale/macos-mode.json'


def existing(root):
    path = selection_file(root)
    if path.exists():
        try:
            mode = json.loads(path.read_text())['mode']
            if mode in MODES:
                return mode
        except (ValueError, KeyError, TypeError):
            pass
        raise RuntimeError('Tailscale 安装模式记录无效：' + str(path))
    if APP.exists() or (Path.home() / 'Applications/Tailscale.app').exists():
        return 'personal'
    # A stopped Homebrew installation must not silently switch variants on update.
    # Inspect the opt link itself first: older root services can leave its target
    # unreadable until the server installer repairs permissions.
    opt = server.binary().parent.parent
    if opt.is_symlink() or any(p.exists() for p in server.daemon_files()) or server.binary().parent.exists():
        return 'server'
    if Path('/Library/LaunchDaemons/com.tailscale.tailscaled.plist').exists():
        return 'server'
    return None


def mode(root, requested=None, prompt=False):
    if requested is not None:
        if requested not in MODES:
            raise RuntimeError('macOS Tailscale 模式必须为 personal 或 server')
        return requested
    selected = existing(root)
    if selected:
        return selected
    if prompt and sys.stdin.isatty():
        print('Tailscale 用途：\n  1) 个人使用：官网图形版（默认）\n  2) 服务器：tailscaled 系统服务，登录前运行', flush=True)
        while True:
            print('请选择 [1/2，回车选 1]：', end='', flush=True)
            answer = sys.stdin.readline()
            if answer == '':
                raise RuntimeError('未读取到安装模式；请使用 --mode=personal 或 --mode=server')
            if answer.strip() in ('', '1', 'personal'):
                return 'personal'
            if answer.strip() in ('2', 'server'):
                return 'server'
            print('请输入 1 或 2。', flush=True)
    return 'personal'


def save(root, selected):
    from deck_network import write
    write(selection_file(root), json.dumps({'schema': 1, 'mode': selected}) + '\n', 0o600)


def binary(root):
    if mode(root) == 'server':
        return server.binary()
    app = APP if APP.exists() else Path.home() / 'Applications/Tailscale.app'
    return app / 'Contents/MacOS/Tailscale'


def command(root):
    if mode(root) == 'server':
        return [server.binary(), '--socket=' + server.SOCKET]
    return ['/usr/bin/env', 'TAILSCALE_BE_CLI=1', binary(root)]


def configure(root, changes=None):
    selected = mode(root)
    if selected == 'server':
        if (changes or {}).get('exit-node') not in (None, False):
            raise RuntimeError('macOS tailscaled 不支持使用其他出口节点；个人电脑请使用 --mode=personal 的图形版。')
        settings.apply(root, command(root), server.admin, changes)
    else:
        if (changes or {}).get('exit-node') not in (None, False):
            from macos_network_guard import require_exit_compatible
            require_exit_compatible(root)
        settings.apply(root, command(root), server.run, changes,
                       marker_path=root / 'config/tailscale/personal-defaults-applied.json')


def action(root, operation, requested=None):
    if requested is not None and operation != 'install':
        raise RuntimeError('--mode 仅用于 wb tailscale install；其他命令沿用已选模式')
    selected = mode(root, requested, prompt=operation in ('install', 'apply'))
    print('Tailscale 安装模式：' + selected, flush=True)
    if selected == 'server':
        result = server.status(operation == 'check') if operation in ('status', 'check') else server.action(root, operation)
    else:
        import macos_tailscale_gui as gui
        result = gui.action(root, operation)
        if operation in ('status', 'check') and result == 0 and gui.state() in ('Running', 'Stopped'):
            from macos_network_guard import check
            try:
                check(root, command(root))
            except (RuntimeError, OSError, sp.TimeoutExpired) as error:
                print('WARNING: ' + str(error))
                if operation == 'check':
                    result = 1
    if operation in ('install', 'update', 'apply') and result in (0, 2):
        save(root, selected)
    return result
