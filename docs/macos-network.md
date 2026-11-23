# macOS 网络客户端

Mac 使用与 Steam Deck 相同固定版本的 Mihomo 核心和 MetaCubeXD 网页。macOS 使用
LaunchDaemon 管理 TUN 后台；Steam Deck 的 systemd、NetworkManager 和休眠监督器不会复制到 Mac。
Tailscale 使用官方 standalone PKG，保留其原生应用和 Network Extension。

## 部署与入口

```sh
workbench mihomo install
workbench mihomo apply
workbench mihomo open
workbench tailscale install
workbench tailscale check
```

完整新机部署使用 `./install.sh macos` 或 `workbench system install macos`；Mac 上省略 profile
也默认选择 macos。`workbench profile apply --only network` 准备两个客户端，系统授权另用 apply。
首次 TUN 安装通过 macOS 管理员弹窗授权，之后网页切换节点、规则模式和 TUN 无需再次授权。
Finder 的 `~/Applications/Mihomo.app` 打开同一网页。

首次安装会在本机导入 Clash Verge Rev 当前订阅、前置自定义规则、自定义策略组和已保存的选择。
不支持自动迁移删除/重排订阅规则的修改，遇到这种配置会明确停止。原应用和配置保留；apply
成功后停用其登录项和系统 helper，避免两个 TUN 接管网络。不会卸载 Clash Verge。
新机没有现有配置时，在 `~/Software/config/mihomo/subscription.url` 写入订阅，
权限设为 600，再执行 install/apply。文件每行一个 URL，忽略空行、整行 `#` 注释和重复 URL：

```text
# 第一个订阅提供主规则、策略组和节点
https://example.com/main-clash-subscription
# 后续完整订阅同时导入节点、策略组和规则
https://example.net/extra-clash-subscription
```

第一个必须是包含 `proxies`、`proxy-groups` 和 `rules` 的完整 Clash YAML；后续订阅可以是
完整 Clash YAML 或仅含 `proxies` 的节点 YAML。暂不转换 Base64/分享链接列表。
保存后运行 `workbench mihomo update`，会一次性拉取所有源、验证并热加载。任一源失败时，
整套旧配置保留。不要直接编辑生成的 `config.yaml`。

每个源生成一个 Mihomo **本地文件代理提供者**，后台统一管理远程更新。原主订阅的策略组
继续保持其节点范围和默认顺序；可在主选择组选择 `📚 全部订阅`，再选 `📦 订阅 1 / 2 / …`。
额外订阅的节点带源标识前缀，避免同名冲突；自动回退组不会扩大到额外订阅。
删除一行后更新即可移除该源。规则优先级为：内置 Tailscale/局域网等保护 → 本机
`overrides.yaml` 的 `prepend-rules` → 按 URL 顺序排列的订阅规则 → 第一订阅的 MATCH。
同一目标命中多个来源时，前面的规则优先；后续订阅的 MATCH 不导入。
额外来源的策略组也加源前缀，不会误用主订阅的同名组。

规则按原顺序的连续同策略段生成 inline 规则提供者，网页可查看；一个订阅可对应多个
规则提供者。`workbench-global-override-*` 是全局覆盖，`workbench-subscription-*`
是订阅规则。修改本机覆盖后运行 `workbench mihomo update`，不要编辑生成的 config.yaml。
额外来源的原生 inline/HTTP 规则提供者会保留并隔离命名；HTTP 提供者仍由核心独立更新。
额外来源若引用本地规则文件、动态代理提供者或 sub-rules，会明确拒绝自动合并，保留旧配置。

网页可查看、测速及重新载入本地代理提供者；这些提供者的“更新”按钮不会下载远程订阅。
生成的 inline 规则提供者随主配置更新，没有独立远程更新任务。
远程更新使用 `workbench mihomo update` 或每日任务，保证节点、组和规则使用同一批快照。
快照在 `config/mihomo/subscriptions/`，本机源缓存为 `subscriptions.json`；均为私密数据，
不进入 Git。Mac 保留当前和前一批提供者文件，便于配置加载失败时恢复。

