#!/usr/bin/env bash
# Full setup from an existing checkout; bootstrap.sh remains the minimal entry.
set -euo pipefail
repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "${1-}" in -h|--help)
 echo 'Usage: ./install.sh [PROFILE] [--plan]'
 echo 'Profiles: base, workstation, macos, steamdeck, scientific; more can be added under profiles/.'
 echo 'Installs OS prerequisites, bootstraps ~/Software, then runs workbench system install.'
 exit 0;; esac
for arg in "$@"; do
 if [[ $arg == --plan ]]; then
  if [[ ! -f "$repo/bin/workbench" ]]; then
   echo 'PLAN: install native prerequisites, clone the selected repository into Software/src/workbench, bootstrap, then system install.'
   exit 0
  fi
  command -v python3 >/dev/null || { echo 'Plan requires Python 3; native setup installs python3/git/curl first.'; exit 0; }
  runtime=${SOFTWARE_HOME:-$HOME/Software}/bin/workbench
  if [[ -x $runtime ]]; then exec "$runtime" system install "$@"; fi
  if python3 -c 'import sys; sys.exit(sys.version_info < (3,12))' 2>/dev/null; then
   exec python3 "$repo/bin/workbench" system install "$@"
  fi
  echo 'PLAN: bootstrap managed Python 3.12, then run workbench system install (no writes).'
  exit 0
 fi
done
[[ $EUID != 0 ]] || { echo 'Run as your normal user; system packages use sudo.' >&2; exit 1; }
case $(uname -s) in
 Darwin)
  if ! xcode-select -p >/dev/null 2>&1; then
   xcode-select --install
   echo 'Complete Apple Command Line Tools installation, then rerun ./install.sh.' >&2
   exit 2
  fi;;
 Linux)
  source /etc/os-release
  missing=0
  for command in python3 git curl; do command -v "$command" >/dev/null || missing=1; done
  if [[ $missing == 1 ]]; then
   case $ID in
    fedora) sudo dnf install -y python3 git curl ca-certificates;;
    ubuntu|debian) sudo apt-get update; sudo apt-get install -y python3 git curl ca-certificates;;
    steamos) echo 'SteamOS must provide Python/Git/curl; update SteamOS first. Root remains read-only.' >&2; exit 2;;
    *) echo "Unsupported distribution: $ID" >&2; exit 1;;
   esac
  fi;;
 *) echo 'Unsupported operating system' >&2; exit 1;;
esac
if [[ ! -f "$repo/bin/workbench" ]]; then
 checkout=${SOFTWARE_HOME:-$HOME/Software}/src/workbench
 if [[ ! -d "$checkout/.git" ]]; then
  [[ ! -e "$checkout" ]] || { echo "Destination already exists: $checkout" >&2; exit 1; }
  mkdir -p "$(dirname "$checkout")"
  git clone "${WORKBENCH_REPOSITORY_URL:-https://github.com/marc-shen/workbench.git}" "$checkout"
 fi
 repo=$(CDPATH= cd -- "$checkout" && pwd -P)
 [[ -f "$repo/bin/workbench" ]] || { echo 'The checkout does not contain workbench v3. Select the updated revision first.' >&2; exit 1; }
fi
bash "$repo/bootstrap.sh"
exec "${SOFTWARE_HOME:-$HOME/Software}/bin/workbench" system install "$@"
