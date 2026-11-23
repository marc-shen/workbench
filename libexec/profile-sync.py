#!/usr/bin/env python3
"""Apply a personal checkout, recording successful scopes independently of Git pull."""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import sys
from config_data import tool_specs, language_specs, plugin_sources, specifications

REPO = Path(__file__).resolve().parents[1]
SCOPES = ('shell', 'emacs', 'helix', 'network')

def git(*args):
    if not (REPO / '.git').exists():
        if args == ('rev-parse', 'HEAD'): return 'source-archive'
        if args == ('status', '--porcelain'): return ''
        raise RuntimeError('Git update requires a Git checkout; use workbench profile apply for a source archive.')
    return sp.check_output(['git', '-C', str(REPO), *args], text=True).strip()

def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)

def snapshot():
    if (REPO / '.git').exists():
        paths = sp.check_output(['git', '-C', str(REPO), 'ls-files', '-co', '--exclude-standard', '-z']).split(b'\0')
    else:
        source_roots = {'bin','lib','libexec','commands','providers','components','profiles','platforms','config','catalog','examples','docs','tests','bootstrap.sh','install.sh','README.md'}
        paths = [os.fsencode(str(path.relative_to(REPO))) for path in REPO.rglob('*')
                 if path.relative_to(REPO).parts[0] in source_roots and not path.is_symlink() and '__pycache__' not in path.parts]

    result = {}
    for raw in paths:
        if not raw: continue
        name = os.fsdecode(raw)
        path = REPO / name
        if name.startswith('config/emacs/') and name.split('/')[2] in ('.cache','elpa','eln-cache','var','tree-sitter','custom.el','projects','projects.eld','tramp','auto-save-list','eshell','transient','log','agent','tramp-rpc'): continue
        if any(parent.is_symlink() for parent in path.parents if parent != REPO and REPO in parent.parents): continue
        if path.is_symlink(): payload = os.readlink(path).encode()
        elif path.is_file(): payload = path.read_bytes()
        else: continue
        result[name] = hashlib.sha256(payload + str(path.lstat().st_mode & 0o777).encode()).hexdigest()
    return result

def scope_files(files, scope):
    prefixes = {
        'shell': ('config/shell/', 'config/starship/', 'config/wezterm/'),
        'emacs': ('config/emacs/', 'platforms/'),
        'helix': ('config/helix/',),
        'network': ('platforms/macos/network/', 'libexec/macos_network.py', 'docs/macos-network', 'platforms/steamdeck/network/', 'docs/steamdeck-network', 'docs/steamdeck-mihomo'),
    }
    return {k: v for k, v in files.items() if k.startswith(('bin/', 'lib/', 'libexec/', 'commands/', 'providers/', 'components/', 'profiles/', 'platforms/', 'bootstrap.sh') + prefixes[scope])}

def changed_files(old, new):
    return sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))

def executable(root, name):
    return shutil.which(name, path=str(root / 'bin') + os.pathsep + os.environ.get('PATH', os.defpath))

def missing_dependencies(root, scopes):
    missing = {'managers': [], 'tools': [], 'plugins': [], 'languages': [], 'native': []}
    for name in ('pixi', 'uv'):
        if scopes != ['network'] and not os.access(root / 'bin' / name, os.X_OK): missing['managers'].append(name)
    if any(scope in scopes for scope in ('shell', 'helix', 'emacs')):
        software = specifications(REPO)
        for name, spec in tool_specs(REPO).items():
            wanted = ('shell' in scopes and software[name]['category'] in ('cli', 'terminal')) or (name == 'helix' and 'helix' in scopes) or (name == 'node' and 'emacs' in scopes)
            if not wanted: continue
            absent = any(not executable(root, c) for c in spec['commands'])
            if spec.get('backend') == 'nvm':
                installed = read_json(root / 'apps/node/current/receipt.json', {})
                absent |= any(installed.get(key) != spec[key] for key in ('backend', 'version', 'nvm_version'))
                absent |= not (root / 'data/nvm/nvm.sh').is_file()
                absent |= any(not os.access(root / 'apps/node/current/bin' / c, os.X_OK) or
                              (root / 'bin' / c).resolve() != (root / 'apps/node/current/bin' / c).resolve()
                              for c in spec['commands'])
            if absent: missing['tools'].append(name)
    if 'shell' in scopes:
        for name in plugin_sources(REPO):
            if not (root / 'share/zsh/plugins' / name / '.git').exists(): missing['plugins'].append(name)
    if 'emacs' in scopes or 'helix' in scopes:
        for name, spec in language_specs(REPO).items():
            if any(not executable(root, c) for c in spec['commands']): missing['languages'].append(name)
    if 'emacs' in scopes:
        if not executable(root, 'emacsclient'): missing['native'].append('Emacs (run workbench emacs install runtime)')
    if 'network' in scopes:
        if sys.platform == 'darwin':
            from macos_network import pending
        else:
            from deck_network import pending
        missing['network'] = pending(root)
    return missing

