"""Exercise real Zsh completion widgets without executing completed commands."""
import importlib.util
import os
import sys
from toml_helpers import dumps
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
ZSH = shutil.which('zsh')
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
SPEC = importlib.util.spec_from_file_location('network', REPO / 'libexec/deck_network.py')
network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network)


@unittest.skipUnless(ZSH, 'Zsh is required')
class CompletionTests(unittest.TestCase):
    def complete(self, command):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / 'Software'
            binary = root / 'apps/demo/v1/bin/demo-tool'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            plugin = root / 'extensions/extra'
            plugin.mkdir(parents=True)
            import json
            (plugin / 'component.toml').write_text(dumps(dict(schema=1,category='cli',name='extra',description='Test extension',platforms=['macos','steamdeck','fedora','ubuntu'],entrypoint=['/bin/false'],actions={name:{'description':name} for name in ['list','install','update','check']})))
            output = home / 'matches'
            (home / '.zshrc').write_text('''
fpath=("$WORKBENCH_HOME/config/shell/completions" $fpath)
autoload -Uz compinit
compinit -D
zstyle ':completion:*:descriptions' format '[%d]'
zstyle ':completion:*:*:workbench:*' group-name ''
PROMPT='READY> '
LISTMAX=0
_capture_complete() {
  compadd() {
    if (( ! ${argv[(Ie)-O]} && ! ${argv[(Ie)-A]} )); then
      local -a captured
      builtin compadd -A captured "$@"
      (( ! $#captured )) || print -rl -- "${captured[@]}" >> "$COMPLETION_CAPTURE"
      local group_index=${argv[(Ie)-J]}
      if (( $#captured && group_index )); then
        print -r -- "__GROUP__${argv[group_index+1]}" >> "$COMPLETION_CAPTURE"
      fi
    fi
    builtin compadd "$@"
  }
  _main_complete
  unfunction compadd
  print -r -- __DONE__ >> "$COMPLETION_CAPTURE"
}
zle -C capture-complete complete-word _capture_complete
bindkey '^I' capture-complete
''')
            master, slave = pty.openpty()
            env = dict(os.environ, HOME=tmp, ZDOTDIR=tmp, WORKBENCH_HOME=str(REPO),
                       SOFTWARE_HOME=str(root), PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',''), COMPLETION_CAPTURE=str(output), TERM='xterm-256color')
            proc = subprocess.Popen([ZSH, '-d', '-i'], env=env, cwd=REPO,
                                    stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            transcript = b''
            try:
                deadline = time.monotonic() + 8
                while b'READY> ' not in transcript and time.monotonic() < deadline:
                    if select.select([master], [], [], .05)[0]:
                        transcript += os.read(master, 65536)
                self.assertIn(b'READY> ', transcript, transcript.decode(errors='replace'))
                os.write(master, command.encode() + b'\t')
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .05)[0]:
                        transcript += os.read(master, 65536)
                    if output.exists() and '__DONE__' in output.read_text():
                        lines=output.read_text().splitlines()
                        self.completion_groups=[line.removeprefix('__GROUP__') for line in lines if line.startswith('__GROUP__')]
                        self.completion_transcript=transcript.decode(errors='replace')
                        return {line for line in lines if not line.startswith('__')}
                self.fail('Completion timed out: ' + transcript.decode(errors='replace'))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                os.close(master)

    def test_category_and_tag_filters(self):
        self.assertTrue({'development', 'terminal', 'network'} <= self.complete('workbench list --category '))
        self.assertIn('runtime', self.complete('workbench list --tag '))
        self.assertIn('development', self.complete('workbench --category '))

    def test_workbench_plugins_actions_and_names(self):
        self.assertTrue({'list', 'check', 'install', 'update'} <= self.complete('workbench fzf-tab '))
        self.assertTrue({'fzf-tab', 'zsh-autosuggestions'} <= self.complete('workbench '))

    def test_workbench_components_have_distinct_category_groups(self):
        names=self.complete('workbench --platform macos ')
        self.assertIn('font',names)
        self.assertIn('extra',names)
        self.assertNotIn('gcc',names)
        groups=list(dict.fromkeys(self.completion_groups))
        for group in ('management','cli','terminal','development','editors','network','latex','fonts'):
            self.assertIn('workbench-'+group,groups)
        self.assertLess(groups.index('workbench-management'),groups.index('workbench-fonts'))

    def test_font_actions_and_action_specific_options(self):
        self.assertTrue({'list','check','outdated','install','update'} <= self.complete('workbench --platform macos font '))
        options=self.complete('workbench --platform macos font check --')
        self.assertIn('--plan',options)
        self.assertFalse({'--probe','--only','--shared','--reinstall'} & options)
        self.assertIn('--shared',self.complete('workbench components create --'))
        self.assertTrue({'--only','--config-only'} <= self.complete('workbench profile apply --'))

    def test_commands_without_targets_do_not_offer_repository_files(self):
        matches=self.complete('workbench --platform macos font check ')
        self.assertFalse({'README.md','components','lib','tests','bootstrap.sh'} & matches)

    def test_lists_at_profile_and_network_entry(self):
        self.assertIn('list', self.complete('workbench profile '))

    def test_manage_profile_scope(self):
        self.assertTrue({'all', 'shell', 'emacs', 'helix', 'network'} <= self.complete('workbench profile apply --only '))

    def test_bootstrap_only_has_bootstrap_options(self):
        matches = self.complete('./bootstrap.sh --')
        self.assertTrue({'--plan', '--offline', '--prefix'} <= matches)
        self.assertNotIn('--only', matches)

    def test_components_actions_and_canonical_names(self):
        names = self.complete('workbench ')
        self.assertIn('components', names)
        self.assertNotIn('modules', names)
        self.assertTrue({'list', 'check', 'create'} <= self.complete('workbench components '))
        self.assertNotIn('upgrade', self.complete('workbench profile '))

    def test_manage_discovers_new_modules(self):
        self.assertTrue({'profile', 'node', 'mihomo', 'extra'} <= self.complete('workbench '))

    def test_manage_discovers_extension_actions(self):
        self.assertTrue({'install', 'update', 'check'} <= self.complete('workbench extra '))

    def test_manage_network_delegates_service_and_action_completion(self):
        self.assertTrue({'update', 'check', 'status'} <= self.complete('workbench mihomo '))

    def test_software_manifest_names(self):
        self.assertTrue({'node', 'zsh', 'ripgrep'} <= self.complete('software-app install '))

    def test_software_versions(self):
        self.assertIn('v1', self.complete('software-app run demo '))

    def test_software_commands(self):
        self.assertIn('demo-tool', self.complete('software-app run demo v1 '))

    def test_build_wrapper_forwards_completion(self):
        self.assertIn('status', self.complete('deck-build git '))


if __name__ == '__main__':
    unittest.main()
