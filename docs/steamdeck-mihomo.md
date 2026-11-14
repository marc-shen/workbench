# Mihomo + MetaCubeXD

此方案直接使用订阅商的完整 Clash/Mihomo YAML，保留节点、策略组与规则。
`components/mihomo/component.toml` 固定官方二进制、网页版本及 SHA256。
规则前置 Tailscale、局域网和 302 直连；DNS 保留供应商设置并支持本机 hosts
及 `.ts.net` MagicDNS。TUN 排除 Tailscale 接口和对应网段。
302 保留安装，其网络服务及自启动由用户在官方 GUI 中管理。Steam 使用原生 HTTPS，平台服务和游戏回退组分开；
[完整网络路径与排查](steamdeck-network.md)。系统 CA 信任不会自动传入 Steam 容器。

## 安装与启用

在本机 `~/Software/config/mihomo/subscription.url` 放置订阅地址，权限设为 600。
该文件、下载的 `provider.yaml` 和生成的 `config.yaml` 都在仓库外，不提交 Git。

```sh
cd ~/Software/src/workbench
workbench mihomo install
# 在 Deck 本机 Konsole 执行一次，输入 sudo 密码：
workbench mihomo apply
```

`workbench mihomo install` 下载客户端和网页、合并本机覆盖、运行核心配置检查、写入用户服务，
不会重启正在运行的代理服务。缺少 PyYAML 时用 uv 创建独立的 Python 辅助环境。
`workbench mihomo apply` 设置核心 capabilities、只针对 `mihomo-tun` 的 resolved DNS 授权，
以及只允许 loopback/Tailscale 接口访问 TCP 9090 的独立 nftables 规则。
同时修复系统 DNS 基线，不修改 302 的服务或设置。
完成后启动 Mihomo、检查控制接口与相应网络状态，再启用开机自启。

启动后访问 `http://本机TailscaleIPv4:9090/ui/`，无需密码/API 密钥。
推荐用 `workbench mihomo open` 或桌面的 Mihomo 入口打开，入口会自动带入正确的后端地址。
Deck 本机也可访问 `http://127.0.0.1:9090/ui/`。网页默认连接同源后端。
其他设备用 `http://本机TailscaleIPv4:9090` 作为后端地址，密钥留空；
后端地址不包含 `/ui/`，其他设备的 `127.0.0.1` 不代表 Deck。
端口迁移后需在 Deck 本机执行一次 `workbench mihomo apply` 更新 root 管理的
防火墙规则并重启服务；新服务会拒绝在旧端口防火墙配置下启动。
HTTP 位于 Tailscale 加密网络内；所有能通过 tailnet ACL 访问该端口的设备
都能管理代理，也可能读取节点信息。Wi-Fi 来源被专用防火墙拒绝。
Mihomo 用户服务启动前检查该防火墙服务已激活；不要手动清空这张 nft 表。

MetaCubeXD 用于选择策略组/节点、查看规则与连接，不是订阅 YAML 的完整编辑器。
高级规则由订阅商 YAML 加本机覆盖管理。

```sh
# 下载订阅、校验并应用变化；没有变化时不重启：
workbench mihomo update
workbench system check steamdeck
```

## 每日订阅更新

`workbench mihomo install` 部署并启用用户级 `mihomo-subscription-update.timer`，每天按 Deck
本地时区在 04:00–04:10 执行。设备关机或休眠错过时，在恢复运行后补执行，
不会主动唤醒设备。无需 sudo，SteamOS 仍保持只读。

定时任务仅刷新订阅与覆盖配置，不升级核心或网页。下载或校验失败时保留旧配置；
配置没有变化时不重启。配置变化且服务正在运行时重启并检查控制接口与 TUN，
失败则恢复旧配置并尝试恢复服务；主动停止的服务不会被定时任务启动。
物理网络尚未确认可用时跳过本次更新，保留订阅缓存。