class Runner:
    def __init__(self, root, attempt):
        self.attempt = attempt
        self.path = root / 'logs' / ('profile-' + attempt['started_at'].replace(':', '-') + '.log')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def run(self, name, args):
        args = list(map(str, args))
        print('->', name, flush=True)
        step = {'name': name, 'command': args, 'status': 'running'}
        self.attempt['steps'].append(step)
        with self.path.open('a') as log:
            log.write('\n' + name + '\n')
            process = sp.Popen(args, stdout=sp.PIPE, stderr=sp.STDOUT, text=True)
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
            code = process.wait()
            process.stdout.close()
        step.update(status='ok' if code == 0 else 'failed', returncode=code)
        if code: raise sp.CalledProcessError(code, args)

@contextmanager
def deployment_lock(root):
    path = root / 'data/profile/apply.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another profile deployment is running.')
        yield

def verify_links(root, scopes):
    links = {root / 'config/workbench': REPO}
    home = Path.home()
    if 'shell' in scopes:
        links.update({home / '.zshrc': REPO / 'config/shell/zshrc', home / '.wezterm.lua': REPO / 'config/wezterm/wezterm.lua'})
    if 'helix' in scopes or 'shell' in scopes: links[home / '.config/helix'] = REPO / 'config/helix'
    if 'emacs' in scopes:
        links[home / '.emacs.d'] = REPO / 'config/emacs'
        for command in ('em', 'ec', 'ediff'): links[root / 'bin' / command] = REPO / 'config/emacs' / command
    for target, source in links.items():
        if not target.is_symlink() or target.resolve() != source.resolve():
            raise RuntimeError('Incorrect deployment link: ' + str(target))
    return len(links)

def install_missing(root, scopes, runner):
    missing = missing_dependencies(root, scopes)
    for manager in missing['managers']:
        runner.run('install manager ' + manager, [sys.executable, REPO / 'libexec/software.py', 'managers', manager])
    if missing['tools']:
        runner.run('install missing tools', [sys.executable, REPO / 'libexec/software.py', 'tools', *missing['tools']])
    if missing['plugins']:
        runner.run('install missing plugins', [sys.executable, REPO / 'libexec/plugins.py', '--missing-only'])
    if ('emacs' in scopes or 'helix' in scopes) and bool(language_specs(REPO)):
        # Recheck existing servers too: a previous install may have created its
        # executable but failed verification. Never mistake that for recovery.
        runner.run('install missing and verify language servers', [sys.executable, REPO / 'libexec/language-servers.py', 'install', '--probe'])

def report_changes(scopes, state, files, root):
    changes = {}
    for scope in scopes:
        old = state.get('scopes', {}).get(scope, {}).get('files', {})
        changes[scope] = changed_files(old, scope_files(files, scope))
        print(f'{scope}: {len(changes[scope])} changed/new/removed files since last applied snapshot')
        for name in changes[scope][:12]: print('  ', name)
        if len(changes[scope]) > 12: print('  ...')
    for group, names in missing_dependencies(root, scopes).items():
        if names: print('Missing ' + group + ': ' + ', '.join(names))
    return changes

