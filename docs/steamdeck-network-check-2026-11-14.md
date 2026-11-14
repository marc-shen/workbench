# Steam Deck 网络迁移验收（2026-11-14）

此记录保留当时的验收结果。后续已纠正直接禁用 302 控制后台的做法：恢复官方安装，
网络服务及自启动由用户在官方 GUI 中控制；Workbench 不再执行停用或改写。
当前行为以 [网络说明](steamdeck-network.md) 为准。

默认网络从“302 hosts 拦截 + Mihomo”改为“原生 HTTPS + Mihomo 分流”。
302 保留安装，停止服务并禁用自启、hosts 自动修改和 DNS 接管。系统 `/etc/hosts` 及
正在运行的 Steam / Steam WebHelper 容器中均没有残留的 `#S302` 条目。

## 实机结果

- `workbench mihomo check`、`workbench tailscale check`、`workbench steamcommunity302 check` 全部通过。
- Mihomo 为 `online`、`rule`，TUN 存在；无异常重启，也没有出现旧启动过程中的“no interface”告警。
- 本机和 Tailscale 地址的 9090 网页可用，远程 HTTP 返回 200。
- Steam 平台选择独立的 4 节点回退池。游戏继续选择香港自动回退，范围随订阅的香港组更新。
- Tailscale 健康告警为空；本机和当前管理电脑的 MagicDNS 短名称、完整域名均解析成功。
  到管理电脑的 Tailscale ping 成功，本次使用 DERP 中继，不是直接 P2P。
- NetworkManager 和 logind 两个事件监听进程均能在普通用户权限下运行。
- 每日订阅更新的实际流程运行成功：下载、合并、双配置校验、重启及健康检查均完成。
  更新后再次核验 Steam、TUN、Tailscale 和策略选择，结果正常。定时器仍为每日 04:00–04:10。
- SteamOS 只读状态为 `enabled`。配置、待机配置和恢复意图文件权限均为 600。

使用正在运行的 Steam WebHelper 容器的真实 CA bundle 请求以下端点，全部 TLS 校验成功：

| 端点 | HTTP 结果 | 实际路由 |
| --- | --- | --- |
| `login.steampowered.com` | 302 | Steam 平台回退 |
| Steam API `GetServerInfo` | 200 | Steam 平台回退 |
| `store.steampowered.com` | 200 | Steam 平台回退 |
| `steamcommunity.com` | 200 | Steam 平台回退 |
| Google Steam 云存储根地址 | 403，TLS 校验通过 | Steam 平台回退 |
| Akamai 客户端更新清单 | 200 | Steam 平台回退 |
| `media.st.dl.eccdnx.com` 国内更新清单 | 200 | DIRECT |
| `steamconnecttest.com`（HTTP） | 200 | DIRECT |

云存储测试只确认 HTTPS 路径和证书正常；未执行实际存档上传/下载。
登录 API 的请求约 1 秒完成；这是端点连通测试，不是一次完整的新登录计时。

## 自动化验证

单元测试覆盖物理/虚拟网卡判别、网关检查、断网防抖、认证门户恢复、网络身份变化、休眠事件、
手动模式/TUN 意图保留、部分重载失败退回待机、未知健康记录不误判失败、范围内恢复及退避。
组件和平台测试通过。

在 Deck 的真实 Mihomo 1.19.34 上另起隔离实例，使用本机 HTTP 测试服务器和独立端口，验证：

- 冷启动处于离线状态时，没有自动健康检查请求。
- 联网、断网、再联网过程中核心 PID 不变，网页持续可用。
- 待机暂停测速，恢复后重新检查，原选择组关系保留。
- 含 emoji 的策略组可正常重载。迁移时发现并修复了 JSON surrogate 转义不被 YAML 解析器接受的问题。

## 验证边界

没有为测试关闭正在使用的 Wi-Fi、强制进入飞行模式、挂起或重启整台设备，也没有故意使真实节点全部失效。
这些状态转换通过模拟和隔离核心测试验证；真实认证 Wi-Fi、具体游戏 UDP、完整新登录和云存档传输
仍需在对应场景中使用验证。两个原生连通性检测站都被阻断时，新网络可能保持 `limited`。

系统更新持久化清单已检查；尚未实际执行一次 SteamOS 升级。升级后可再次运行三个组件的 `check`。
运行方案与命令见 [Steam Deck 网络](steamdeck-network.md) 和 [Mihomo 配置](steamdeck-mihomo.md)。
