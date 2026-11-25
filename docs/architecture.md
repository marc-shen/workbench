# Workbench 3 架构

## 声明与实现

框架使用 Python 3.12 标准库，通过 `tomllib` 读取人工维护的 TOML 声明。
安装回执、运行状态、第三方 API 和 `--json` 输出仍为 JSON。
Shell、Emacs Lisp、Brewfile、Mihomo YAML 等第三方配置保留原生格式。

每个软件在 `components/<名称>/component.toml` 中定义一次。安装器、帮助、补全和依赖计划读取同一份声明。
`category` 是唯一用途分类，`tags` 描述辅助能力；分类不决定安装来源或执行顺序。
分类定义在 `catalog/categories.toml`。独立组件可引用共享后端，也可使用自身的任意可执行脚本。

```toml
schema = 1
name = "my-app"
description = "我的软件"
category = "development"
tags = ["runtime"]
platforms = ["macos", "steamdeck", "fedora", "ubuntu", "debian"]
requires = ["node"]
entrypoint = ["{python}", "{component}/run.py"]
arguments = ""

[actions.list]
description = "查看状态"

[actions.check]
description = "验证安装"

[actions.install]
description = "安装缺失项"

[actions.update]
description = "更新软件"
```

`{python}` 是运行框架的 3.12+ 解释器；其他占位符为 `{repo}`、`{component}`、`{root}`、`{platform}`。
入口接收操作和后续参数的独立 argv，不经过 eval。
环境提供 SOFTWARE_HOME、SOFTWARE_USER_HOME、WORKBENCH_HOME、PROFILE_PLATFORM、PROFILE_PYTHON。

操作可声明 `arguments`、`options`（选项语法到说明的映射）、`targets` 和 `platforms`。
`entrypoints` 可按平台覆盖默认入口。声明校验不执行组件代码。
本机扩展位于 `Software/extensions/<名称>/component.toml`；同名冲突或依赖环在执行前报错。

## 软件和安装后端

内置组件的 `[install]` 声明 `kind`、`provider` 以及该软件所需的版本、来源等信息。
通用后端实现位于 libexec；Workbench 不维护另一份按安装来源划分的软件清单。
例如 Node 的用途是 development，标签是 runtime/javascript，安装后端是 nvm。
Shell 插件属于 terminal，由 Git 下载；语言服务器属于 development，可由 uv 或 Pixi 安装。

install/update 先解析 `requires` 中的具体组件，再补齐需要的 uv/Pixi 后端。
已有依赖执行 install，不因更新目标而自动升级所有依赖。
`providers_by_platform` 允许声明额外平台后端，例如 Steam Deck 编译 Emacs 所需的 Pixi。
软件的实际版本、命令路径和安装来源写入 JSON 回执；旧安装目录可复用，不因声明改名重装应用。

## 框架管理命令

`commands/` 单独保存 system、profile、components、providers、platform 声明，首页与软件分开展示。
这些命令用于整机方案、配置部署、扩展脚手架、后端管理和平台前提，不参与软件用途分类。

`profiles/*.toml` 组合明确的组件操作，可使用 extends 继承其他方案。步骤字段为 component/action/args。
步骤可选 `platforms` 限定执行平台，继承后仍按实际主机筛选；例如 workstation 中 Snug 的安装和检查仅用于 macOS。
未声明该字段的步骤继续在方案支持的全部平台执行，组件自身的平台限制仍由计划器检查。
安装/更新后执行方案的完整 check；相同依赖步骤去重。默认 Steam Deck 选择 steamdeck，其余选择 workstation。
`after_install` 中的步骤只在 `system install` 的安装和检查全部成功后执行，也支持继承。
macOS 用它调用 `texlive offer`，询问是否安装官方 MacTeX PKG；拒绝或非交互运行时跳过，
不把可选软件加入强制检查。`system check/update` 不执行安装询问。
跨平台 `--platform` 只允许预览，不能伪装为其他系统实际安装。

## 引导和 Python

bootstrap.sh 只创建基础环境，不安装全部应用或部署私人配置。
系统 Python 3.9+ 运行小型引导种子：准备 uv，安装托管 Python 3.12，验证后重新执行 bootstrap。
正式框架入口拒绝低于 3.12 的解释器，使用标准库 tomllib，无额外 TOML 解析依赖。
托管解释器在 Software/data/uv/python，稳定入口在 Software/apps/python/current 与 Software/bin/python3。
系统解释器不被替换。离线引导必须已有兼容解释器；预览不下载或写入。

install.sh 在需要时准备原生引导前提，随后运行 bootstrap 和 system install。
macOS 初次 CLT/Homebrew 授权、Tailscale 登录和代理订阅属于主机前提。
SteamOS 不解除只读，构建依赖通过用户目录环境提供。

## 执行与恢复

安装、更新等写操作使用跨进程锁，运行回执位于 Software/data/workbench/runs，日志位于 Software/logs。
`open` 只负责打开应用或网页，不占用或等待安装锁；保持应用窗口打开不会阻止其他应用启动。
回执只保存组件、操作和状态，不保存可能包含秘密的任意参数。
失败停止后可修复并重跑同一命令；install 必须可重复执行。
只读列表、帮助、补全和 --plan 不下载、不安装、不写运行记录。

配置部署与应用进程分离，普通 apply 不重启 Emacs。
JSON 运行记录、订阅、身份凭据以及 Emacs 包和缓存均保存在源码目录之外。
不保留旧 module/component JSON、聚合软件命令或旧目录兼容入口。
