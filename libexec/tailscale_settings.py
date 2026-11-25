"""Explicit Tailscale preferences; initialize defaults once, then preserve choices."""
import argparse
import json
from pathlib import Path
import subprocess as sp

from config_data import read

SPEC = read(Path(__file__).resolve().parents[1] / 'components/tailscale/component.toml')
DEFAULTS = SPEC['settings']['defaults']


def boolean(value):
    if value not in ('true', 'false'):
        raise argparse.ArgumentTypeError('请输入 true 或 false')
    return value == 'true'


def exit_node(value):
    if value in ('false', ''):
        return False
    if value == 'true' or value.startswith('-') or any(c.isspace() for c in value):
        raise argparse.ArgumentTypeError('请提供出口节点的 IP/名称，或 false；提供本机出口请用 --advertise-exit-node')
    return value


def parse(argv):
    parser = argparse.ArgumentParser(prog='wb tailscale set', allow_abbrev=False)
    parser.add_argument('--accept-dns', type=boolean)
    parser.add_argument('--accept-routes', type=boolean)
    parser.add_argument('--advertise-exit-node', type=boolean)
    parser.add_argument('--exit-node', type=exit_node)
    options = {key.replace('_', '-'): value for key, value in vars(parser.parse_args(argv)).items() if value is not None}
    if not options:
        parser.error('请指定至少一个设置；查看当前值使用 wb tailscale show')
    if options.get('advertise-exit-node') is True and options.get('exit-node') not in (None, False):
        parser.error('不能同时让本机提供出口并使用其他出口节点')
    return options


def marker(root):
    return root / 'config/tailscale/defaults-applied.json'


def flags(settings):
    return ['--' + key + '=' + ('' if key == 'exit-node' and value is False else
                               str(value).lower() if isinstance(value, bool) else value)
            for key, value in settings.items()]


def apply(root, command, privileged, changes=None, *, marker_path=None):
    """The daemon persists values. The marker only prevents resetting user choices."""
    path = marker(root) if marker_path is None else marker_path
    first = not path.is_file()
    settings = {**DEFAULTS, **(changes or {})} if first else dict(changes or {})
    if not settings:
        return
    try:
        privileged([*command, 'set', *flags(settings)])
    except sp.CalledProcessError as error:
        raise RuntimeError('Tailscale 设置失败（退出码 ' + str(error.returncode) + '）；未记录为已完成。') from error
    # Never mark a failed CLI call as configured. Do not store keys, peers or URLs.
    if first:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'schema': 1, 'defaults': DEFAULTS}, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(path)
    print('Tailscale 设置已生效：' + ', '.join(key + '=' + str(value).lower() for key, value in settings.items()))


def preferences(command):
    result = sp.run([*map(str, command), 'debug', 'prefs'], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('无法读取 Tailscale 设置；请先运行 wb tailscale start，再运行 wb tailscale show')
    try:
        prefs = json.loads(result.stdout)
        dns = prefs['CorpDNS']
        if not isinstance(dns, bool):
            raise ValueError('CorpDNS is not a boolean')
        if 'RouteAll' in prefs and not isinstance(prefs['RouteAll'], bool):
            raise ValueError('RouteAll is not a boolean')
        routes = prefs.get('AdvertiseRoutes') or []
        if not isinstance(routes, list):
            raise ValueError('AdvertiseRoutes is not a list')
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('Tailscale 返回了无法识别的设置格式') from None
    return prefs


def values(prefs):
    routes = prefs.get('AdvertiseRoutes') or []
    selected = {'accept-dns': prefs['CorpDNS'],
            'advertise-exit-node': '0.0.0.0/0' in routes or '::/0' in routes,
            'exit-node': prefs.get('ExitNodeIP') or prefs.get('ExitNodeID') or False}
    if 'RouteAll' in prefs:
        selected['accept-routes'] = prefs['RouteAll']
    return selected


def login_flags(command):
    # Initial `up` creates preferences from its flag defaults. Explicitly carry
    # these values across login instead of silently restoring accept-dns=true.
    prefs = preferences(command)
    selected = values(prefs)
    if prefs.get('ExitNodeID') and not prefs.get('ExitNodeIP'):
        result = sp.run([*map(str, command), 'status', '--json'], capture_output=True, text=True, timeout=15)
        try:
            peers = json.loads(result.stdout).get('Peer') or {}
            peer = next(p for p in peers.values() if p.get('ID') == prefs['ExitNodeID'])
            selected['exit-node'] = peer['TailscaleIPs'][0]
        except (ValueError, KeyError, StopIteration, IndexError, TypeError):
            raise RuntimeError('无法解析已选出口节点；请先运行 wb tailscale set --exit-node=false，再登录。') from None
    result = flags(selected)
    if prefs.get('OperatorUser'):
        result.append('--operator=' + prefs['OperatorUser'])
    return result


def show(command):
    # debug prefs may contain identity material: print only these selected fields.
    selected = values(preferences(command))
    for key, value in selected.items():
        print(key + ': ' + (str(value).lower() if isinstance(value, bool) else value))
