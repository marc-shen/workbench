# 日常操作

| 需求 | 命令 |
| --- | --- |
| 按用途查看全部软件 | `workbench` |
| 查看开发环境 | `workbench list --category development` |
| 查看语言运行时 | `workbench list --tag runtime` |
| 查看具体参数 | `workbench node update --help` |
| 更新 Node | `workbench node update` |
| 更新 Python 3.12 补丁版本 | `workbench python update` |
| 更新单个 CLI | `workbench fd update` |
| 更新终端插件 | `workbench fzf-tab update` |
| 检查语言服务协议 | `workbench ruff check --probe` |
| 安装 Emacs 包 | `workbench emacs install packages` |
| 部署 Emacs 服务定义 | `workbench emacs apply service` |
| 重启 Emacs | `workbench emacs restart` |
| 查看代理后台 | `workbench mihomo status` |
| 更新代理订阅 | `workbench mihomo update` |
| 更新 Tailscale（Deck 本机） | `workbench tailscale update` |
| 安装 GCC | `workbench gcc install` |
| 安装 TeX Live | `workbench texlive install` |
| 整机安装/检查 | `workbench system install` / `workbench system check` |
| 更新整机方案 | `workbench system update` |
| 部署本地配置 | `workbench profile apply` |
| Git 更新并部署 | `workbench profile update` |
| 管理安装后端 | `workbench providers list` |
| 新增独立组件 | `workbench components create my-app` |

每个软件使用相同操作语义。帮助中的 `<参数>` 必填、`[参数]` 可选、`...` 可重复。
终端自动配色；NO_COLOR、TERM=dumb、管道和重定向禁用配色。JSON 与补全输出始终无颜色。
执行用 `[1/3]`、`[OK]`、`[PENDING]`、`[FAIL]` 表示进度和结果，安装日志移除 ANSI 颜色码。

```sh
workbench --help
workbench fd update --plan
workbench --platform ubuntu system install --plan --json
NO_COLOR=1 workbench list
```

组件 TOML 修改后，帮助和补全即时读取；终端补全脚本更新后可重新打开终端或 source ~/.zshrc。
system update 可能重新编译 Linux Emacs，日常只同步配置使用 profile update。
