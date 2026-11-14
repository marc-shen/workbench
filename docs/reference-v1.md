> 历史版本记录；当前命令与安装方式以 README 和 docs/workbench.md 为准。

# 历史版本参考（以 README.md 与 architecture.md 为准）

# profile

面向 **macOS、Fedora 和 Steam Deck** 的个人开发环境配置，统一管理 Emacs、Zsh、终端工具、Python CLI 和编译环境。Ubuntu / Debian 已有安装适配，尚未完成实机验证。

配置通过 Git 同步；程序在每台机器上按平台安装。用户软件采用 `~/Software` 布局，以 `bin` 作为稳定命令入口，以 `apps/<名称>/<版本>` 容纳管理器、程序、编译器和复杂环境。Steam Deck 的安装保持在用户目录中，不解除 SteamOS 只读。

Steam Deck 的 Mihomo／MetaCubeXD、Tailscale 安装入口
也由 profile 管理：`workbench profile apply --only network`。
本机管理员授权、Tailscale 登录和节点导入见 [Deck 网络配置](docs/steamdeck-network.md)。
订阅、节点和私密凭据不保存到仓库。

## 从哪里开始

| 要做的事 | 文档 |
|---|---|
| 在新机器安装 | 本页的[快速部署](#快速部署) |
| 把修改同步到另一台设备 | [多设备同步](docs/sync.md) |
| 升级工具、管理编译器、重启 Emacs、排查问题 | [日常维护](docs/maintenance.md) |
| 理解 Software 布局和安装来源 | [Software 管理规范](docs/Software.txt) |
| 配置 Steam 游戏模式里的 Emacs | [Steam Deck 说明](docs/Steam-Deck.txt) |
| 查看 Emacs 操作 | [常用键位](emacs.d/KEYBINDINGS.md)、[完整键位](emacs.d/KEYBINDINGS-FULL.md) |
| 查看此前实际安装、清理和验证情况 | [2026-10-21 迁移记录](docs/environment-migration-2026-10-21.md) |

## 包含什么

- **Emacs**：完整的 `emacs.d`，包含补全、项目导航、Git、终端、Python/C/Fortran、LaTeX、Markdown、Org、PDF 等配置，以及可选 AI/agent 集成。`em`、`ec` 共用当前用户的默认 daemon。
- **Zsh**：Modules、fzf、fzf-tab、zsh-completions、zsh-autosuggestions、zsh-syntax-highlighting、zoxide 和 Starship。
- **终端工具**：fd、ripgrep、fzf、zoxide、Starship、jq、bat、Helix（`hx`）、Zsh，清单见 [tools.json](catalog/tools.json)。Neovim / NvChad 配置完整保留在 `archieve/nvim/`，不再默认安装、部署或作为编辑器候选。
- **环境管理**：官方独立 Pixi、uv；基础语言服务及可选 MCP 工具；Environment Modules 用于按需选择编译器和科研环境。
- **终端配置**：Starship 和 WezTerm 配置。部署会链接 WezTerm 配置，但不安装 WezTerm 应用。共享配置默认以 80 列 × 24 行窗口启动；Linux 窗口大小也受桌面窗口管理器控制，不自动全屏，F12 可手动切换全屏。

安装需求通常不固定版本，显式更新时解析可用版本；已安装程序的精确依赖保存在本机 `pixi.lock` 和 `receipt.json` 中。Node.js 例外：固定 26.13.0 LTS，由 nvm 从官网安装并提供 npm/npx，详见[维护说明](docs/maintenance.md#nodejsnvm-和-npm)。其余基础 CLI 使用独立 Pixi 环境，是否包含 Python 由依赖决定，不会为所有 CLI 默认安装 Python。Yazi 不在当前方案中。

### 平台与来源

| 组件 | macOS | Fedora | Steam Deck | Ubuntu / Debian |
|---|---|---|---|---|
| 基础 Git、证书、构建组件 | Xcode Command Line Tools / 系统 | 系统 / DNF | 使用现有系统组件 | 系统 / APT |
| Pixi / uv | 官方发行二进制 | 官方发行二进制 | 官方发行二进制 | 官方发行二进制 |
| 基础 CLI | Pixi / conda-forge | Pixi / conda-forge | Pixi / conda-forge | Pixi / conda-forge |
| Emacs | Homebrew `emacs-app` | 源码，系统开发库 | 源码，应用内 Pixi 开发库 | 源码，系统开发库 |
| Modules | Homebrew | 系统包 | 源码，私有 Tcl | 系统包 |
| 验证情况 | 已实机验证 | 已实机验证 | 已实机验证 | 适配待实机验证 |

本项目仅在 macOS 使用 Homebrew。工具缺少 Pixi 包时，需要明确增加系统包、上游二进制或源码配方；安装器不会因网络错误自动切换来源。平台验证记录见迁移文档，不代表所有系统版本、架构和可选功能都已测试。

## 快速部署

### 1. 准备系统基础组件

需要 Git、curl、CA 证书和 Python 3.9+。

**macOS**：先安装 Xcode Command Line Tools，再按 [Homebrew 官方说明](https://brew.sh/) 安装 Homebrew，完成安装器提示的 shell 初始化。

```sh
xcode-select --install
```

**Fedora**：

```sh
sudo dnf install git curl ca-certificates python3
```

**Ubuntu / Debian**：

```sh
sudo apt-get update
sudo apt-get install git curl ca-certificates python3
```

**Steam Deck**：使用系统已有组件，不运行 pacman 或关闭只读。若上述基础命令缺失，先检查系统状态。

### 2. 获取仓库并安装

以下流程用于新机器。已有 checkout 的机器直接进入原目录，不必再 clone 一份。

```sh
git clone https://github.com/marc-shen/workbench.git ~/Software/src/workbench
cd ~/Software/src/workbench
./bootstrap.sh --plan
./bootstrap.sh
. ~/Software/env.sh
workbench profile install
bash libexec/install-emacs-packages.sh
workbench emacs apply service
```

`bootstrap.sh` 只创建 Software 目录、安装 `workbench`、接入 PATH，并补齐缺少的 uv/Pixi；不部署应用、编辑器或网络服务。`--plan` 只预览，`--offline` 跳过管理器下载。之后用 `workbench profile install` 安装基础工具、平台依赖、Emacs、Modules 和 Zsh 插件，再部署配置链接。Linux 源码编译可能较久；Fedora / Ubuntu / Debian 安装系统依赖需要 sudo。个人部署应由本人运行，不要给整条个人安装命令加 sudo。

Emacs 包单独安装，之后才启用后台服务。现有配置被替换前会保存到 `Software/backups`；如果原目录和 checkout 中都存在独立的 Emacs 运行数据，部署会停止，需先核对数据。

macOS 部署会自动生成统一的 `~/Applications/Emacs Client.app`，包含 Dock 常驻、新窗口焦点恢复及正常重启所需的组件。运行时入口是包内 `Contents/Runtime/Emacs.app`，首次默认链接 Homebrew 原版，后续部署保留你选择的完整应用或符号链接。插件安装脚本优先使用统一包内的 Emacs；显式设置 `EMACS_BINARY` 时使用指定程序。只部署 Emacs 也会生成后台需要的 `Software/env.sh`，不必先部署 Zsh。系统 Rime/Squirrel 的安装及用户配置仍单独管理。

安装完成后进入 Zsh，再检查入口：

```sh
exec ~/Software/bin/zsh -l
```

```sh
cd ~/Software/config/workbench
workbench profile check
```

安装 Zsh 与修改账户登录 shell 是两件事。安装器不会执行 `chsh`；新终端可显式使用 `~/Software/bin/zsh -l`。已配置的 Deck 保留系统 Bash 账户，并通过交互终端入口进入用户 Zsh，详见维护文档。

### 3. 按需安装 Python CLI

```sh
cd ~/Software/config/workbench
bash libexec/install-uv-tools.sh       # fortls、basedpyright、ruff（显式升级）
```

Steam Deck 可另行安装 WezTerm 和 TeX Live，均保存在 Software 中，不关闭系统只读：

```sh
python3 libexec/install-wezterm.py
python3 libexec/install-texlive.py
```

这两项不随基础环境自动安装。来源、中文排版方案和更新命令见 [Steam Deck 维护说明](docs/Steam-Deck.txt)。

可选 MCP 工具：

```sh
bash libexec/install-uv-tools.sh arxiv-mcp-server markitdown-mcp zotero-mcp-server
```

这些是 uv 的独立工具环境。安装不会替你创建 MCP 客户端配置、账号凭据或项目环境。

## 日常命令

| 命令 | 行为 |
|---|---|
| `em 文件名` | 连接已有 Emacs 后台；图形模式打开后返回终端 |
| `em` | 打开当前目录 |
| `ec 文件名` | 连接同一后台，等待编辑结束；适合 Git 的编辑器调用 |
| `ediff 文件A 文件B` | 在同一 Emacs 后台中左右比较两个文件 |
| `ls \| em` | 将管道文本放入可编辑的 `*stdin*` 缓冲区，图形模式立即返回 |
| `ls \| ec` | 导入管道文本，等待 `C-x #`；保留未保存的缓冲区 |
| `ga` | `git add . && git commit`；暂存当前目录及其子目录，不推送 |
| `gap` | `git add -A && git commit && git push`；暂存整个仓库的修改、新增和删除 |
| `gap -p` / `gap --patch` | 分块选择已有文件的修改，然后提交、推送 |
| `gap -m "说明"` | 全部暂存，使用给定提交说明并推送 |
| `sz` | 重新加载 `~/.zshrc` |
| `module avail` / `module list` | 查看可用 / 已加载环境 |
| `software-app list` | 查看 Software 下的应用目录 |

`ec` 中编辑完按 **`C-x #`**，或执行 `M-x server-edit`；仅保存不会结束等待。无图形环境的 SSH 会话中，`em`、`ec` 使用终端界面，此时 `em` 也必须保持连接。默认 `EDITOR` / `VISUAL` 优先使用 `ec`。

`em +行号:列号 文件名` 可以定位，多个文件名可以一起传入。管道用法和保存方式见 [Emacs 终端输入](docs/maintenance.md#emacs-终端输入)。

`gap` 的 `-p` / `--patch` 必须是第一个参数，其余参数传给 `git commit`。提交包含先前已经暂存的内容；分块界面按 `q` 只结束选择，并不取消整个函数。新建未跟踪文件需要显式 `git add 文件名`。前一步失败时，后续步骤不会执行。

Emacs 语言服务默认包括 Python basedpyright、Fortran fortls、C/C++ clangd、Bash、JSON、YAML，并安装 Ruff 供 Python 格式化。Python 工具使用 uv 隔离环境，其他语言服务使用 Pixi；版本自由更新，实际版本和锁文件留在本机。详见 [语言服务](docs/language-servers.md)。

Helix 使用 `hx 文件名` 启动，`Ctrl-Y` 打开内置文件选择器。三平台统一通过 Pixi 安装，更新可运行 `software-app install helix`，再运行 `workbench profile link --only helix` 部署配置；已有配置会备份。语言服务器按需单独安装，详见 [Helix 配置说明](helix/README.md)。

## 配置放在哪里

```text
profile/                         # Git 管理的可同步内容
├── README.md
├── bootstrap.sh                 # Software 基础引导
├── manage.d/                    # 独立管理模块（自动发现）
├── zshrc / shell/               # Shell 配置、通用环境、Modules 初始化
├── emacs.d/                     # 完整 Emacs 配置；运行数据由 .gitignore 排除
├── helix/                       # Helix 配置；部署到 ~/.config/helix
├── starship.toml / wezterm.lua
├── catalog/                   # CLI、系统依赖、Git 插件、可选 uv 工具清单
├── libexec/                     # 安装、源码构建、部署和清单导出
├── platforms/steamdeck/           # Steam 启动器和本机 native 模块构建
├── modulefiles/                 # 可复用模板，实际模块在各机 Software 中
├── docs/                        # 同步、维护、平台说明及历史记录
└── archieve/                    # 历史文件与 nvim/ 配置，不作为活动安装配置
```

部署后，`~/.zshrc` 链接到仓库 `zshrc`，`~/.emacs.d` **完整链接**到仓库 `emacs.d`，`~/Software/config/workbench` 链接到该 checkout。因此直接修改 `~/.zshrc` 或已跟踪的 Emacs 配置文件，就会修改 Git 工作树。

本机变量放在 `~/Software/config/local/env.sh`；交互 Shell 的本机扩展放在 `~/Software/config/local/zshrc`。它们不在仓库中。Emacs 的 `custom.el`、ELPA、`.eln`、缓存、历史等同样不通过 Git 同步；需共享的设置写入已跟踪的配置文件。

## 多设备同步

修改设备提交并推送；接收设备拉取同一分支。不要同步整份 Software 二进制目录。

```sh
cd ~/Software/config/workbench
workbench profile update   # 拉取、部署、补齐缺失依赖并检查
```

已手动 `git pull` 时，使用 `workbench profile apply`；先预览可加 `--plan`。第一次从尚无 workbench 命令的旧版升级，先 `git pull --ff-only`，运行一次 `./bootstrap.sh`，再执行 `workbench profile apply`。本地有未提交修改时 update 会停止，apply 可以部署当前工作树并记录 dirty 状态。

部署会更新链接、命令入口、Mac Dock 应用及服务定义，记录本机上次部署状态并验证链接；不会重启正在编辑的 Emacs。默认补装缺失工具、插件和语言服务；只部署配置可用 `--config-only`。剩余缺失依赖（例如 Emacs 本体）退出码为 2。重新加载方式、备份和日志见[完整同步流程](docs/sync.md)。

软件升级独立执行，例如 `workbench languages update` 或 `workbench fd update`。`workbench profile update` 只做 Git 快进同步和配置部署；完整环境升级须显式执行 `workbench system update`，在 Linux 会编译 Emacs。

## 共享安装与限制

默认是 `~/Software` 个人安装，也提供管理员安装入口：

```sh
workbench profile plan --scope system --prefix /opt/Software
sudo ~/Software/bin/workbench profile install --scope system --prefix /opt/Software
```

共享前缀由管理员维护，个人配置、缓存、凭据和 Emacs server 仍应分开。此流程尚未完成端到端实机验证，个人服务和插件对共享路径的接入仍需核对；不要将其视为已验证的一键多用户部署。macOS 的 Homebrew 应先由其普通用户拥有者安装所需组件；Steam Deck 不使用 `/opt` 共享安装。具体边界见[维护指南](docs/maintenance.md)。

## 扩展管理命令

日常入口为 `workbench <模块> <操作>`，例如 `workbench fd update`、
`workbench profile apply`、`workbench network mihomo update`。`workbench list` 列出模块，
`workbench <模块> --help` 显示用途和操作；Tab 动态补全新模块及其声明的操作。

共享脚本放在仓库 `manage.d/`，仅本机脚本放在
`~/Software/config/manage.d/`。添加带 shebang 的可执行脚本即可调用，无需改注册表；
同名模块会报错，不会静默覆盖。协议和示例见 [workbench 扩展说明](docs/workbench.md)。
