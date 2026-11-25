# macOS 网络客户端

Mac 使用与 Steam Deck 相同固定版本的 Mihomo 核心和 MetaCubeXD 网页。macOS 使用
LaunchDaemon 管理 TUN 后台；Steam Deck 的 systemd、NetworkManager 和休眠监督器不会复制到 Mac。
Tailscale 使用 Homebrew 的 `tailscale` formula，通过 `sudo brew services` 安装 root 级
`tailscaled` LaunchDaemon，开机即运行，不依赖用户登录或 GUI / Network Extension。

## 部署与入口

```sh
workbench mihomo install
workbench mihomo apply
workbench mihomo open
workbench tailscale install
workbench tailscale apply   # 首次登录，在终端显示授权链接
workbench tailscale check
```

完整新机部署使用 `./install.sh macos` 或 `workbench system install macos`；Mac 上省略 profile
也默认选择 macos。`workbench profile apply --only network` 准备两个客户端，系统授权另用 apply。
安装使用终端 sudo 授权；整机安装/更新开始时验证一次，在任务期间每 45 秒续期，结束后停止续期。
密码由 sudo 直接读取，不保存到文件、日志或环境变量，不创建永久免密规则。
Tailscale 登录仍需浏览器授权，Apple Command Line Tools 首次安装仍可能需要系统交互；
新版 tailscaled 不需要 GUI 版的系统扩展审批。
网页切换节点、规则模式和 TUN 无需再次授权。非交互后台任务不弹密码框，缺少授权时明确失败。
Finder 的 `~/Applications/Mihomo.app` 打开同一网页。

首次安装会在本机导入 Clash Verge Rev 当前订阅、前置自定义规则、自定义策略组和已保存的选择。
不支持自动迁移删除/重排订阅规则的修改，遇到这种配置会明确停止。原应用和配置保留；apply
成功后停用其登录项和系统 helper，避免两个 TUN 接管网络。不会卸载 Clash Verge。
新机无需提前提供订阅。install/apply 会安装核心、网页、系统服务和每日更新任务，创建权限 600 的
`~/Software/config/mihomo/subscription.url`。未配置订阅时页面可用，使用 DIRECT 模式，关闭 TUN 和 DNS 接管。
填入订阅后运行 `wb mihomo update`；首次有效订阅通过验证和热加载后启用规则模式及 TUN。
已经配置订阅的设备继续保留网页上主动选择的 mode/TUN 状态。
文件每行一个 URL，忽略空行、整行 `#` 注释和重复 URL。
已有配置的服务故障、订阅格式错误、下载和验证失败仍会报错，不会当作“未提供订阅”忽略。

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
| `install` | 安装核心、网页，准备配置和每日更新任务 | 安装 formula、启动 root LaunchDaemon、设置当前用户为 operator |
| `apply` | 安装并启动系统后台，需要管理员授权 | 确保系统服务运行，首次登录显示授权链接 |
| `update` | 更新订阅，验证后通过 API 热加载，失败保留/恢复旧配置 | 升级 formula；版本变化后重启系统服务 |
| `start / stop / restart` | 控制 LaunchDaemon，需要管理员授权 | 启动、停止或重启系统 LaunchDaemon，保留登录身份 |
| `status / check` | 查看核心、TUN、网页及定时任务 | 检查 formula、系统服务及 Tailscale 健康状态 |
| `open / logs` | 打开网页 / 读取核心日志 | 打开管理网页 / 读取 tailscaled.log |

`update` 当前仅更新 Mihomo 订阅；核心和网页版本由组件 TOML 固定，升级组件版本后重新
install/apply 部署。Tailscale 已安装时 install 不重复安装、不变更现有连接偏好；显式 update
才更新。已加载 daemon 的 `Stopped` 表示用户主动断开，check 不会擅自连接；
`wb tailscale stop` 则停止系统服务，check 会提示未加载。start/restart 只管理服务，不重置网络偏好。

## 路由与访问

- 保留订阅分流；本机 `overrides.yaml` 保存从 Verge 导入的自定义规则、组和 DNS 设置。
- Tailscale 进程/域名直连，`100.64.0.0/10` 与 `fd7a:115c:a1e0::/48` 排除在 TUN 路由外。
- 局域网、教育网镜像直连；MagicDNS 的 `*.ts.net` 交给 `100.100.100.100`。
- TUN 使用 macOS `utun1024` 和 native DNS，不安装 Linux nftables 或 systemd-resolved 配置。
- 控制器仅监听 `127.0.0.1:9090`，混合代理端口为 `127.0.0.1:2080`。
- Mac 通过 Tailscale Serve 将 tailnet 的 TCP 9090 转发到本机控制器。
  其他已获 Tailnet 规则授权的设备访问 `http://<这台 Mac 的 Tailscale IP>:9090/ui/`。
  无需网页密码，不向普通局域网或公网开放；不使用 Funnel、不重置其他 Serve 服务。
