"""Official standalone macOS app: verified PKG installation and bundled CLI."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess as sp

import macos_tailscale as server
import macos_tailscale_mode as variants
import tailscale_settings as settings

BASE = 'https://pkgs.tailscale.com/stable/'
STATE = Path('/Library/Tailscale')
CURL = ['/usr/bin/curl', '--fail', '--location', '--show-error', '--proto', '=https',
        '--proto-redir', '=https', '--connect-timeout', '20', '--retry', '3']


def command():
    return ['/usr/bin/env', 'TAILSCALE_BE_CLI=1', variants.APP / 'Contents/MacOS/Tailscale']


def installed():
    return os.access(variants.APP / 'Contents/MacOS/Tailscale', os.X_OK)


def conflict_check():
    user_app = Path.home() / 'Applications/Tailscale.app'
    if user_app.exists() or (variants.APP / 'Contents/_MASReceipt').exists():
        raise RuntimeError('检测到用户目录或 App Store 版 Tailscale；请按官方迁移说明在本机卸载并重启，'
                           '再安装官网图形版。脚本不会覆盖另一种应用。')
    files = [*server.daemon_files(), Path('/Library/LaunchDaemons/com.tailscale.tailscaled.plist'),
             *(Path.home() / 'Library/LaunchAgents' / (name + '.plist') for name in server.LABELS)]
    running = sp.run(['/usr/bin/pgrep', '-x', 'tailscaled'], stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode == 0
    if (any(p.exists() for p in files) or running or server.loaded()
            or server.loaded('com.tailscale.tailscaled') or server.loaded(domain=f'gui/{os.getuid()}')):
        raise RuntimeError('检测到 tailscaled 服务。请在本机先运行 sudo brew services stop tailscale；'
                           '用户级服务用 brew services stop tailscale，install-system-daemon 版用 '
                           'sudo /usr/local/bin/tailscaled uninstall-system-daemon。'
                           '停止旧服务后再运行 wb tailscale install --mode=personal；需要重新登录。'
                           '不要通过正在迁移的 Tailscale SSH 连接操作。')
    if not installed() and any((STATE / name).exists() for name in ('tailscaled.state', 'profile-data')):
        raise RuntimeError('检测到 /Library/Tailscale 中的旧身份数据。停止旧服务并不会清除登录状态；'
                           '请在本机按 docs/macos-network.md 的迁移说明处理旧状态和钥匙串后重试。'
                           '脚本不会自动删除身份数据或把它迁入图形版。')


def latest():
    result = server.run([*CURL, '--silent', '--max-time', '60', BASE + '?mode=json'], capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
        name = data['MacZips']['universal-package']
        version = data['MacZipsVersion']
        if not re.fullmatch(r'\d+\.\d+\.\d+', version) or name != f'Tailscale-{version}-macos.pkg':
            raise ValueError('unexpected asset')
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('官网未返回有效的 macOS 稳定版安装包') from None
    return name, version


def version():
    try:
        data = plistlib.loads((variants.APP / 'Contents/Info.plist').read_bytes())
        value = data['CFBundleShortVersionString']
        return tuple(int(part) for part in value.split('.'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, plistlib.InvalidFileException):
        return ()


def package(root, name):
    url = BASE + name
    checksum = server.run([*CURL, '--silent', '--max-time', '60', url + '.sha256'], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch('[0-9a-fA-F]{64}', checksum):
        raise RuntimeError('官网 SHA256 校验值无效')
    path = root / 'cache/tailscale' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    def verified():
        if not path.is_file():
            return False
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == checksum.lower()
    if not verified():
        temporary = path.with_suffix('.pkg.download')
        print('下载官网 Tailscale 安装包：' + name, flush=True)
        server.run([*CURL, '--progress-bar', '--max-time', '1800', '--output', temporary, url])
        temporary.replace(path)
        if not verified():
            path.unlink()
            raise RuntimeError('Tailscale PKG SHA256 校验失败；未运行安装器')
    server.run(['/usr/sbin/pkgutil', '--check-signature', path])
    server.run(['/usr/sbin/spctl', '--assess', '--type', 'install', '--verbose=2', path])
    return path


def link_cli(root):
    from deck_network import write
    from software import atomic_link
    wrapper = root / 'apps/tailscale/tailscale'
    write(wrapper, '#!/bin/sh\nexec ' + shlex.join(map(str, command())) + ' "$@"\n', 0o755)
    # Replace the old symlink, never write through it into a Homebrew binary.
    atomic_link(wrapper, root / 'bin/tailscale', root)


def state():
    if not installed():
        return 'NotInstalled'
    try:
        result = server.run([*command(), 'status', '--json'], capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout).get('BackendState', 'Unavailable')
    except sp.CalledProcessError as error:
        # NeedsLogin also returns a nonzero CLI exit status.
        try:
            return json.loads(error.stdout).get('BackendState', 'Unavailable')
        except (ValueError, TypeError, AttributeError):
            return 'Unavailable'
    except (OSError, ValueError, TypeError, AttributeError, sp.TimeoutExpired):
        return 'Unavailable'


def defaults(root):
    settings.apply(root, command(), server.run,
                   marker_path=root / 'config/tailscale/personal-defaults-applied.json')


def install(root, update=False):
    conflict_check()
    if update or not installed():
        name, release = latest()
        if not installed() or version() < tuple(map(int, release.split('.'))):
            path = package(root, name)
            server.admin(['/usr/sbin/installer', '-verboseR', '-pkg', path, '-target', '/'], umask=0o022)
            if not installed():
                raise RuntimeError('官网安装器完成后未找到 Tailscale.app')
    link_cli(root)
    if state() in ('Running', 'Stopped'):
        defaults(root)
    else:
        print('图形版已安装；运行 wb tailscale apply，在应用中批准系统扩展/VPN 并登录，'
              '再运行 wb tailscale apply 初始化 DNS/出口默认关闭。', flush=True)


def action(root, operation):
    if operation in ('install', 'update'):
        install(root, operation == 'update')
        return 0
    if operation in ('status', 'check'):
        current = state()
        print('Tailscale 图形版：' + current)
        if current not in ('Running', 'Stopped'):
            print('PENDING: 请运行 wb tailscale apply 完成系统授权和登录')
        return int(current not in ('Running', 'Stopped')) if operation == 'check' else 0
    if operation == 'apply':
        install(root)
    if not installed():
        raise RuntimeError('未安装官网图形版；请运行 wb tailscale install --mode=personal')
    if operation == 'logs':
        server.run(['/usr/bin/log', 'show', '--last', '10m', '--style', 'compact',
                    '--predicate', 'subsystem BEGINSWITH "io.tailscale" OR process == "Tailscale"'], timeout=30)
        return 0
    if operation in ('apply', 'open', 'start', 'restart'):
        conflict_check()
        server.run(['/usr/bin/open', str(variants.APP)])
        if operation == 'open':
            return 0
    if operation in ('apply', 'start', 'restart'):
        current = state()
        if current not in ('Running', 'Stopped'):
            print('待完成：在 Tailscale.app 中批准系统扩展/VPN 并登录，然后运行 wb tailscale apply。')
            return 2
        defaults(root)
        if operation == 'restart':
            server.run([*command(), 'down'], timeout=30)
        if current == 'Stopped' or operation == 'restart':
            server.run([*command(), 'up'], timeout=60)
        if state() != 'Running':
            raise RuntimeError('Tailscale 尚未连接；请查看图形客户端和 wb tailscale status')
    elif operation == 'stop':
        server.run([*command(), 'down'], timeout=30)
    else:
        raise RuntimeError('不支持的 Tailscale 操作：' + operation)
    return 0