| 操作 | Mihomo | Tailscale |
| --- | --- | --- |
| `install` | 安装核心、网页，准备配置和每日更新任务 | 复用现有官方 standalone；缺失时下载签名 PKG |
| `apply` | 安装并启动系统后台，需要管理员授权 | 打开官方应用，完成系统扩展授权/登录 |
| `update` | 更新订阅，验证后通过 API 热加载，失败保留/恢复旧配置 | 获取官方稳定版 PKG，验证签名及 Gatekeeper 后安装 |
| `start / stop / restart` | 控制 LaunchDaemon，需要管理员授权 | 连接、断开或重连 Tailscale，保留登录身份 |
| `status / check` | 查看核心、TUN、网页及定时任务 | 查看官方安装和 Tailscale 健康状态 |
| `open / logs` | 打开网页 / 读取核心日志 | 打开官方应用 / 读取系统日志 |

`update` 当前仅更新 Mihomo 订阅；核心和网页版本由组件 TOML 固定，升级组件版本后重新
install/apply 部署。Tailscale 已安装时 install 不重复安装、不变更现有连接偏好；显式 update
才更新。Tailscale 的 stopped 状态允许用户主动断开，check 不会擅自重新连接。

## 路由与访问

- 保留订阅分流；本机 `overrides.yaml` 保存从 Verge 导入的自定义规则、组和 DNS 设置。
- Tailscale 进程/域名直连，`100.64.0.0/10` 与 `fd7a:115c:a1e0::/48` 排除在 TUN 路由外。
- 局域网、教育网镜像直连；MagicDNS 的 `*.ts.net` 交给 `100.100.100.100`。
- TUN 使用 macOS `utun1024` 和 native DNS，不安装 Linux nftables 或 systemd-resolved 配置。
- 控制器仅监听 `127.0.0.1:9090`，混合代理端口为 `127.0.0.1:2080`。
- Mac 控制器免密码，且只允许本机回环地址访问；`workbench mihomo open` 直接打开网页。
  不对局域网或 Tailscale 网卡开放控制端口。

Mac 没有移植 Steam Deck 的离线/认证门户状态机。遇到认证 Wi-Fi，可在网页关闭 TUN 完成认证后
重新打开；关闭 TUN 不会停止网页和核心。订阅更新不会覆盖当前网页选择的 mode/TUN 状态。

## 存放位置

| 内容 | 路径 |
| --- | --- |
| 核心下载及网页 | `~/Software/apps/mihomo`、`~/Software/apps/metacubexd` |
| 实际系统后台核心 | `/Library/Application Support/Workbench/mihomo/mihomo`，root 拥有 |
| 系统后台定义 | `/Library/LaunchDaemons/io.workbench.mihomo.plist` |
| 订阅、分流及运行缓存 | `~/Software/config/mihomo/`，目录 700，私密文件 600 |
| 核心日志 | `~/Software/logs/mihomo.log` |
| 每日订阅更新 | `~/Library/LaunchAgents/io.workbench.mihomo.subscription.plist`，04:10 |
| Tailscale | `/Applications/Tailscale.app`，官方 PKG 和应用管理系统扩展 |

每日更新需要用户已登录；Mac 休眠期间不会主动唤醒。launchd 的日历任务在休眠结束后补跑，
断网时更新失败保留旧配置。系统后台可以在用户登录前运行，控制网页无需常驻 GUI。

Tailscale 官方说明：[macOS 安装](https://tailscale.com/docs/install/mac)、
[standalone 与其他版本区别](https://tailscale.com/docs/concepts/macos-variants)。

DNS 启动解析使用多个独立公共解析器；节点域名解析同时保留 UDP 备用，避免 DoH 不可达时
所有代理节点失效。验收需覆盖 AI 策略组的实际 HTTPS 请求，不能只检查 Google/Apple。

组件清单固定 Mihomo 1.19.34、MetaCubeXD 1.276.0，使用官方发布包及 SHA256。
2026-11-23 排障时曾用签名校验通过的 Clash Verge 内置 1.19.33 恢复网络，旧版本保留
供回退。升级需使用当前 DNS、节点选择及 TUN 实测 AI 流量。全局 IPv6 关闭时 DNS IPv6 同步关闭。
迁移到 TUN 后会清除匹配旧 Verge 回环端口的系统代理，避免请求继续发送到已停止的服务。
