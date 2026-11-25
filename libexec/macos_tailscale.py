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
LABELS = (LABEL, *SPEC.get('legacy_services', []))
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


def admin(args, **kwargs):
    return run(['/usr/bin/sudo', *([] if sys.stdin.isatty() else ['-n']), '--', *args], **kwargs)


def loaded(label=None, domain='system'):
    return any(sp.run(['/bin/launchctl', 'print', domain + '/' + name],
                      stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode == 0
               for name in ((label,) if label else LABELS))


def daemon_files():
    return [DAEMON, *(DAEMON.with_name(name + '.plist') for name in LABELS if name != LABEL)]


def installed():
    return (os.access(binary(), os.X_OK) and os.access(binary().with_name('tailscaled'), os.X_OK)
            and any(path.is_file() for path in daemon_files()))


def conflict_check():
    apps = [Path('/Applications/Tailscale.app'), Path.home() / 'Applications/Tailscale.app']
    extension = sp.run(['/usr/bin/pgrep', '-f',
                        '/(io[.]tailscale[.]ipn[.]macsys[.]network-extension|IPNExtension)( |$)'],
                       stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    if any(app.exists() for app in apps) or extension.returncode == 0:
        raise RuntimeError('检测到 Tailscale GUI 版。请在本机退出并卸载 Tailscale.app、清空废纸篓并重启，'
                           '然后重跑 wb tailscale install --mode=server；旧 GUI 登录身份不会自动迁移。'
                           '请勿通过当前 Tailscale SSH 连接执行卸载。')
    if loaded('com.tailscale.tailscaled') or Path('/Library/LaunchDaemons/com.tailscale.tailscaled.plist').exists():
        raise RuntimeError('检测到 install-system-daemon 安装的 tailscaled；请先在本机运行 '
                           'sudo /usr/local/bin/tailscaled uninstall-system-daemon 卸载旧服务定义（保留状态），'
                           '再运行 wb tailscale install，避免两个守护进程使用同一状态文件。')
    if loaded(domain=f'gui/{os.getuid()}') or any(
            (Path.home() / 'Library/LaunchAgents' / (name + '.plist')).exists() for name in LABELS):
        raise RuntimeError('检测到用户级 Tailscale 服务；请运行 brew services stop tailscale，'
                           '再重跑 wb tailscale install 以安装系统级服务。')


def service(operation):
    brew = brew_prefix() / 'bin/brew'
    if not brew.is_file():
        raise RuntimeError('缺少 Homebrew；请先运行 wb platform install')
    admin([brew, 'services', operation, FORMULA], umask=0o022)


def repair_cli_permissions():
    # Older Workbench runs inherited Mihomo's private umask (077). Once brew
    # services chowns bin to root, mode 0700 locks the caller out of both CLIs.
    # Repair only these public executables and their bin directory; never chmod
    # the whole prefix, private Tailscale state, logs, or subscription files.
    # macOS checks read permission on the symlink itself in readlink/realpath.
    # Following it can succeed even while Path.resolve() fails. brew services
    # also chowns these links to root, so repair them before resolving binaries.
    for link in (brew_prefix() / 'opt/tailscale', brew_prefix() / 'var/homebrew/linked/tailscale'):
        if link.is_symlink():
            try:
                link.readlink()
            except PermissionError:
                print('修复 Tailscale 符号链接权限：' + str(link), flush=True)
                admin(['/bin/chmod', '-h', 'a+rx', link])
                link.readlink()  # Fail here with the exact path if repair did not work.
    directory = binary().parent
    if directory.is_dir() and not os.access(directory, os.X_OK):
        print('修复 tailscale bin 目录权限（保留 root 所有权）。', flush=True)
        admin(['/bin/chmod', 'a+rx', directory])
    for name in ('tailscale', 'tailscaled'):
        path = directory / name
        if path.is_file() and not os.access(path, os.X_OK):
            admin(['/bin/chmod', 'a+rx', path])


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
    repair_cli_permissions()
    present = os.access(binary(), os.X_OK) and os.access(binary().with_name('tailscaled'), os.X_OK)
    before = binary().resolve() if present else None
    if update or not present:
        run([brew, 'upgrade' if present else 'install', '--formula', FORMULA], umask=0o022)
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
    from tailscale_settings import apply as apply_settings
    apply_settings(root, [binary(), '--socket=' + SOCKET], admin)
    from software import atomic_link
    atomic_link(binary(), root / 'bin/tailscale', root)
    if not installed() or not loaded():
        raise RuntimeError('tailscaled 系统级 LaunchDaemon 安装检查失败')
    print('tailscaled 系统服务已就绪；查看 DNS/出口设置：wb tailscale show。首次登录：wb tailscale apply')


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
            command = [binary(), '--socket=' + SOCKET]
            if state['BackendState'] == 'Stopped':
                # A bare up preserves all preferences. Even --timeout disables
                # this CLI special case, so bound the subprocess instead.
                admin([*command, 'up'], timeout=60)
            else:
                from tailscale_settings import login_flags
                admin([*command, 'up', '--timeout=60s', *login_flags(command)])
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
            raise RuntimeError('tailscaled 缺失或不可执行；请先运行 wb tailscale install 修复')
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
        print('tailscaled 系统服务: ' + state)
        issues.extend(data.get('Health') or [])
        if state not in ('Running', 'Stopped'):
            issues.append('登录/设备授权待完成：wb tailscale apply' if state in ('NeedsLogin', 'NeedsMachineAuth')
                          else '无法读取 tailscaled 状态：wb tailscale logs')
    for issue in issues:
        print('PENDING:', issue)
    if not issues:
        print('tailscale: OK')
    return int(bool(issues)) if check else 0
