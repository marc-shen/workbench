"""Plan and execute explicit argv commands with serialized writes and run receipts."""
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from .registry import order, supported
from .ui import Console
from .process import elapsed, stream_command

READ_ONLY = {'list', 'check', 'status', 'logs', 'plan', 'show'}
# A GUI can remain open indefinitely. Launching it must neither hold the
# installation lock nor be blocked by another application's open window.
UNLOCKED_ACTIONS = READ_ONLY | {'open'}


def command(spec, action, args, repo, root, platform):
    values = {'python': sys.executable, 'repo': str(repo), 'component': str(spec['_directory']),
              'root': str(root), 'platform': platform}
    entry = spec.get('entrypoints', {}).get(platform, spec['entrypoint'])
    return [part.format(**values) for part in entry] + [action, *args]


def plan(components, name, action, args, repo, root, platform, dependencies=True):
    if name not in components:
        raise RuntimeError('Unknown component: ' + name)
    spec = components[name]
    if action not in spec['actions']:
        raise RuntimeError(name + ': unsupported action ' + action)
    names = order(components, [name]) if dependencies and action in ('install', 'update') else [name]
    steps = []
    providers = set()
    for item in names:
        operation = action if item == name else 'install'
        component = components[item]
        if operation not in component['actions'] or not supported(component, operation, platform):
            raise RuntimeError(f'{item} {operation} is not supported on {platform}')
        if operation in ('install', 'update'):
            installation = component.get('install', {})
            needed = [installation.get('provider')] + installation.get('providers', []) + installation.get('providers_by_platform', {}).get(platform, [])
            for provider in needed:
                if provider not in ('pixi', 'uv') or provider in providers: continue
                providers.add(provider)
                steps.append(dict(component='providers', action='install',
                                  command=command(components['providers'], 'install', [provider], repo, root, platform)))
        arguments = args if item == name else []
        steps.append(dict(component=item, action=operation,
                          command=command(component, operation, arguments, repo, root, platform)))
    return steps


@contextmanager
def lock(root):
    path = root / 'data/workbench/operation.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another workbench operation is running.')
        yield


def execute(steps, repo, root, platform):
    console = Console()
    env = dict(os.environ, SOFTWARE_HOME=str(root), SOFTWARE_USER_HOME=str(root),
               WORKBENCH_HOME=str(repo), PROFILE_PLATFORM=platform, PROFILE_PYTHON=sys.executable,
               PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    for variable, relative in [('UV_TOOL_DIR', 'data/uv/tools'), ('UV_TOOL_BIN_DIR', 'bin'),
                               ('UV_PYTHON_INSTALL_DIR', 'data/uv/python'), ('UV_CACHE_DIR', 'cache/uv'),
                               ('PIXI_CACHE_DIR', 'cache/pixi')]:
        env[variable] = str(root / relative)
    env['PYTHONPATH'] = str(repo / 'lib') + os.pathsep + env.get('PYTHONPATH', '')
    env['PATH'] = str(root / 'bin') + os.pathsep + env.get('PATH', os.defpath)
    if platform == 'macos': env['PATH'] = str(root / 'bin') + ':/opt/homebrew/bin:/usr/local/bin:' + env['PATH']
    if all(step['action'] in UNLOCKED_ACTIONS for step in steps):
        codes = []
        for index, step in enumerate(steps, 1):
            if len(steps) > 1:
                console.status(f'{index}/{len(steps)}', step['component'] + ' ' + step['action'], 'key')
            code = subprocess.run(step['command'], env=env).returncode
            codes.append(code)
            if step['action'] == 'check':
                console.status('OK' if code == 0 else 'PENDING' if code == 2 else 'FAIL',
                               step['component'] + ' check', 'ok' if code == 0 else 'warn' if code == 2 else 'error')
        return max((code if code >= 0 else 128 - code for code in codes), default=0)
    if os.geteuid() == 0:
        raise RuntimeError('Run as the target user; platform installers request sudo only where needed.')
    with lock(root):
        stamp = datetime.datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')
        directory = root / 'data/workbench/runs'
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / (stamp + '.json')
        log = root / 'logs' / ('workbench-' + stamp + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        record = dict(schema=1, platform=platform, started_at=stamp, status='running', log=str(log), steps=[])
        def save():
            temporary = receipt.with_suffix('.tmp')
            temporary.write_text(json.dumps(record, indent=2) + '\n')
            temporary.replace(receipt)
        save()
        console.table([('安装日志', log)])
        try:
            with log.open('w') as stream:
                log.chmod(0o600)
                for index, step in enumerate(steps, 1):
                    console.status(f'{index}/{len(steps)}', step['component'] + ' ' + step['action'], 'key')
                    # Do not store arbitrary arguments: they may contain private URLs/tokens.
                    progress = dict(component=step['component'], action=step['action'], status='running')
                    record['steps'].append(progress); save()
                    started = time.monotonic()
                    code = stream_command(step['command'], env=env, log=stream,
                                          label=step['component'] + ' ' + step['action'])
                    duration = time.monotonic() - started
                    progress.update(returncode=code, status='ok' if code == 0 else 'failed',
                                    elapsed_seconds=round(duration, 2))
                    save()
                    console.status('OK' if code == 0 else 'PENDING' if code == 2 else 'FAIL',
                                   step['component'] + ' ' + step['action'] + f'（{elapsed(duration)}）',
                                   'ok' if code == 0 else 'warn' if code == 2 else 'error')
                    if code:
                        record['status'] = 'pending' if code == 2 else 'failed'; save()
                        console.print('已停止。解决问题后可重跑同一命令。')
                        console.table([('日志', log)])
                        return code
            record['status'] = 'ok'; save()
            console.status('DONE', str(len(steps)) + ' 个步骤完成', 'ok')
            console.table([('运行记录', receipt)])
            return 0
        except BaseException:
            record['status'] = 'interrupted'; save()
            raise
