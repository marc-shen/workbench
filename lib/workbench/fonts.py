"""Manage only the font casks declared in the macOS platform Brewfile."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .brew import entries, install
from .process import stream_command
from .ui import Console


def font_action(action, args, repo, platform):
    if args:
        raise RuntimeError('font takes no targets')
    packages = [(kind, name, declaration) for kind, name, declaration
                in entries(repo / 'platforms/macos/Brewfile')
                if kind == 'cask' and name.startswith('font-')]
    if not packages:
        raise RuntimeError('No font casks declared in platforms/macos/Brewfile')
    names = [name for _, name, _ in packages]
    console = Console()
    if platform != 'macos':
        console.table([(name, 'macOS Homebrew 字体') for name in names])
        return 0 if action == 'list' else 1
    brew = shutil.which('brew')
    if not brew:
        console.table([(name, '[MISSING] Homebrew 未安装') for name in names])
        console.print('请先运行 workbench platform install。')
        return 0 if action == 'list' else 1
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1')
    if action in ('install', 'update'):
        with tempfile.TemporaryDirectory(prefix='workbench-fonts-') as folder:
            manifest = Path(folder) / 'Brewfile'
            manifest.write_text(''.join(declaration + '\n' for _, _, declaration in packages))
            return install(brew, manifest, action)
    if action == 'outdated':
        console.status('FONT', '刷新 Homebrew 更新索引；不安装或升级软件', 'key')
        code = stream_command([brew, 'update'], label='字体更新索引')
        if code:
            return code
    result = subprocess.run([brew, 'list', '--cask', '--versions'], env=env, capture_output=True, text=True)
    if result.returncode:
        console.print(result.stderr)
        return result.returncode
    versions = {parts[0]: ' '.join(parts[1:]) for line in result.stdout.splitlines()
                if len(parts := line.split()) > 1}
    available = {}
    installed_names = [name for name in names if name in versions]
    if action == 'outdated' and installed_names:
        result = subprocess.run([brew, 'outdated', '--cask', '--greedy', '--json=v2', *installed_names],
                                env=env, capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
            available = {item['name']: str(item['current_version']) for item in data['casks']}
        except (ValueError, KeyError, TypeError):
            raise RuntimeError('Homebrew 字体更新查询失败：' + (result.stderr.strip() or '返回了无效 JSON')) from None
        # brew outdated returns 1 when named casks have updates; that is not a query error.
        if result.returncode and not (result.returncode == 1 and available):
            console.print(result.stderr or 'Homebrew 字体更新查询失败')
            return result.returncode
    console.title('工作站字体（Homebrew 管理）')
    for name in names:
        if name not in versions:
            console.status('MISSING', name + '：未安装或未由 Homebrew 管理', 'warn')
        elif name in available:
            console.status('UPDATE', f'{name}：{versions[name]} → {available[name]}', 'warn')
        else:
            suffix = '；已是最新版本' if action == 'outdated' else ''
            console.status('OK', f'{name}：{versions[name]}{suffix}', 'ok')
    if available:
        console.print('安装字体更新：workbench font update')
    if len(installed_names) != len(names):
        console.print('补齐字体：workbench font install')
    if action in ('list', 'check'):
        console.print('检查可用更新：workbench font outdated')
    return int(action == 'check' and len(installed_names) != len(names))