```sh
# 下次执行时间
workbench mihomo status
# 手动执行一次相同的自动更新流程
workbench mihomo update
# 查看后台和订阅更新日志（更新任务不输出订阅链接）
workbench mihomo logs
```

Mihomo 是默认且唯一代理后端。新机器需先执行 `workbench mihomo install` / `workbench mihomo apply` 完成配置。
更新更换 Mihomo 二进制后重新执行 `workbench mihomo apply`，恢复文件 capabilities。
SteamOS 保持只读；防火墙及专用授权文件由 `/etc/atomic-update.conf.d` 保留。
Tailscale 的登录和 exit node 偏好保留；302 的开关、hosts/DNS 及自启动偏好由官方 GUI 管理。

节点切换通过核心 API 完成。专用 polkit 规则只授权指定用户对 `mihomo-tun`
的 DNS 操作，没有授予任意 sudo 权限。

## 无网络时的行为

`mihomo.service` 由 `libexec/mihomo_network.py` 管理核心。启动先加载 `standby.json`：
`direct` 模式、TUN 关闭、自动测速组临时变为 `select`，provider 自动更新和健康检查暂停。
保持组名和可选成员；正式配置仍在 `config.yaml`，网络恢复后通过核心 API 重载。
这样离线启动也不会先触发一轮全节点检测，且不重启核心或控制台。
网页手动测速仍会主动发起网络请求。

