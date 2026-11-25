"""Read-only checks for the managed Mihomo TUN and Tailscale exit-node conflict."""
import json
import subprocess as sp
import urllib.error
import urllib.request

CONFLICT = ('Mihomo TUN 已开启；同时使用 Tailscale exit-node 可能使 DNS/网页访问失效。'
            '仅访问远端局域网可使用 wb tailscale set --accept-routes=true --exit-node=false；'
            '确需出口节点时，请先在 Mihomo 控制页面关闭 TUN。')


def tun_enabled(root):
    if not (root / 'config/mihomo/config.yaml').is_file():
        return False
    try:
        # A system/HTTP proxy must not intercept the local control API.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:9090/configs', timeout=3) as response:
            data = json.load(response)
        enabled = data['tun']['enable']
        if not isinstance(enabled, bool):
            raise ValueError('invalid tun.enable')
        return enabled
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        result = sp.run(['/bin/launchctl', 'print', 'system/io.workbench.mihomo'],
                        capture_output=True, text=True, timeout=5)
        if result.returncode:
            return False
        raise RuntimeError('无法确认 Mihomo TUN 状态；请检查 wb mihomo status 和控制页面后再设置出口节点。') from None


def require_exit_compatible(root):
    if tun_enabled(root):
        raise RuntimeError(CONFLICT)


def check(root, command):
    import tailscale_settings as settings
    prefs = settings.preferences(command)
    if prefs.get('WantRunning') is not False and (prefs.get('ExitNodeID') or prefs.get('ExitNodeIP')):
        require_exit_compatible(root)
