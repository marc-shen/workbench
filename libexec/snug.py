#!/usr/bin/env python3
"""Install the signed universal Snug app from its official stable GitHub DMG."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess as sp
import sys
import tempfile

from crossdesk import download, latest
from software import atomic_link, receipt, run

UPSTREAM = 'zephyr4123/snug'
APP = Path('/Applications/Snug.app')
BUNDLE_ID = 'com.zephyr4123.snug'


def compatible():
    if platform.system() != 'Darwin' or platform.machine() not in ('arm64', 'x86_64'):
        raise RuntimeError('Snug 官方目前仅支持 Apple 芯片/Intel Mac，不提供 Linux 安装包')
    if int(platform.mac_ver()[0].split('.')[0] or '0') < 14:
        raise RuntimeError('Snug 需要 macOS 14 或更新版本')


def app_version(app):
    try:
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        version = info['CFBundleShortVersionString']
        if (info['CFBundleIdentifier'] != BUNDLE_ID or info['CFBundleExecutable'] != 'Snug'
                or not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version)
                or not os.access(app / 'Contents/MacOS/Snug', os.X_OK)):
            return None
        return version
    except (OSError, ValueError, KeyError, TypeError):
        return None


def verify(app, expected=None):
    version = app_version(app)
    if not version or expected is not None and version != expected:
        raise RuntimeError('Snug 应用身份或版本检查失败：' + str(app))
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    run(['/usr/sbin/spctl', '--assess', '--type', 'execute', app])
    return version


def select_asset(release):
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v?\d+\.\d+\.\d+', tag):
        raise RuntimeError('Snug 最新正式版元数据无效')
    name = 'Snug-' + tag.removeprefix('v') + '.dmg'
    matches = [asset for asset in release.get('assets', []) if asset['name'] == name]
    if len(matches) != 1:
        raise RuntimeError('Snug 最新正式版缺少唯一的通用安装包：' + name)
    asset = matches[0]
    expected = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', expected):
        raise RuntimeError('Snug 官方安装包缺少有效 SHA256')
    if asset['browser_download_url'] != f'https://github.com/{UPSTREAM}/releases/download/{tag}/{name}':
        raise RuntimeError('Snug 安装包地址不属于官方 GitHub Release')
    return asset, expected.split(':')[1].lower()


@contextmanager
def mounted(package):
    with tempfile.TemporaryDirectory(prefix='workbench-snug-mount-') as directory:
        mount = Path(directory) / 'volume'
        mount.mkdir()
        run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', mount, package])
        try:
            yield mount / 'Snug.app'
        finally:
            run(['/usr/bin/hdiutil', 'detach', mount])


def admin(args, **kwargs):
    return run(['/usr/bin/sudo', *([] if sys.stdin.isatty() else ['-n']), '--', *args], **kwargs)


def replace_app(source, version):
    # Stage on the same filesystem, then rename the complete bundle. Keep the
    # previous bundle until the installed copy passes validation; roll back on failure.
    result = admin(['/usr/bin/mktemp', '-d', str(APP.parent / '.workbench-snug.XXXXXX')],
                   capture_output=True, text=True)
    stage = Path(result.stdout.strip())
    if stage.parent != APP.parent or not stage.name.startswith('.workbench-snug.'):
        raise RuntimeError('Snug 安装暂存目录无效')
    candidate, previous = stage / 'Snug.app', stage / 'previous.app'
    moved_old = published = success = False
    try:
        admin(['/bin/chmod', '755', stage])
        admin(['/usr/bin/ditto', source, candidate])
        verify(candidate, version)
        if APP.exists() or APP.is_symlink():
            admin(['/bin/mv', APP, previous])
            moved_old = True
        try:
            admin(['/bin/mv', candidate, APP])
            published = True
            verify(APP, version)
            success = True
        except BaseException:
            if published:
                admin(['/bin/mv', APP, candidate])
            if moved_old:
                admin(['/bin/mv', previous, APP])
                moved_old = False
            raise
    finally:
        # If rollback itself fails, retain the only copy of the previous app.
        if moved_old and previous.exists() and not success:
            print('旧应用保留在：' + str(previous), file=sys.stderr)
        else:
            admin(['/bin/rm', '-rf', '--', stage])


def install(root, update=False):
    compatible()
    current = app_version(APP)
    if current and not update:
        verify(APP)
        print('[SKIP] Snug 已安装；更新请运行 wb snug update')
        return
    if (APP.exists() or APP.is_symlink()) and not current:
        raise RuntimeError('已有 Snug.app 无法确认身份；请先在本机检查，脚本不会覆盖该应用')
    print('[SNUG 1/4] 获取官方 GitHub 最新正式版', flush=True)
    release = latest(UPSTREAM, 'Snug')
    asset, expected = select_asset(release)
    version = release['tag_name'].removeprefix('v')
    if current and tuple(map(int, current.split('.'))) >= tuple(map(int, version.split('.'))):
        verify(APP)
        print('[SKIP] Snug 已是当前或更新版本：' + current)
        return
    running = sp.run(['/usr/bin/pgrep', '-x', 'Snug'], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    if running.returncode == 0:
        raise RuntimeError('请先从菜单栏退出 Snug，再重跑更新；不会中断当前远程屏幕')
    if running.returncode != 1:
        raise RuntimeError('无法确认 Snug 是否正在运行，停止安装')
    print('[SNUG 2/4] 下载并校验 SHA256：' + asset['name'], flush=True)
    package = download(root, asset, expected, component='snug')
    print('[SNUG 3/4] 验证签名、公证并安装到 /Applications', flush=True)
    with mounted(package) as source:
        verify(source, version)
        replace_app(source, version)
    print('[SNUG 4/4] 记录安装版本', flush=True)
    record = root / 'apps/snug' / (version + '-' + expected[:12])
    record.mkdir(parents=True, exist_ok=True)
    receipt(record, dict(name='snug', version=version, backend='official-dmg',
                         url=asset['browser_download_url'], sha256=expected))
    atomic_link(record, root / 'apps/snug/current', root)
    print('[OK] Snug ' + version + '；首次打开后授予辅助功能权限。使用 CrossDesk 时，'
          '在 Snug 中开启“提前准备虚拟屏”，再手动重启 CrossDesk。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'check', 'install', 'update'])
    args = parser.parse_args()
    root = Path(os.environ.get('SOFTWARE_HOME', str(Path.home() / 'Software'))).expanduser().resolve()
    if args.action == 'list':
        print('Snug: ' + (app_version(APP) or '未安装') + '\n位置：' + str(APP))
        return 0
    if args.action == 'check':
        compatible()
        verify(APP)
        return 0
    install(root, args.action == 'update')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError, sp.SubprocessError) as error:
        print('snug: ' + str(error), file=sys.stderr)
        sys.exit(1)
