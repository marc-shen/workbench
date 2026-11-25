#!/usr/bin/env python3
"""Install CrossDesk's latest stable GitHub PKG/DEB, without launching the GUI."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import subprocess as sp
import sys
import tarfile

from software import atomic_link, install_environment, receipt, resolve, run

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from workbench.platforms import detect

UPSTREAM = 'kunkundi/crossdesk'
API = f'https://api.github.com/repos/{UPSTREAM}/releases/latest'
MAC_APP = Path('/Applications/CrossDesk.app')
# Linux releases bundle Slint, but depend on these desktop/runtime libraries.
# Keep them in a private environment on Fedora/SteamOS, never alter SteamOS root.
RUNTIME = {name: '*' for name in (
    'libgl', 'zlib', 'freetype', 'expat', 'pulseaudio-client', 'dbus', 'libxcb',
    'xorg-libx11', 'xorg-libxext', 'xorg-libxrender', 'xorg-libxft',
    'xorg-libxtst', 'xorg-libxrandr', 'xorg-libxfixes', 'xorg-libxcursor',
    'xorg-libxi', 'libstdcxx', 'libgcc', 'libxkbcommon', 'wayland', 'alsa-lib', 'libfuse')}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def latest():
    result = sp.run(['curl', '-fsSL', '--connect-timeout', '20', '--max-time', '90',
                     '--retry', '2', API], capture_output=True, text=True)
    # Public API limits are low on shared networks. Reuse gh authentication if available.
    if result.returncode and shutil.which('gh'):
        result = sp.run(['gh', 'api', f'repos/{UPSTREAM}/releases/latest'],
                        capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('无法获取 CrossDesk 最新版本：' + result.stderr.strip())
    data = json.loads(result.stdout)
    if data.get('draft') or data.get('prerelease'):
        raise RuntimeError('GitHub 返回了非正式版，停止安装')
    return data


def select_asset(release, host, machine):
    tag = release['tag_name']
    if not re.fullmatch(r'v?\d[\w.+-]*', tag):
        raise RuntimeError('不支持的 CrossDesk 版本名称：' + tag)
    arch = {'arm64': 'arm64', 'aarch64': 'arm64',
            'x86_64': 'x64' if host == 'macos' else 'amd64'}.get(machine)
    if not arch:
        raise RuntimeError('CrossDesk 官方没有此架构安装包：' + machine)
    name = f'crossdesk-{"macos" if host == "macos" else "linux"}-{arch}-{tag}'
    name += '.pkg' if host == 'macos' else '.deb'
    matches = [a for a in release['assets'] if a['name'] == name]
    if len(matches) != 1:
        raise RuntimeError('最新正式版缺少官方安装包：' + name)
    asset = matches[0]
    expected = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', expected):
        raise RuntimeError('官方安装包缺少有效 SHA256：' + name)
    if asset['browser_download_url'] != f'https://github.com/{UPSTREAM}/releases/download/{tag}/{name}':
        raise RuntimeError('安装包地址不属于 CrossDesk 官方 GitHub Release')
    return asset, expected.split(':')[1].lower()


def download(root, asset, expected):
    package = root / 'downloads/crossdesk' / asset['name']
    package.parent.mkdir(parents=True, exist_ok=True)
    if package.is_file() and digest(package) == expected:
        print('[SKIP] 复用已校验安装包', flush=True)
        return package
    partial = package.with_suffix(package.suffix + '.part')
    command = ['curl', '-fL', '--progress-bar', '--retry', '3', '--retry-all-errors', '--connect-timeout', '20',
               '--max-time', '1800', '--proto', '=https', '--proto-redir', '=https',
               '--continue-at', '-', '--output', str(partial)]
    if os.environ.get('PROFILE_DOWNLOAD_PROXY'):
        command += ['--proxy', os.environ['PROFILE_DOWNLOAD_PROXY']]
    run([*command, asset['browser_download_url']])
    if digest(partial) != expected:
        partial.unlink()
        raise RuntimeError('CrossDesk SHA256 不匹配；已移除损坏下载，请重试')
    partial.replace(package)
    return package


def extract_deb(package, destination):
    """Read the small ar container without requiring dpkg/ar on SteamOS."""
    with package.open('rb') as stream:
        if stream.read(8) != b'!<arch>\n':
            raise RuntimeError('无效 DEB 安装包')
        while header := stream.read(60):
            if len(header) != 60 or header[58:] != b'`\n':
                raise RuntimeError('损坏的 DEB 文件头')
            size = int(header[48:58].strip())
            name = header[:16].decode('ascii').strip().rstrip('/')
            if name in ('data.tar.xz', 'data.tar.gz', 'data.tar'):
                payload = stream.read(size)
                if len(payload) != size:
                    raise RuntimeError('DEB 数据不完整')
                with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                    # Python 3.12's data filter rejects escaping links and special files.
                    archive.extractall(destination, filter='data')
                return
            stream.seek(size + size % 2, 1)
    raise RuntimeError('DEB 中没有支持的数据归档（tar/xz/gz）')


def runtime_env(app):
    return dict(os.environ, LD_LIBRARY_PATH=str(app / 'payload/usr/lib/crossdesk') + ':' +
                str(app / '.pixi/envs/default/lib'), LD_PRELOAD='', LD_AUDIT='')


def verify_linux(directory, env=None):
    for name in ('crossdesk-bin', 'crossdesk_updater'):
        binary = directory / name
        if not os.access(binary, os.X_OK):
            raise RuntimeError('CrossDesk 安装缺少可执行文件：' + str(binary))
        result = sp.run(['ldd', '-r', str(binary)], env=env, capture_output=True, text=True)
        report = result.stdout + result.stderr
        if result.returncode or re.search(r'not found|undefined symbol:|version .+ not found', report):
            raise RuntimeError('CrossDesk 运行依赖检查失败：\n' + report.strip())


def installed(root, host):
    if host == 'macos':
        return os.access(MAC_APP / 'Contents/MacOS/CrossDesk', os.X_OK)
    if host in ('ubuntu', 'debian'):
        if not os.access('/usr/lib/crossdesk/crossdesk-bin', os.X_OK):
            return False
        result = sp.run(['dpkg-query', '-W', '-f=${Status}', 'crossdesk'], capture_output=True, text=True)
        return result.returncode == 0 and result.stdout == 'install ok installed'
    return (root / 'apps/crossdesk/current/receipt.json').is_file() and os.access(root / 'bin/crossdesk', os.X_OK)


def version(root, host):
    if host == 'macos':
        info = MAC_APP / 'Contents/Info.plist'
        if info.is_file():
            with info.open('rb') as stream:
                return str(plistlib.load(stream).get('CFBundleShortVersionString', '?'))
    elif host in ('ubuntu', 'debian'):
        result = sp.run(['dpkg-query', '-W', '-f=${Version}', 'crossdesk'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    record = root / 'apps/crossdesk/current/receipt.json'
    return json.loads(record.read_text()).get('version', '?') if record.is_file() else '?'


def check(root, host):
    if not installed(root, host):
        return False
    if host in ('ubuntu', 'debian'):
        verify_linux(Path('/usr/lib/crossdesk'), dict(os.environ, LD_LIBRARY_PATH='/usr/lib/crossdesk',
                                                    LD_PRELOAD='', LD_AUDIT=''))
    elif host != 'macos':
        app = (root / 'apps/crossdesk/current').resolve()
        verify_linux(app / 'payload/usr/lib/crossdesk', runtime_env(app))
    return True


def desktop_quote(value):
    # Desktop Exec has its own quoting rules; this is not shell quoting.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def portable(root, package, tag, expected):
    work, _ = resolve(root, 'crossdesk', RUNTIME)
    app = install_environment(root, 'crossdesk', tag + '-' + expected[:12], work)
    extract_deb(package, app / 'payload')
    private = app / 'payload/usr/lib/crossdesk'
    verify_linux(private, runtime_env(app))
    launch = app / 'launch/crossdesk'
    launch.parent.mkdir(exist_ok=True)
    launch.write_text('#!/bin/sh\nset -e\n'
                      'export LD_LIBRARY_PATH=' + shlex.quote(runtime_env(app)['LD_LIBRARY_PATH']) + '\n'
                      'exec ' + shlex.quote(str(private / 'crossdesk-bin')) + ' "$@"\n')
    launch.chmod(0o755)
    return app


def publish_portable(root, app):
    atomic_link(app, root / 'apps/crossdesk/current', root)
    atomic_link(root / 'apps/crossdesk/current/launch/crossdesk', root / 'bin/crossdesk', root)
    directory = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
    directory.mkdir(parents=True, exist_ok=True)
    for source in (app / 'payload/usr/share/applications').glob('crossdesk*.desktop'):
        lines = source.read_text().splitlines()
        icon = root / 'apps/crossdesk/current/payload/usr/share/icons/hicolor/256x256/apps/crossdesk.png'
        lines = ['Exec=' + desktop_quote(root / 'bin/crossdesk') if s.startswith('Exec=') else
                 'Icon=' + str(icon) if s.startswith('Icon=') else s for s in lines]
        (directory / source.name).write_text('\n'.join(lines) + '\n')


def install(root, host, update=False):
    try:
        ready = check(root, host)
    except RuntimeError as error:
        print('[REPAIR] ' + str(error), flush=True)
        ready = False
    if ready and not update:
        print('[SKIP] CrossDesk 已安装；更新请运行 wb crossdesk update')
        return
    print('[CROSSDESK 1/4] 获取官方 GitHub 最新正式版', flush=True)
    release = latest()
    asset, expected = select_asset(release, host, platform.machine())
    tag = release['tag_name']
    if ready and version(root, host).removeprefix('v') == tag.removeprefix('v'):
        print('[SKIP] CrossDesk 已是最新正式版：' + tag)
        return
    print('[CROSSDESK 2/4] 下载并校验 SHA256：' + asset['name'], flush=True)
    package = download(root, asset, expected)
    print('[CROSSDESK 3/4] 安装 ' + tag, flush=True)
    if host == 'macos':
        run(['/usr/sbin/pkgutil', '--check-signature', package])
        run(['/usr/sbin/spctl', '--assess', '--type', 'install', package])
        run(['/usr/bin/sudo', '/usr/sbin/installer', '-verboseR', '-pkg', package, '-target', '/'])
    elif host in ('ubuntu', 'debian'):
        # Official recommendations include the very large CUDA toolkit; keep those optional.
        run(['sudo', 'apt-get', 'install', '-y', '--no-install-recommends', package])
    else:
        app = portable(root, package, tag, expected)
    print('[CROSSDESK 4/4] 检查安装并记录版本', flush=True)
    if host in ('macos', 'ubuntu', 'debian'):
        if not check(root, host):
            raise RuntimeError('官方安装器完成，但 CrossDesk 安装检查未通过')
        if version(root, host).removeprefix('v') != tag.removeprefix('v'):
            raise RuntimeError('官方安装器完成，但 CrossDesk 实际版本与下载版本不一致')
        app = root / 'apps/crossdesk' / (tag + '-' + expected[:12])
        app.mkdir(parents=True, exist_ok=True)
    record = dict(name='crossdesk', version=tag, backend='official-pkg' if host == 'macos' else
                  'official-deb' if host in ('ubuntu', 'debian') else 'official-deb-user',
                  url=asset['browser_download_url'], sha256=expected)
    if (app / 'pixi.lock').is_file():
        record['lock_sha256'] = digest(app / 'pixi.lock')
    receipt(app, record)
    if host in ('macos', 'ubuntu', 'debian'):
        atomic_link(app, root / 'apps/crossdesk/current', root)
    else:
        publish_portable(root, app)
    print('[OK] CrossDesk ' + tag + '；首次使用请从应用菜单打开并授予所需权限。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'check', 'install', 'update'])
    args = parser.parse_args()
    root = Path(os.environ.get('SOFTWARE_HOME', str(Path.home() / 'Software'))).expanduser().resolve()
    host = detect()
    if args.action in ('list', 'check'):
        ready = check(root, host) if args.action == 'check' else installed(root, host)
        print(('[OK] ' if ready else '[MISSING] ') + 'CrossDesk ' + (version(root, host) if ready else ''))
        print('位置：', MAC_APP if host == 'macos' else '/usr/bin/crossdesk' if host in ('ubuntu', 'debian') else root / 'bin/crossdesk')
        return int(args.action == 'check' and not ready)
    install(root, host, update=args.action == 'update')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, sp.CalledProcessError, tarfile.TarError) as error:
        print('crossdesk: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
