"""Visible, per-entry installation of the repository's declarative Brewfile."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .process import elapsed, stream_command
from .ui import Console


def ensure_cask(name, action, available=False, reinstall=False):
    """Upgrade managed apps, install missing apps, and preserve external installs."""
    result = subprocess.run(['brew', 'info', '--json=v2', '--cask', name],
                            env=dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1'),
                            capture_output=True, text=True)
    if result.returncode:
        Console().print(result.stderr or f'Homebrew 查询失败：{name}')
        return result.returncode
    try:
        casks = json.loads(result.stdout)['casks']
        cask = next(item for item in casks
                    if name in (item.get('token'), item.get('full_token')))
        managed = bool(cask['installed'])
    except (ValueError, KeyError, TypeError, StopIteration):
        raise RuntimeError(f'Homebrew 返回了无效的 cask 状态：{name}') from None
    if not managed and available:
        if reinstall:
            raise RuntimeError(f'{name} 已存在但不由 Homebrew 管理；请先处理现有安装，再重装。')
        Console().status('SKIP', f'{name} 已存在，但不由 Homebrew 管理；保留现有应用，未检查或安装更新。', 'warn')
        Console().print('如需更新，请使用原安装渠道。')
        return 0
    operation = 'reinstall' if managed and reinstall else 'upgrade' if managed and action == 'update' else 'install'
    return stream_command(['brew', operation, '--cask', name], label=f'{name} {operation}')


def entries(manifest):
    result = []
    for number, line in enumerate(manifest.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'''(brew|cask)\s+["']([^"']+)["']\s*(?:#.*)?''', line)
        if not match:
            raise RuntimeError(f'{manifest}:{number}: 逐项安装仅支持 brew/cask 名称声明')
        result.append((match[1], match[2], line))
    return result


def install(brew, manifest, action):
    # Validate the entire file before installing anything. Keep Homebrew Bundle
    # responsible for installation/reuse/update semantics and dependencies.
    packages = entries(manifest)
    console = Console()
    total = len(packages)
    ready = changed = 0
    started = time.monotonic()
    console.status('BREW', f'共 {total} 项；依赖由 Homebrew 自动处理，数量不计入此进度', 'key')
    with tempfile.TemporaryDirectory(prefix='workbench-brew-') as directory:
        single = Path(directory) / 'Brewfile'
        for index, (kind, name, declaration) in enumerate(packages, 1):
            label = f'Homebrew {index}/{total} {name}'
            console.status(f'BREW {index}/{total}', f'{name} ({kind})：检查 / ' +
                           ('安装' if action == 'install' else '更新'), 'key')
            item_started = time.monotonic()
            single.write_text(declaration + '\n')
            args = [str(brew), 'bundle']
            file_arg = '--file=' + str(single)
            check_env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1')
            if action == 'install' and subprocess.run(
                    [*args, 'check', '--no-upgrade', file_arg], env=check_env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                ready += 1
                console.status('SKIP', f'{name} 已安装；完成 {index}/{total}', 'ok')
                continue
            code = stream_command([*args, 'install', '--verbose', file_arg,
                                   *(['--no-upgrade'] if action == 'install' else [])], label=label)
            duration = elapsed(time.monotonic() - item_started)
            if code:
                console.status('FAIL', f'{name} 失败（退出码 {code}，{duration}）', 'error')
                console.status('BREW', f'已完成 {index - 1}/{total}：已有 {ready} 项，'
                               f'安装/更新成功 {changed} 项；失败 1 项，未执行 {total - index} 项', 'error')
                console.print('修复后重跑同一命令；install 会跳过已安装项。')
                return code
            changed += 1
            console.status('OK', f'{name} 安装/更新完成（{duration}）；完成 {index}/{total}', 'ok')
    console.status('BREW DONE', f'{total}/{total} 项完成：已有 {ready} 项，'
                   f'安装/更新成功 {changed} 项；耗时 {elapsed(time.monotonic() - started)}', 'ok')
    return 0
