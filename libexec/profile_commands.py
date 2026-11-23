#!/usr/bin/env python3
"""Portable installation entry point; package sources are explicit and recorded."""
import argparse, os, platform, shutil, subprocess as sp, sys
from pathlib import Path
repo=Path(__file__).resolve().parents[1]
simple=len(sys.argv)>1 and sys.argv[1] in ['apply','update','install']
p=argparse.ArgumentParser(prog='workbench profile',description=__doc__)
if simple:
 p.add_argument('action',choices=[sys.argv[1]])
else:
 p.add_argument('action',choices=['list','plan','install','link','check','service','apply','update'],nargs='?',default='plan')
if simple:
 p.description='Update pulls Git then applies; apply deploys the current checkout. Both install missing dependencies and verify language servers by default.'
p.add_argument('--scope',choices=['user','system'],default='user',help=argparse.SUPPRESS if simple else 'Installation scope')
p.add_argument('--prefix',type=Path,help=argparse.SUPPRESS if simple else 'Software installation directory')
p.add_argument('--dry-run',action='store_true',help=argparse.SUPPRESS)
p.add_argument('--only',choices=['all','shell','emacs','helix','network'],default='all',help='Deploy only this configuration group (network: macOS / Steam Deck; default: all)')
p.add_argument('--reinstall',action='store_true',help=argparse.SUPPRESS if simple else 'Reinstall Pixi tools during install/update')
p.add_argument('--plan',action='store_true',help='Preview without network or writes')
p.add_argument('--config-only',dest='install_missing',action='store_false',help='Deploy configuration only; skip dependency installation and LSP probes')
p.set_defaults(install_missing=True)
a=p.parse_args();root=(a.prefix or Path('/opt/Software' if a.scope=='system' else os.environ.get('SOFTWARE_HOME',str(Path.home()/'Software')))).expanduser().absolute()
os.environ['SOFTWARE_HOME']=str(root)
if a.action=='list':
 for name, description in [('shell','Shell, terminal and CLI configuration'),('emacs','Emacs configuration and service'),('helix','Helix configuration'),('network','Mihomo and Tailscale (macOS / Steam Deck)')]:
  print(name + ': ' + description)
 sys.exit(0)
def run(args):sp.run(list(map(str,args)),check=True)
if a.action in ['apply','update','install']:
 if a.scope!='user':sys.exit('apply/update currently support per-user deployments only; shared /opt provisioning remains separate.')
 if os.geteuid()==0:sys.exit('Run personal deployment as the target user, not root.')
 command=[sys.executable,'-B',str(repo/'libexec/profile-sync.py'),'sync' if a.action=='update' else 'apply','--prefix',str(root),'--only',a.only]
 if a.plan or a.dry_run:command+=['--plan']
 if not a.install_missing:command+=['--config-only']
 os.execv(sys.executable,command)
if a.plan:a.dry_run=True
if a.only=='network' and a.action not in ['apply','update']:
 sys.exit('Use workbench profile apply --only network; networking supports macOS and Steam Deck.')
if a.action=='plan' or a.dry_run:
 print('Platform:',platform.system(),platform.machine());print('Scope:',a.scope,'Prefix:',root)
 print('System Git; upstream Pixi/uv; isolated Pixi CLI tools; upstream Git Zsh plugins.')
 print('Emacs: Homebrew on macOS; source on Linux. Configuration/data remain per user.')
 print('Explicit updates; retain installation receipts and locks. No automatic package-manager fallback on errors.')
 sys.exit(0)
if a.scope=='system' and os.geteuid()!=0:sys.exit('System provisioning requires an administrator. Run this command with sudo.')
if a.scope=='user' and os.geteuid()==0:sys.exit('Do not deploy personal dotfiles as root; use --scope system or the target user.')
if a.scope=='system' and Path('/etc/os-release').exists() and any(line.strip().strip('"\'')=='steamos' for line in [entry.split('=',1)[1] for entry in Path('/etc/os-release').read_text().splitlines() if entry.startswith('ID=')]):
 sys.exit('SteamOS stays read-only: use a user installation under ~/Software.')
action=a.action
run([sys.executable,repo/'libexec/deploy.py',action,'--scope',a.scope,'--prefix',root,'--only',a.only])
