#!/bin/bash
# Root-owned core; no persistent sudo rule or privileged Python interpreter.
set -euo pipefail
root=$1
staged=$2
tun=${3:-1}
[[ $tun == 0 || $tun == 1 ]]
[[ $EUID == 0 && $(uname -s) == Darwin ]]
label=io.workbench.mihomo
old=io.github.clash-verge-rev.clash-verge-rev.service
destination='/Library/Application Support/Workbench/mihomo'
plist=/Library/LaunchDaemons/$label.plist
core=$root/apps/mihomo/current/mihomo
[[ -x $core && -f $staged && -f $root/config/mihomo/config.yaml ]]
/usr/bin/plutil -lint "$staged" >/dev/null
# Validate before interrupting the current proxy.
SAFE_PATHS="$root/apps" "$core" -t -d "$root/config/mihomo" -f "$root/config/mihomo/config.yaml" > "$root/config/mihomo/root-validation.log" 2>&1
# Preserve an existing Workbench daemon until the replacement passes readiness.
saved=$(/usr/bin/mktemp -d /var/tmp/workbench-mihomo.XXXXXX)
trap 'rm -rf "$saved"' EXIT
previous_loaded=0
/bin/launchctl print "system/$label" >/dev/null 2>&1 && previous_loaded=1
[[ ! -f $destination/mihomo ]] || cp -p "$destination/mihomo" "$saved/mihomo"
[[ ! -f $plist ]] || cp -p "$plist" "$saved/service.plist"
restore_previous() {
 /bin/launchctl bootout "system/$label" >/dev/null 2>&1 || true
 if [[ -f $saved/mihomo && -f $saved/service.plist ]]; then
  cp -p "$saved/mihomo" "$destination/mihomo"
  cp -p "$saved/service.plist" "$plist"
  [[ $previous_loaded == 0 ]] || /bin/launchctl bootstrap system "$plist"
 fi
 [[ $old_loaded == 0 ]] || /bin/launchctl bootstrap system "/Library/LaunchDaemons/$old.plist"
}
mkdir -p "$destination"
chmod 755 "$destination"
/usr/bin/install -o root -g wheel -m 755 "$core" "$destination/mihomo.new"
mv "$destination/mihomo.new" "$destination/mihomo"
/usr/bin/install -o root -g wheel -m 644 "$staged" "$plist"
deck_uid=$(/usr/bin/stat -f %u "$root")
old_loaded=0
/bin/launchctl print "system/$old" >/dev/null 2>&1 && old_loaded=1
# Stop only the previous GUI/agent after administrator authorization and validation.
/bin/launchctl bootout "gui/$deck_uid/io.github.clash-verge-rev.clash-verge-rev" >/dev/null 2>&1 || true
/usr/bin/pkill -TERM -u "$deck_uid" -f '^/Applications/Clash Verge.app/Contents/MacOS/clash-verge$' || true
if [[ $old_loaded == 1 ]]; then
 /bin/launchctl bootout "system/$old"
fi
# Wait for the old TUN core to release routes; quit the service's child if orphaned.
for attempt in {1..30}; do
 if ! /usr/bin/pgrep -f '^/Library/Application Support/clash-verge-service/cores/verge-mihomo ' >/dev/null; then break; fi
 if [[ $attempt == 1 ]]; then /usr/bin/pkill -TERM -f '^/Library/Application Support/clash-verge-service/cores/verge-mihomo ' || true; fi
 sleep 1
done
if /usr/bin/pgrep -f '^/Library/Application Support/clash-verge-service/cores/verge-mihomo ' >/dev/null; then
 restore_previous
 echo 'Old core still running; migration aborted.' >&2; exit 1
fi
/bin/launchctl bootout "system/$label" >/dev/null 2>&1 || true
/bin/launchctl enable "system/$label"
if ! /bin/launchctl bootstrap system "$plist"; then
 restore_previous
 exit 1
fi
ready=0
for attempt in {1..30}; do
 code=$(/usr/bin/curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:9090/version || true)
 if [[ $code == 200 ]] && { [[ $tun == 0 ]] || /sbin/ifconfig utun1024 >/dev/null 2>&1; }; then ready=1; break; fi
 sleep 1
done
if [[ $ready != 1 ]]; then
 restore_previous
 echo 'New core/TUN did not become ready; restored previous helper.' >&2; exit 1
fi
/bin/launchctl disable "gui/$deck_uid/io.github.clash-verge-rev.clash-verge-rev"
/bin/launchctl disable "system/$old"
echo 'Installed Mihomo LaunchDaemon; disabled the previous Clash Verge helper.'