- Tailscale 尚未登录时保留本机页面；用户登录后，LaunchAgent 每 60 秒检查并自动补齐转发。
  `wb mihomo apply` 也会立即尝试配置。9090 已被其他 Serve 服务占用时明确报错并保留原设置。
- `wb mihomo check` 会检查转发配置；远端还需 Tailnet grants/ACL 允许访问该设备的 TCP 9090。

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
| Tailscale 页面转发检查 | `~/Library/LaunchAgents/io.workbench.mihomo.tailnet.plist`，登录后每 60 秒 |
| Tailscale 命令 | `<Homebrew 前缀>/opt/tailscale/bin/tailscale`，链接到 `~/Software/bin/tailscale` |
| Tailscale 系统服务 | `/Library/LaunchDaemons/sh.brew.tailscale.plist`；兼容旧版 `homebrew.mxcl.tailscale.plist` |
| Tailscale 日志 | `<Homebrew 前缀>/var/log/tailscaled.log` |

每日更新需要用户已登录；Mac 休眠期间不会主动唤醒。launchd 的日历任务在休眠结束后补跑，
断网时更新失败保留旧配置。系统后台可以在用户登录前运行，控制网页无需常驻 GUI。
首次配置页面转发需要用户登录；设置成功后 Tailscale Serve 使用 `--bg` 保存转发并随 Tailscale 恢复。

## 从 GUI 版迁移到 tailscaled

脚本检测到 `/Applications/Tailscale.app`、用户目录的同名应用或仍运行的 Network Extension 时，
会在启动新服务前停止并提示迁移。按官方建议，在本机退出并卸载旧应用、清空废纸篓、重启 Mac，
然后执行 `wb tailscale install` 和 `wb tailscale apply`。不要通过待迁移的 Tailscale SSH 连接卸载旧版。
GUI 与开源 daemon 的身份存储不同，需重新登录，可能产生新的节点/IP；原设备记录由你在管理后台确认后处理。
脚本不自动删除旧身份数据，不同时运行两套 VPN。

如果此前用普通 `brew services start tailscale` 安装了用户服务，先运行 `brew services stop tailscale`。
如果用过 `tailscaled install-system-daemon`，先在本机运行
`sudo /usr/local/bin/tailscaled uninstall-system-daemon` 卸载旧服务定义（保留状态），再安装 Workbench 管理的系统服务。

安装时使用 Tailscale 自带的 `set --operator=<当前用户>` 授权本机用户操作 daemon，
以便 Mihomo 的后台任务配置 Serve，无需永久免密 sudo 规则。连接偏好、已有 Serve 端口和登录状态保留。
非交互安装会准备服务；需要首次登录时 `apply` 返回待完成状态，不无限等待浏览器授权。

开源 macOS daemon 不自动修改系统 DNS。Mihomo 开启 DNS 接管时已有 `*.ts.net` 转发规则；
未开启接管时，如需 MagicDNS，需要另行配置 DNS。脚本不会直接覆盖当前 Wi-Fi/以太网 DNS。

Tailscale 官方说明：[tailscaled on macOS（含 Homebrew 系统服务安装方法）](https://github.com/tailscale/tailscale/wiki/Tailscaled-on-macOS)、
[各 macOS 版本的区别与迁移要求](https://tailscale.com/docs/concepts/macos-variants)。

DNS 启动解析使用多个独立公共解析器；节点域名解析同时保留 UDP 备用，避免 DoH 不可达时
所有代理节点失效。验收需覆盖 AI 策略组的实际 HTTPS 请求，不能只检查 Google/Apple。

组件清单固定 Mihomo 1.19.34、MetaCubeXD 1.276.0，使用官方发布包及 SHA256。
2026-11-23 排障时曾用签名校验通过的 Clash Verge 内置 1.19.33 恢复网络，旧版本保留
供回退。升级需使用当前 DNS、节点选择及 TUN 实测 AI 流量。全局 IPv6 关闭时 DNS IPv6 同步关闭。
迁移到 TUN 后会清除匹配旧 Verge 回环端口的系统代理，避免请求继续发送到已停止的服务。
