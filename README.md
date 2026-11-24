# Workbench 3

macOS、Steam Deck、Fedora、Ubuntu 的个人工作站管理工具。
每个软件是一个独立组件，使用 TOML 声明分类、依赖和安装方式；运行记录使用 JSON。
框架要求 **Python 3.12+**，默认使用 uv 管理的 **Python 3.12**，无需 PyPI 框架依赖。

## 从零安装

```sh
git clone https://github.com/marc-shen/workbench.git ~/Software/src/workbench
cd ~/Software/src/workbench
./install.sh
```

`install.sh` 补齐系统引导前提，准备 Python 3.12，再执行整机安装与检查。
macOS 初次安装需要 Apple Command Line Tools 和 Homebrew 授权；Linux 的原生依赖可能需要 sudo。
SteamOS 保持只读，应用、Python 和编译环境安装在用户目录。Tailscale 登录与代理订阅由本机用户提供。

macOS 完整安装包含 WezTerm、BetterDisplay、Maccy、Ice（`jordanbaird-ice`）和完整版 MacTeX（含图形应用）。
WezTerm 由独立组件安装，其余四项由 [macOS Brewfile](platforms/macos/Brewfile) 安装，并纳入平台检查。

仅准备基础目录、Python 和安装后端：

```sh
./bootstrap.sh
. ~/Software/env.sh
workbench system install
```

引导种子可运行在系统 Python 3.9+ 上，通过 uv 安装托管 Python 3.12 后重新执行；
Workbench 本身始终要求 3.12+。`Software/bin/python`、`python3` 指向托管版本，系统 Python 不替换。
`--offline` 不下载，仅复用已有兼容解释器；`--plan` 不写入、不下载。

## 软件组件与分类

```sh
workbench                         # 按用途分组列出软件和管理命令
workbench list --category development
workbench list --tag runtime
workbench node check
workbench fd update
workbench fzf-tab update
workbench ruff check --probe
workbench emacs restart
workbench mihomo update
workbench gcc install
workbench texlive install
```

| 分类 | 内容 |
| --- | --- |
| cli | fd、ripgrep、jq、bat |
| terminal | WezTerm、Zsh、fzf、Starship、zoxide 与 Shell 插件 |
| development | Python、Node、编译器、LSP、检查工具和可选 MCP 工具 |
| editors | Emacs、Helix |
| network | Mihomo（含 MetaCubeXD）、Tailscale |
| latex | TeX Live |

每个软件只有一个主分类，可拥有多个标签。分类用于显示与筛选，依赖直接指向组件。
Homebrew、dnf、apt、uv、Pixi、nvm 是安装方式，不作为软件用途分类。
Environment Modules 的 `module load/list/unload` 保持原有含义。

## 统一操作

命令形式：`workbench <component> <action> [<target> ...] [options]`。
`list` 查看，`check` 检查，`install` 补齐，`update` 显式升级，`apply` 应用配置。
服务组件按实际能力提供 `start/stop/restart/status/logs`。具体选项以操作帮助为准。

```sh
workbench node --help
workbench emacs install --help
workbench fd update --plan
workbench --platform fedora system install --plan --json
workbench profile apply            # 部署本地配置
workbench profile update           # Git 快进拉取后部署
workbench system check
workbench system update            # 更新整机方案；Linux 可能重新编译 Emacs
```

方案：`base`（命令行）、`workstation`（完整工作站）、`macos` / `steamdeck`（增加平台网络客户端）、
`scientific`（Linux 科研环境，增加 GCC 和 TeX Live）。默认依据主机选择。
Mac 网络部署见 [macOS 网络客户端](docs/macos-network.md)。

终端自动配色并按中英文宽度对齐；`<参数>` 必填、`[参数]` 可选。
管道、重定向、`NO_COLOR` 和 `TERM=dumb` 禁用配色；JSON 与补全数据始终无颜色。
更新补全后重新打开终端或执行 `source ~/.zshrc`。

## 目录与扩展

```text
bin/workbench                    公共入口
lib/workbench/                   发现、校验、依赖计划、执行与输出
components/<软件>/component.toml 软件的唯一声明，可附独立实现脚本
commands/<命令>/command.toml     system/profile/components/providers/platform
providers/<后端>.toml            安装后端来源
profiles/<方案>.toml             整机安装与检查步骤
platforms/<系统>/platform.toml   平台信息和原生依赖
catalog/categories.toml          用途分类标签
config/                         编辑器、Shell、终端等原生配置
libexec/                        共享安装与部署实现
examples/components/            可运行的扩展示例
```

```sh
workbench components create my-app          # ~/Software/extensions/my-app
workbench components create my-app --shared # 仓库 components/my-app
workbench my-app install
workbench my-app check
```

本机扩展与仓库组件使用相同 TOML 协议。默认示例分类为 cli，可直接修改标签。
只接受当前 TOML 协议与独立软件命令，不保留旧聚合入口或 JSON 声明兼容。

软件保存在 `Software/apps`，运行数据在 `Software/data`，日志在 `Software/logs`，
私人配置在 `Software/config/local`，订阅在仓库外。Emacs 包和缓存位于 `Software/data/emacs`。
开发检出可以放在 Software 外，通过 `Software/config/workbench` 接入。

更多说明：[架构](docs/architecture.md)、[日常操作](docs/workbench.md)、[配置同步](docs/sync.md)。
源码导出：`python3 libexec/export-source.py /path/workbench-source.tar.gz`；导出包不含运行数据和私人设置。

软件源配置：见 [镜像源与恢复](docs/mirrors.md)，入口为 `workbench mirrors apply`。