def apply_unlocked(args, root):
    from deck_network import is_steamdeck
    scopes = list(SCOPES) if args.only == 'all' else [args.only]
    if not is_steamdeck() and sys.platform != 'darwin':
        if args.only == 'network': raise RuntimeError('The network scope is supported on macOS and Steam Deck.')
        scopes = [s for s in scopes if s != 'network']
    # Shell deployment also manages Helix, matching deploy.py's existing contract.
    if args.only == 'shell': scopes.append('helix')
    state_path = root / 'data/profile/deployment.json'
    state = read_json(state_path, {'schema': 1, 'scopes': {}})
    files = snapshot()
    changes = report_changes(scopes, state, files, root)
    if args.plan:
        print('PLAN: repair links, deploy the Mac Dock bundle, write service definitions without restarting Emacs, verify links.')
        if 'network' in scopes:
            print('PLAN: install Mihomo/MetaCubeXD and platform-native Tailscale; keep private data local.')
        print('Missing dependencies will ' + ('be installed.' if args.install_missing else 'be reported only.'))
        return 0
    attempt = {'started_at': datetime.datetime.now().astimezone().isoformat(timespec='microseconds'),
               'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain')),
               'scopes': scopes, 'steps': [], 'status': 'running'}
    runner = Runner(root, attempt)
    try:
        if args.only != 'network':
            runner.run('deploy configuration', [sys.executable, REPO / 'libexec/deploy.py', 'link', '--prefix', root, '--only', args.only])
        elif not args.install_missing:
            from software import atomic_link
            atomic_link(REPO, root / 'config/workbench', root)
        if 'emacs' in scopes:
            runner.run('write Emacs service (keep live daemon)', [sys.executable, REPO / 'libexec/deploy.py', 'service', '--prefix', root, '--no-start'])
        if args.install_missing: install_missing(root, scopes, runner)
        if 'network' in scopes and args.install_missing:
            if sys.platform == 'darwin':
                for service in ('mihomo', 'tailscale'):
                    runner.run('deploy Mac '+service, [sys.executable, REPO / 'libexec/macos_network.py', '--prefix', root, service, 'install'])
            else:
                runner.run('deploy Deck network clients', [sys.executable, REPO / 'libexec/deck_network.py', '--prefix', root, 'all', 'prepare'])
        print('Verified configuration links:', verify_links(root, scopes))
        pending = missing_dependencies(root, scopes)
        attempt['pending_dependencies'] = pending
        attempt['status'] = 'pending' if any(pending.values()) else 'ok'
        for scope in scopes:
            state.setdefault('scopes', {})[scope] = {'commit': attempt['commit'], 'dirty': attempt['dirty'],
                'files': scope_files(files, scope), 'applied_at': attempt['started_at']}
        if args.only == 'all' and attempt['status'] == 'ok':
            state['last_successful_commit'] = attempt['commit']
            state['last_successful_dirty'] = attempt['dirty']
        if changes.get('shell'): print('Reload: open a new terminal for updated shell configuration.')
        if changes.get('emacs'): print('Reload pending: save work and restart Emacs for Lisp/environment changes; no daemon was stopped. Service definition changes require service reload/logout.')
        if changes.get('helix'): print('Reload: restart Helix or use :config-reload.')
        if any(pending.values()):
            print('Configuration deployed; dependencies remain missing:', json.dumps(pending))
            if pending.get('network'):
                print('Network: see docs/macos-network.md or docs/steamdeck-network.md; complete local administrator/login steps.')
            if any(names for group, names in pending.items() if group != 'network'):
                print('Run workbench profile apply to install missing managed dependencies; missing native Emacs needs workbench emacs install runtime. For the full environment, use workbench system install.')
        print('Deployment log:', runner.path)
        return 2 if attempt['status'] == 'pending' else 0
    except Exception as error:
        attempt.update(status='failed', error=str(error))
        raise
    finally:
        attempt['log'] = str(runner.path)
        state.update(repo=str(REPO), last_attempt=attempt)
        atomic_json(state_path, state)

def apply(args, root):
    if args.plan:
        return apply_unlocked(args, root)
    with deployment_lock(root):
        return apply_unlocked(args, root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply', 'sync'])
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--only', choices=['all', *SCOPES], default='all')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--config-only', dest='install_missing', action='store_false')
    parser.set_defaults(install_missing=True)
    args = parser.parse_args()
    root = args.prefix.expanduser().absolute()
    os.environ['SOFTWARE_HOME'] = str(root)
    os.environ['PATH'] = str(root / 'bin') + os.pathsep + os.environ.get('PATH', os.defpath)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    if args.action == 'sync':
        if git('status', '--porcelain'):
            raise RuntimeError('Checkout has local changes. Commit or back them up before sync; no auto-stash/reset is performed.')
        if args.plan:
            print('PLAN: git pull --ff-only, then apply the newly pulled scripts. No fetch performed in preview.')
            return apply(args, root)
        git('rev-parse', '--abbrev-ref', '@{upstream}')
        with deployment_lock(root):
            sp.run(['git', '-C', str(REPO), 'pull', '--ff-only'], check=True)
        # Execute the UPDATED entry point, not this pre-pull Python module.
        command = [sys.executable, str(REPO / 'libexec/profile_commands.py'), 'apply', '--prefix', str(root), '--only', args.only]
        if not args.install_missing: command.append('--config-only')
        os.execv(sys.executable, command)
    return apply(args, root)

if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, sp.CalledProcessError) as error:
        print('profile:', error, file=sys.stderr)
        sys.exit(1)
