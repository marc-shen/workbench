"""Adapters for existing installers; components declare the public contract."""
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import sys
from .platforms import detect
from .ui import Console

REPO = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get('SOFTWARE_HOME', str(Path.home() / 'Software')))


def run(*args):
    return sp.run(list(map(str, args))).returncode


def backend(name, *args):
    return run(sys.executable, REPO / 'libexec' / name, *args)


def native_installed():
    if detect() == 'macos':
        return Path('/Applications/Emacs.app/Contents/MacOS/Emacs').is_file()
    return os.access(ROOT / 'apps/emacs/current/bin/emacs', os.X_OK) or bool(shutil.which('emacs'))


def main(name, argv):
    from .data import read
    from .registry import discover
    action, args = argv[0], argv[1:]
    platform = detect()
    spec = discover(REPO, ROOT)[name]
    if spec.get('kind') == 'command':
        if name == 'mirrors':
            from .mirrors import mirror_action
            return mirror_action(action, args, REPO, ROOT, platform)
        if name == 'providers':
            selected = args or ['uv', 'pixi']
            if set(selected) - {'uv', 'pixi'}: raise RuntimeError('Providers: uv pixi')
            for provider in selected:
                binary = ROOT / 'bin' / provider
                if action in ('list', 'check'):
                    ready = os.access(binary, os.X_OK)
                    Console().status('OK' if ready else 'MISSING', provider, 'ok' if ready else 'error')
                    if not ready and action == 'check': return 1
                elif action == 'update' or not os.access(binary, os.X_OK):
                    code = backend('software.py', 'managers', provider)
                    if code: return code
            return 0
        if name == 'profile':
            if action == 'install': action = 'apply'
            if action == 'plan': return backend('profile_commands.py', 'apply', '--plan', *args)
            return backend('profile_commands.py', action, *args)
        if name == 'platform':
            from .provision import platform_action
            return platform_action(action, args, REPO, ROOT, platform)
        if name == 'components':
            from .scaffold import component_action
            return component_action(action, args, REPO, ROOT)
        if name == 'system':
            Console().table([(p.stem, read(p)['description']) for p in sorted((REPO / 'profiles').glob('*.toml'))])
            return 0
    install = spec.get('install', {})
    kind = install.get('kind')
    if kind == 'crossdesk':
        if args: raise RuntimeError('crossdesk takes no targets')
        return backend('crossdesk.py', action)
    if kind == 'fonts':
        from .fonts import font_action
        return font_action(action, argv[1:], REPO, platform)
    if kind == 'emacs':
        from .provision import emacs_action
        return emacs_action(action, args, REPO, ROOT, platform)
    if kind == 'python-cli':
        if args: raise RuntimeError(name + ' takes no targets')
        result = sp.run([str(ROOT / 'bin/uv'), 'tool', 'list'], capture_output=True, text=True) if (ROOT / 'bin/uv').is_file() else None
        installed = {line.split()[0] for line in result.stdout.splitlines() if line and not line.startswith((' ', '-'))} if result and not result.returncode else set()
        ready = install['package'] in installed
        if action in ('list', 'check'):
            Console().status('OK' if ready else 'MISSING', name, 'ok' if ready else 'error')
            return int(action == 'check' and not ready)
        if action == 'install' and ready: return 0
        return run(ROOT / 'bin/uv', 'tool', 'install', *(['--upgrade'] if action == 'update' else []), install['package'])
    if kind == 'plugin':
        return backend('plugins.py', action, name, *args)
    if kind == 'language-server':
        return backend('language-servers.py', {'list': 'plan', 'update': 'upgrade'}.get(action, action), install['storage_name'], *args)
    if kind == 'network':
        service = install['service']
        if service == 'tailscale' and action in ('set', 'show'):
            sys.path.insert(0, str(REPO / 'libexec'))
            import tailscale_settings
            changes = tailscale_settings.parse(args) if action == 'set' else None
            if action == 'show' and args: raise RuntimeError('tailscale show takes no arguments')
            if platform == 'macos':
                import macos_tailscale_mode as variants
                if action == 'show': tailscale_settings.show(variants.command(ROOT))
                else: variants.configure(ROOT, changes)
                return 0
            else:
                command = ['/opt/tailscale/tailscale']
                def privileged(command):
                    sp.run(['sudo', *map(str, command)], check=True)
            if action == 'show': tailscale_settings.show(command)
            else: tailscale_settings.apply(ROOT, command, privileged, changes)
            return 0
        if service == 'tailscale' and platform == 'macos' and action == 'install':
            return backend('macos_network.py', service, action, *args)
        if args: raise RuntimeError(name + ' takes no targets')
        if action == 'list':
            Console().table([(name, spec['description']), ('operations', ' '.join(spec['actions']))]); return 0
        if platform == 'macos':
            return backend('macos_network.py', service, action)
        if action == 'apply':
            sys.path.insert(0, str(REPO / 'libexec'))
            import deck_network
            if not deck_network.service_pending(ROOT, service):
                Console().status('OK', name + ' already configured', 'ok'); return 0
        return backend('deck_network.py', service, {'install': 'prepare', 'apply': 'activate'}.get(action, action))
    if kind == 'python':
        if args: raise RuntimeError('python takes no targets')
        sys.path.insert(0, str(REPO / 'libexec'))
        from python_runtime import ensure, usable
        binary = ROOT / 'apps/python/current/bin/python3'
        if action in ('list', 'check'):
            ready = usable(binary, exact=True)
            Console().status('OK' if ready else 'MISSING', 'Python 3.12: ' + str(binary), 'ok' if ready else 'error')
            return int(action == 'check' and not ready)
        ensure(ROOT, update=action == 'update'); return 0
    if kind == 'texlive' and platform == 'macos':
        if args: raise RuntimeError('texlive takes no targets')
        from .mactex import mactex_action
        return mactex_action(action, ROOT, install['macos'])
    if kind in ('package', 'wezterm', 'gcc', 'texlive'):
        if any(arg != '--reinstall' for arg in args): raise RuntimeError(name + ' accepts only [--reinstall]')
        commands = install.get('commands', ['wezterm'] if kind == 'wezterm' else ['gfortran'] if kind == 'gcc' else ['xelatex'])
        paths = [ROOT / 'bin' / command for command in commands]
        if kind == 'wezterm' and platform == 'macos': paths = [Path('/Applications/WezTerm.app/Contents/MacOS/wezterm')]
        ready = all(os.access(path, os.X_OK) for path in paths)
        record = ROOT / 'apps' / name / 'current/receipt.json'
        receipt = json.loads(record.read_text()) if record.is_file() else {}
        if kind == 'package' and install['provider'] == 'nvm':
            ready = ready and all(receipt.get(k) == install[k] for k in ('version', 'nvm_version'))
            ready = ready and (ROOT / 'data/nvm/nvm.sh').is_file()
            ready = ready and all(p.resolve() == (ROOT / 'apps/node/current/bin' / p.name).resolve() for p in paths)
        if kind in ('gcc', 'texlive'): ready = ready and record.is_file()
        if action in ('list', 'check'):
            Console().table([(name, ('[OK]' if ready else '[MISSING]') + '  ' + str(receipt.get('version', '-'))),
                             ('path', str(paths[0]))])
            return int(action == 'check' and not ready)
        if action == 'install' and ready and '--reinstall' not in args: return 0
        if kind == 'package': return backend('software.py', 'tools', name, *args)
        if kind == 'wezterm':
            if platform == 'macos':
                from .brew import ensure_cask
                return ensure_cask('wezterm', action, available=ready, reinstall='--reinstall' in args)
            return backend('install-wezterm.py', *args)
        return backend('install-gcc.py' if kind == 'gcc' else 'install-texlive.py', *args)
    raise RuntimeError('No adapter for ' + name)


def entry(name):
    try:
        return main(name, sys.argv[1:])
    except (RuntimeError, OSError, ValueError) as error:
        print(name + ': ' + str(error), file=sys.stderr)
        return 1
