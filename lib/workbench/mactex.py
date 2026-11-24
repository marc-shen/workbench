"""Optional MacTeX installation from the official CTAN-distributed PKG."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from .process import stream_command
from .ui import Console

TEXBIN = Path('/Library/TeX/texbin/xelatex')
TEXSHOP = Path('/Applications/TeX/TeXShop.app')
CURL = ['/usr/bin/curl', '--fail', '--location', '--show-error', '--retry', '3',
        '--connect-timeout', '20', '--proto', '=https', '--proto-redir', '=https']


def installed():
    # Recognize both official-PKG and existing Homebrew installations, but not
    # BasicTeX or a receipt left behind by an incomplete installation.
    if not os.access(TEXBIN, os.X_OK) or not TEXSHOP.is_dir():
        return False
    result = subprocess.run(['/usr/sbin/pkgutil', '--pkgs'], capture_output=True, text=True)
    if result.returncode or not re.search(r'^org\.tug\.mactex\.texlive\d+$', result.stdout, re.M):
        return False
    try:
        return subprocess.run([str(TEXBIN), '--version'], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def manual_command(root):
    prefix = [] if root == Path.home() / 'Software' else ['--prefix', str(root)]
    return shlex.join(['workbench', *prefix, 'texlive', 'install'])


def confirm(root):
    console = Console()
    manual = manual_command(root)
    console.status('OPTIONAL', '其他组件已安装并检查完成。MacTeX 是可选的大型下载。', 'key')
    console.print('稍后手动安装：' + manual)
    if not sys.stdin.isatty():
        console.print('非交互运行：跳过 MacTeX。')
        return False
    if os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY'):
        try:
            return input('现在下载并安装官方 MacTeX PKG？[y/N] ').strip().lower() in ('y', 'yes')
        except EOFError:
            return False
    script = ('on run argv\n'
              'set answer to display dialog (item 1 of argv) with title "Workbench · MacTeX" '
              'buttons {"稍后安装", "现在安装"} default button "稍后安装" cancel button "稍后安装"\n'
              'return button returned of answer\nend run')
    message = ('其他组件已安装并检查完成。\n\n是否现在下载并安装官方 MacTeX PKG？'
               '\n安装包较大，可能需要较长时间和管理员授权。\n\n稍后手动安装：\n' + manual)
    result = subprocess.run(['/usr/bin/osascript', '-e', script, message], capture_output=True, text=True)
    # Cancellation or an unavailable GUI always leaves this optional install off.
    return result.returncode == 0 and result.stdout.strip() == '现在安装'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha512').hexdigest()


def install_package(root, spec, update=False):
    console = Console()
    console.status('MACTEX 1/4', '获取官方 PKG 的 SHA512 校验值', 'key')
    result = subprocess.run([*CURL, '--silent', '--max-time', '120', spec['checksum_url']],
                            capture_output=True, text=True)
    if result.returncode:
        console.print(result.stderr)
        return result.returncode
    fields = result.stdout.split()
    if not fields or not re.fullmatch(r'[0-9a-fA-F]{128}', fields[0]):
        raise RuntimeError('官方 MacTeX SHA512 校验文件格式无效')
    expected = fields[0].lower()
    receipt = root / 'data/workbench/mactex.json'
    if update and receipt.is_file() and installed():
        if json.loads(receipt.read_text()).get('sha512') == expected:
            console.status('SKIP', '已安装当前官方 MacTeX PKG', 'ok')
            return 0
    package = root / 'downloads/archives' / f'mactex-{expected[:16]}.pkg'
    package.parent.mkdir(parents=True, exist_ok=True)
    console.status('MACTEX 2/4', '下载官方 PKG（支持断点续传）并校验 SHA512', 'key')
    if not package.is_file() or digest(package) != expected:
        partial = package.with_suffix('.pkg.part')
        code = stream_command([*CURL, '--progress-bar', '--continue-at', '-',
                               '--output', str(partial), spec['package_url']], label='MacTeX 下载')
        if code:
            console.print('下载未完成，重跑可续传：' + manual_command(root))
            return code
        console.status('VERIFY', '下载完成，正在计算 SHA512…', 'key')
        if digest(partial) != expected:
            partial.unlink()
            raise RuntimeError('MacTeX SHA512 不匹配；已移除损坏的下载，请重试')
        partial.replace(package)
    else:
        console.status('SKIP', '复用已校验的官方 PKG 下载缓存', 'ok')
    console.status('MACTEX 3/4', '验证安装包签名与 Gatekeeper', 'key')
    for command in (['/usr/sbin/pkgutil', '--check-signature', str(package)],
                    ['/usr/sbin/spctl', '--assess', '--type', 'install', str(package)]):
        code = stream_command(command, label='MacTeX 签名验证')
        if code:
            return code
    console.status('MACTEX 4/4', '安装官方 PKG；sudo 可能要求输入管理员密码', 'key')
    code = stream_command(['/usr/bin/sudo', '/usr/sbin/installer', '-verboseR', '-pkg', str(package),
                           '-target', '/'], label='MacTeX PKG 安装')
    if code:
        return code
    if not installed():
        raise RuntimeError('PKG 安装结束，但 MacTeX 安装检查未通过；请运行 workbench texlive check')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(backend='official-pkg', url=spec['package_url'],
                                       sha512=expected), indent=2) + '\n')
    console.status('OK', 'MacTeX 安装完成；重新打开终端使 TeX 命令的 PATH 生效', 'ok')
    return 0


def mactex_action(action, root, spec):
    console = Console()
    ready = installed()
    if action in ('list', 'check'):
        console.status('OK' if ready else 'MISSING', 'MacTeX（官方 PKG）', 'ok' if ready else 'warn')
        return int(action == 'check' and not ready)
    if ready and action in ('install', 'offer'):
        console.status('SKIP', 'MacTeX 已安装，无需重复下载', 'ok')
        return 0
    if action == 'offer' and not confirm(root):
        console.status('SKIP', '已跳过 MacTeX；其他组件已就绪。手动安装：' + manual_command(root), 'warn')
        return 0
    if action not in ('install', 'update', 'offer'):
        raise RuntimeError('Unsupported MacTeX action: ' + action)
    return install_package(root, spec, update=action == 'update')