NetworkManager / logind 事件加 30 秒复核驱动状态转换。物理网络断开约 10 秒后进入待机；
认证门户、新 Wi-Fi 未确认可用或休眠恢复时先释放 TUN，允许系统 DNS 和认证页面正常工作。
网络确认可用、稳定约 3 秒后恢复原运行模式和 TUN 偏好。具体状态与检测退避见
[网络状态与恢复](steamdeck-network.md#网络状态与恢复)。

在线时网页修改模式/TUN 会保存到私有 `network-intent.json`，断网不会永久覆盖成直连。
待机期间变更过的选项也会记为联网后的意图，但自动保护仍保持 TUN 关闭；界面当前值与待恢复意图
不一定相同。策略组选择由核心缓存保存。断网时清除旧代理连接，应用联网后需重新建立连接。

```sh
workbench mihomo status   # online / offline / portal / limited / checking
workbench mihomo open    # 本机 127.0.0.1:9090/ui/
```

没有新增 root 常驻进程，不停掉 Tailscale，不主动开关 Wi-Fi，也不唤醒休眠设备。
健康状态正常时只复用核心已有结果；额外恢复探测仅在确认节点组全体失效后启用。
`workbench mihomo stop` 则停止整个核心与管理进程，之后需要显式 `start`。

## Steam 平台独立回退

当前订阅的 `🎮 游戏平台` 保持原有范围，Steam 域名另由 `🎮 Steam平台` 控制，默认选择
`🎮 Steam自动回退`。它从香港源组取最多 4 个节点，避免 Steam 平台服务牵动整个游戏组。
本机可通过 `~/Software/config/mihomo/steam-platform.toml` 指定范围和优先节点：

```toml
schema = 1
source = "🇭🇰 香港节点"
max_nodes = 4
# preferred = ["范围内节点甲", "范围内节点乙"]
```

`max_nodes` 可为 2–8；优先节点若被订阅移除，忽略并从同一源组补齐。
当前集成识别供应商的 `🎮 游戏平台` 分类；源组必须是显式节点列表。
订阅不含该分类时不自动添加 Steam 覆盖。有该分类但无香港源组时，需要在上述文件指定源组。
此文件及节点名字仅保存在本机，订阅更新会重新合并，不写 Git。

Steam 回退组每 180 秒按需检查 Steam API，超时 5 秒，要求 HTTP 200。
国内 `steam@cn` 和网络连通性检测优先直连；其余 Steam 规则在供应商规则之前加入。

## 限定范围的自动回退

本机可使用 `~/Software/config/mihomo/fallbacks.toml` 添加独立的回退组。
文件不提交 Git；订阅更新时按供应商组的最新成员重新生成，不复制订阅或节点凭据。
例如当前 Deck 的游戏平台只在供应商的“香港节点”范围内自动回退：

```toml
schema = 1

[[fallback]]
name = "🎮 香港自动回退"
source = "🇭🇰 香港节点"
attach-to = "🎮 游戏平台"
# 可选：填入范围内的具体节点名，作为第一优先级；只保存在本机。
# preferred = "具体节点名"
url = "https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/"
interval = 180
timeout = 8000
```

组被代理流量使用时，每 180 秒对组内节点执行 Steam API 的 HTTPS HEAD 检查，8 秒超时，要求 HTTP 200。
按列表顺序选择首个健康节点，优先节点恢复后，新连接可以重新使用它。已有 TCP/UDP
连接不能无缝迁移，断线时应用仍可能需要重连；这个检查也不保证具体游戏 UDP 链路可用。
所有节点都失败时，不扩大到其他地区或自动直连；下面的恢复器会暂停受影响的选择组。

`preferred` 已不在最新供应商范围内时会被忽略；新节点按供应商顺序加入。
目前支持源组的显式节点列表，不接受嵌套策略组、DIRECT 或动态 provider/filter
作为范围。源组消失、范围为空或配置无效时停止更新并保留旧配置，不扩大范围。

```sh
# 合并本机配置并校验；不重启正在运行的服务
workbench mihomo install
# 应用刚生成的配置
workbench mihomo restart
workbench mihomo open
```

随后在“游戏平台”选择“香港自动回退”。首次设置需选择一次；后续日常订阅更新
由 `store-selected` 保存选择。仅向游戏平台增加选项，不改变供应商香港组、
全局“节点选择”或其他应用的选择；在网页上改回原香港组即可恢复手动选择。
不要直接编辑生成的 `config.yaml`，下一次订阅更新会覆盖它。

机制参考：[Mihomo fallback](https://wiki.metacubex.one/config/proxy-groups/fallback/)、
[健康检查参数](https://wiki.metacubex.one/config/proxy-groups/)。


## 节点全部失效时

普通回退由 **Mihomo 核心的 fallback** 完成，MetaCubeXD 不负责切换。
Workbench 的 `mihomo_health.py` 只补充全体失效后的暂停与恢复：

1. 每 30 秒读取核心的已有健康记录。只有范围内每个节点都有新鲜、匹配检测 URL 的失败记录，
   并在至少 30 秒后的下一次复核仍失败，才进入确认阶段；未知或过期记录不算失败。
2. 再用组内最多两个节点检查另一 HTTPS 站点，避免只因 Steam 检测站故障而暂停整个组。
3. 确认失败后，将引用该 fallback 的选择组临时置为 `REJECT`，清理该组的旧连接。
   国内直连、Tailscale 和其他组继续运行。原 fallback 使用 lazy 检测，闲置后停止周期探测；
   已开始的检测可能完成，并非逐包立即取消所有后台工作。
4. 依次等待 60、120、300 秒，之后最多每 300 秒检查一次，每轮只检查原范围内最多三个节点。
   同一节点连续两次成功、间隔至少 60 秒，才恢复原 fallback；不扩大地区范围。
5. 手工改选其他策略时取消这次自动恢复。切换物理网络或断网会撤销临时 `REJECT`、暂停恢复探测，
   等新网络可用后由核心重新检查。

`group-recovery.json` 以 600 权限记录临时暂停和恢复意图，服务意外退出后可找回原选择，避免永久遗留临时 `REJECT`。
`workbench mihomo status` 显示正在等待恢复的组。该逻辑只作用于带 `REJECT` 选项且当前选择了
显式 fallback 的选择组；直接手选单节点、动态 provider 或供应商其他类型的组不在此恢复器范围内。
探测基于 HTTPS，不保证每个应用目标、每种 UDP 游戏都可用；已有会话仍可能需要重新连接。
