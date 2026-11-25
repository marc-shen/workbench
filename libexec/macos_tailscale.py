"""Homebrew's open-source tailscaled as a root LaunchDaemon on macOS."""
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess as sp
import sys
import time
from config_data import read

SPEC = read(Path(__file__).resolve().parents[1] / 'components/tailscale/component.toml')['install']['macos']
LABEL = SPEC['service']
FORMULA = SPEC['formula']
DAEMON = Path('/Library/LaunchDaemons') / (LABEL + '.plist')
SOCKET = '/var/run/tailscaled.socket'


def brew_prefix():
    brew = shutil.which('brew')
    return Path(brew).parent.parent if brew else Path('/opt/homebrew' if platform.machine() == 'arm64' else '/usr/local')


def binary():
    return brew_prefix() / 'opt/tailscale/bin/tailscale'


def run(args, **kwargs):
    return sp.run(list(map(str, args)), check=True, **kwargs)


def admin(args):
    return run(['/usr/bin/sudo', *([] if sys.stdin.isatty() else ['-n']), '--', *args])


def loaded(label=LABEL, domain='system'):
    return sp.run(['/bin/launchctl', 'print', domain + '/' + label],
                  stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode == 0


def installed():
    return os.access(binary(), os.X_OK) and os.access(binary().with_name('tailscaled'), os.X_OK) and DAEMON.is_file()


def conflict_check():
    apps = [Path('/Applications/Tailscale.app'), Path.home() / 'Applications/Tailscale.app']
    extension = sp.run(['/usr/bin/pgrep', '-f',
                        '/(io[.]tailscale[.]ipn[.]macsys[.]network-extension|IPNExtension)( |$)'],
                       stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    if any(app.exists() for app in apps) or extension.returncode == 0:
        raise RuntimeError('检测到 Tailscale GUI 版。请在本机退出并卸载 Tailscale.app、清空废纸篓并重启，'
                           '然后重跑 wb tailscale install；旧 GUI 登录身份不会自动迁移。'
                           '请勿通过当前 Tailscale SSH 连接执行卸载。')
    if loaded('com.tailscale.tailscaled') or Path('/Library/LaunchDaemons/com.tailscale.tailscaled.plist').exists():
        raise RuntimeError('检测到 install-system-daemon 安装的 tailscaled；请先在本机运行 '
                           'sudo /usr/local/bin/tailscaled uninstall-system-daemon 卸载旧服务定义（保留状态），'
                           '再运行 wb tailscale install，避免两个守护进程使用同一状态文件。')
    if loaded(domain=f'gui/{os.getuid()}') or (Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')).exists():
        raise RuntimeError('检测到用户级 Tailscale 服务；请运行 brew services stop tailscale，'
                           '再重跑 wb tailscale install 以安装系统级服务。')


def service(operation):
    brew = brew_prefix() / 'bin/brew'
    if not brew.is_file():
        raise RuntimeError('缺少 Homebrew；请先运行 wb platform install')
    admin([brew, 'services', operation, FORMULA])


def wait_ready():
    for _ in range(20):
        result = sp.run([str(binary()), '--socket=' + SOCKET, 'status', '--json'],
                        capture_output=True, text=True, timeout=5)
        try:
            state = json.loads(result.stdout)
        except ValueError:
            state = {}
        # NeedsLogin has a nonzero CLI exit code but proves the daemon is ready.
        if state.get('BackendState') in ('Running', 'Stopped', 'NeedsLogin', 'NeedsMachineAuth'):
            return state
        time.sleep(0.5)
    raise RuntimeError('tailscaled 未就绪；请运行 wb tailscale logs 查看启动日志')


def operator():
    # Native LocalAPI authorization, not a sudoers exemption. This lets the user's
    # Mihomo LaunchAgent restore Serve forwarding without background sudo prompts.
    username = pwd.getpwuid(os.getuid()).pw_name
    admin([binary(), '--socket=' + SOCKET, 'set', '--operator=' + username])


def prepare_log():
    # Create it as the invoking user before launchd opens it as root. Keep login
    # URLs and network diagnostics private while making `wb tailscale logs` usable.
    log = brew_prefix() / 'var/log/tailscaled.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.touch(mode=0o600)
    elif not os.access(log, os.R_OK):
        username = pwd.getpwuid(os.getuid()).pw_name
        admin(['/bin/chmod', '+a', 'user:' + username + ' allow read', log])


def install(root, update=False):
    conflict_check()
    brew = brew_prefix() / 'bin/brew'
    if not brew.is_file():
        raise RuntimeError('缺少 Homebrew；请先运行 wb platform install')
    present = os.access(binary(), os.X_OK) and os.access(binary().with_name('tailscaled'), os.X_OK)
    before = binary().resolve() if present else None
    if update or not present:
        run([brew, 'upgrade' if present else 'install', '--formula', FORMULA])
    if not os.access(binary(), os.X_OK) or not os.access(binary().with_name('tailscaled'), os.X_OK):
        raise RuntimeError('Homebrew 未安装完整的 tailscale/tailscaled')
    prepare_log()
    # The stable opt symlink follows upgrades. Restart only if the binary changed.
    if loaded() and before != binary().resolve():
        service('restart')
    elif not loaded():
        service('start')
    wait_ready()
    operator()
    from software import atomic_link
    atomic_link(binary(), root / 'bin/tailscale', root)
    if not installed() or not loaded():
        raise RuntimeError('tailscaled 系统级 LaunchDaemon 安装检查失败')
    print('tailscaled 系统服务已就绪；保留已有登录、路由与出口设置。首次登录：wb tailscale apply')


def action(root, operation):
    if operation in ('install', 'update'):
        install(root, operation == 'update')
    elif operation == 'apply':
        install(root)
        state = wait_ready()
        if state['BackendState'] not in ('Running', 'NeedsMachineAuth'):
            if not sys.stdin.isatty():
                print('登录/连接待完成：请在终端运行 wb tailscale apply')
                return 2
            # No --reset / --force-reauth: retain existing node settings and keys.
            admin([binary(), '--socket=' + SOCKET, 'up', '--timeout=60s'])
        elif state['BackendState'] == 'NeedsMachineAuth':
            print('设备待管理员批准：https://login.tailscale.com/admin/machines')
            return 2
    elif operation == 'open':
        run(['open', 'https://login.tailscale.com/admin/machines'])
    elif operation == 'logs':
        run(['tail', '-n', '80', brew_prefix() / 'var/log/tailscaled.log'])
    elif operation in ('start', 'stop', 'restart'):
        if operation != 'stop':
            conflict_check()
        if not os.access(binary().with_name('tailscaled'), os.X_OK):
            raise RuntimeError('请先运行 wb tailscale install')
        service(operation)
        if operation != 'stop':
            wait_ready()
    else:
        raise RuntimeError('不支持的 Tailscale 操作：' + operation)
    return 0


def status(check=False):
    issues = []
    if not installed():
        issues.append('tailscaled 系统服务未安装：wb tailscale install')
    if not loaded():
        issues.append('tailscaled 系统服务未加载：wb tailscale start')
    elif os.access(binary(), os.X_OK):
        result = sp.run([str(binary()), '--socket=' + SOCKET, 'status', '--json'],
                        capture_output=True, text=True, timeout=15)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            data = {}
        state = data.get('BackendState', 'unavailable')
        print('tailscaled (system/' + LABEL + '): ' + state)
        issues.extend(data.get('Health') or [])
        if state not in ('Running', 'Stopped'):
            issues.append('登录/设备授权待完成：wb tailscale apply' if state in ('NeedsLogin', 'NeedsMachineAuth')
                          else '无法读取 tailscaled 状态：wb tailscale logs')
    for issue in issues:
        print('PENDING:', issue)
    if not issues:
        print('tailscale: OK')
    return int(bool(issues)) if check else 0
