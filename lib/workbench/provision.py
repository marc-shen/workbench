"""Native platform integration, kept separate from reusable component scheduling."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import sys
from .builtin import run, backend, native_installed
from .data import read


def packages(repo, platform):
    return read(repo / 'platforms' / platform / 'platform.toml').get('packages', [])


def missing_packages(names, platform):
    missing = []
    for name in names:
        command = ['rpm', '-q', '--whatprovides', name] if platform == 'fedora' else ['dpkg-query', '-W', '-f=${Status}', name]
        result = sp.run(command, capture_output=True, text=True)
        if result.returncode or (platform != 'fedora' and result.stdout != 'install ok installed'):
            missing.append(name)
    return missing


def platform_action(action, args, repo, root, platform):
    if args: raise RuntimeError('platform takes no targets')
    spec = read(repo / 'platforms' / platform / 'platform.toml')
    if action == 'list':
        print(json.dumps(spec, ensure_ascii=False, indent=2)); return 0
    if platform == 'steamdeck':
        missing = [name for name in ('git', 'python3', 'curl', 'tar') if not shutil.which(name)]
        print('SteamOS: root stays read-only; build dependencies are installed through user-owned Pixi environments.')
        if missing: raise RuntimeError('SteamOS prerequisites missing: ' + ', '.join(missing))
        return 0
    if platform == 'macos':
        brew = shutil.which('brew') or next((str(Path(p) / 'bin/brew') for p in ('/opt/homebrew', '/usr/local') if (Path(p) / 'bin/brew').is_file()), None)
        if not brew:
            if action == 'check':
                print('MISSING: Homebrew'); return 1
            installer = root / 'downloads/installers/homebrew.sh'
            installer.parent.mkdir(parents=True, exist_ok=True)
            code = run('curl', '-fLsS', '--retry', '3', 'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh', '-o', installer)
            if code: return code
            code = run('/bin/bash', installer)
            if code: return code
            brew = next((str(Path(p) / 'bin/brew') for p in ('/opt/homebrew', '/usr/local') if (Path(p) / 'bin/brew').is_file()), None)
            if not brew: raise RuntimeError('Homebrew installation did not produce a usable executable')
        os.environ['PATH'] = str(Path(brew).parent) + os.pathsep + os.environ.get('PATH', '')
        manifest = repo / 'platforms/macos/Brewfile'
        if action == 'check':
            os.environ['HOMEBREW_NO_AUTO_UPDATE'] = '1'
            return run(brew, 'bundle', 'check', '--no-upgrade', '--file=' + str(manifest))
        from .brew import install
        return install(brew, manifest, action)
    names = packages(repo, platform)
    missing = missing_packages(names, platform)
    if action == 'check':
        if platform in ('ubuntu', 'debian') and Path('/usr/bin/gcc').is_file():
            major = sp.check_output(['/usr/bin/gcc', '-dumpversion'], text=True).strip().split('.')[0]
            missing.extend(missing_packages(['libgccjit-' + major + '-dev'], platform))
        print('Native dependencies: ' + ('OK' if not missing else 'missing ' + ', '.join(missing)))
        return int(bool(missing))
    if action == 'update': missing = names
    if missing:
        if platform == 'fedora':
            code = run('sudo', 'dnf', 'install', '-y', *missing)
        else:
            code = run('sudo', 'apt-get', 'update')
            if not code: code = run('sudo', 'apt-get', 'install', '-y', *missing)
        if code: return code
    if platform in ('ubuntu', 'debian'):
        major = sp.check_output(['/usr/bin/gcc', '-dumpversion'], text=True).strip().split('.')[0]
        jit = 'libgccjit-' + major + '-dev'
        if missing_packages([jit], platform):
            return run('sudo', 'apt-get', 'install', '-y', jit)
    return 0


def service_action(action, root, platform):
    if platform == 'macos':
        label = 'gui/' + str(os.getuid()) + '/org.gnu.emacs.daemon'
        if action == 'status': return run('launchctl', 'print', label)
        if action == 'logs': return run('log', 'show', '--last', '15m', '--predicate', 'process CONTAINS "Emacs"')
        if action == 'stop': return run('launchctl', 'bootout', label)
        if action == 'restart': return run('launchctl', 'kickstart', '-k', label)
        if action == 'start': return backend('deploy.py', 'service')
    return run('journalctl', '--user', '-u', 'emacs.service', '-n', '80', '--no-pager') if action == 'logs' else run('systemctl', '--user', action, 'emacs.service')


def emacs_action(action, args, repo, root, platform):
    target = args[0] if args else ('service' if action in ('apply', 'start', 'stop', 'restart', 'status', 'logs') else 'runtime')
    if len(args) > 1 or target not in ('runtime', 'packages', 'service'):
        raise RuntimeError('Emacs targets: runtime packages service')
    if action == 'list':
        print('runtime: native Emacs\npackages: Lisp packages and native helpers\nservice: launchd/systemd user daemon'); return 0
    if action in ('start', 'stop', 'restart', 'status', 'logs'):
        if target != 'service': raise RuntimeError(action + ' only applies to service')
        if action == 'start':
            return backend('deploy.py', 'service')
        return service_action(action, root, platform)
    if action == 'apply':
        if target == 'service': return backend('deploy.py', 'service', '--no-start')
        return backend('deploy.py', 'link', '--only', 'emacs')
    marker = root / 'data/workbench/emacs-packages.json'
    # Record the exact dependency declarations; a changed declaration reruns the installer.
    sources = [repo / 'config/emacs/lisp/init-package.el', repo / 'libexec/install-emacs-packages.sh']
    fingerprint = hashlib.sha256(b''.join(path.read_bytes() for path in sources)).hexdigest()
    def packages_ready():
        if not marker.is_file(): return False
        record = json.loads(marker.read_text())
        directories = record.get('directories', [])
        return record.get('fingerprint') == fingerprint and bool(directories) and all((Path.home() / '.emacs.d/elpa' / name).is_dir() for name in directories)
    if action == 'check':
        ok = native_installed() if target == 'runtime' else packages_ready()
        if target == 'service': return service_action('status', root, platform)
        print('Emacs ' + target + ': ' + ('OK' if ok else 'MISSING or needs installation'))
        return int(not ok)
    if target == 'packages':
        if action == 'install' and packages_ready():
            print('Emacs packages match the installed configuration'); return 0
        if not (Path.home() / '.emacs.d/init.el').is_file():
            raise RuntimeError('Deploy configuration first: workbench profile apply --config-only')
        code = run('bash', repo / 'libexec/install-emacs-packages.sh')
        if code: return code
        marker.parent.mkdir(parents=True, exist_ok=True)
        directories = sorted(path.name for path in (Path.home() / '.emacs.d/elpa').iterdir() if path.is_dir() and not path.name.startswith('.'))
        marker.write_text(json.dumps(dict(fingerprint=fingerprint, directories=directories)) + '\n')
        return 0
    if target == 'service': return backend('deploy.py', 'service')
    if action == 'install' and native_installed():
        if platform == 'steamdeck' and not (root / 'apps/modules/current/init').is_dir():
            return backend('build-modules.py')
        print('Native Emacs already installed'); return 0
    if platform == 'macos':
        from .brew import ensure_cask
        return ensure_cask('emacs-app', action, available=native_installed())
    code = backend('build-emacs.py', '--deps', 'pixi' if platform == 'steamdeck' else 'system')
    if not code and platform == 'steamdeck': code = backend('build-modules.py')
    return code
