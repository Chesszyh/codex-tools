# Codex Tools

面向 Linux 和 macOS 的 Codex 本地工具：查看请求平均输出速度、接收缓存通知、维护共享后台，以及切换用户技能配置。工具读取本机日志与配置，运行数据保存在本机。

| 工具 | 用途 | 平台与使用说明 |
| --- | --- | --- |
| [输出速度检测](codex-speed-monitor/README.md) | 记录各会话的请求平均输出速度，绘图并导出数据 | Linux、macOS |
| [缓存提醒](cache-miss-notifier/README.md) | 在提示词缓存未命中或大比例命中时发送桌面通知 | Linux、macOS |
| [共享后台维护](codex-daemon-maintenance/README.md) | 空闲时定期更新 Codex CLI 并同步共享后台版本 | Linux、macOS |
| [`codex-skills-mode`](codex-skills-mode) | 在原技能配置与禁用用户技能的极简模式之间切换 | Linux、macOS；Python 3.11+ |

## 快速开始

需要 Python 3.11+ 和本机 Codex 日志。克隆后可直接运行速度检测：

```bash
git clone https://github.com/Chesszyh/codex-tools.git ~/.codex/tools
python3 ~/.codex/tools/codex-speed-monitor/monitor.py
```

打开终端显示的本机地址查看图表；按 `Ctrl+C` 停止。速度是已完成请求的平均值，包含等待与推理，具体口径见[速度检测说明](codex-speed-monitor/README.md#测量口径)。

## 更新与命令入口

在仓库中拉取更新：

```bash
cd ~/.codex/tools
git status --short
git pull --ff-only
```

`--ff-only` 只接受直接推进分支；两端各有提交时，先处理分支差异再继续同步。Git 同步代码，不会自动重启已经运行的工具。监测相关代码更新后，在该机器重新运行[安装器](codex-speed-monitor/install_app_services.py)。

如需直接使用仓库根目录的命令，在各自的 `~/.zshenv` 中加入：

```bash
export PATH="$HOME/.codex/tools:$PATH"
```

## 安装缓存提醒与速度检测

两端都需要安装 ChatGPT App 和 Python。统一使用 Python 3.11+ 可同时运行仓库中的技能切换工具。安装器让缓存提醒与速度检测随 App 主进程启动、退出；仅关闭窗口但 App 仍在后台时，工具继续运行。

速度图表默认位于 <http://127.0.0.1:8766/>。每台机器展示自己的会话；采样口径、导出格式和运行选项见[速度检测说明](codex-speed-monitor/README.md)。

### Linux

本机使用 systemd 用户服务。需要 `chatgpt`、`python3`、`inotifywait` 和 `notify-send` 命令；Fedora 的对应依赖可安装为：

```bash
sudo dnf install python3 inotify-tools libnotify
```

然后安装两个工具及 App 启停监听器：

```bash
python3 ~/.codex/tools/codex-speed-monitor/install_app_services.py
```

安装器通过 `chatgpt` 命令定位 App，重新生成当前用户的服务配置，并取消两个工具的登录常驻启动。

### macOS

macOS 使用 LaunchAgent（用户后台服务配置）。除 Python 外，启停监听器的编译还需要 Command Line Tools 中的 `swiftc`；未安装时运行：

```bash
xcode-select --install
```

先安装缓存提醒的 LaunchAgent：

```bash
~/.codex/tools/cache-miss-notifier/install.sh
```

新克隆仓库时，创建速度检测的 LaunchAgent 配置；文件里的路径由当前机器生成：

```bash
python3 - <<'PY'
from pathlib import Path
import plistlib
import sys

root = Path.home() / ".codex/tools/codex-speed-monitor"
agents = Path.home() / "Library/LaunchAgents"
agents.mkdir(parents=True, exist_ok=True)
label = "xyz.chesszyh.codex-speed-monitor"
config = {
    "Label": label,
    "ProgramArguments": [sys.executable, str(root / "monitor.py")],
    "RunAtLoad": False,
    "KeepAlive": False,
}
(agents / f"{label}.plist").write_bytes(plistlib.dumps(config))
PY
```

最后运行共同安装器，它会注册两个工具并安装 App 启停监听器：

```bash
python3 ~/.codex/tools/codex-speed-monitor/install_app_services.py
```

更新仓库后通常只需重新运行共同安装器。单独重新运行缓存提醒的 `install.sh` 会恢复该工具的常驻启动配置，之后再运行共同安装器即可恢复随 App 启停。

## 本机配置与数据

以下内容各自保留在运行它的机器上：

| 内容 | 保存位置 | 同步方式 |
| --- | --- | --- |
| 工具代码、文档与服务模板 | 本仓库 | Git |
| 速度采样、数据库、导出图表 | `codex-speed-monitor/data/` | 已被 Git 忽略；需要比较两台机器时分别导出 CSV |
| 共享后台维护结果与检查时间 | `codex-daemon-maintenance/data/` | 已被 Git 忽略 |
| Linux 服务配置 | `~/.config/systemd/user/`，或 `XDG_CONFIG_HOME` 指定的配置目录 | 在本机运行安装器生成 |
| macOS 服务配置 | `~/Library/LaunchAgents/` | 在本机运行安装器生成 |
| Codex 会话、配置与技能切换状态 | `~/.codex/` 下的仓库外目录 | 不属于这个工具仓库 |

`codex-skills-mode` 会修改当前机器的 Codex 配置，使用 `status` 查看状态、`minimal` 切换极简模式、`current` 恢复切换前的技能配置：

```bash
codex-skills-mode status
codex-skills-mode minimal
codex-skills-mode current
```

## 查看服务状态

Linux：

```bash
systemctl --user status codex-app-monitor-services.service codex-cache-miss-notifier.service codex-speed-monitor.service
```

macOS：

```bash
launchctl print gui/$(id -u)/xyz.chesszyh.chatgpt-monitor-services
```

缓存提醒的暂停、恢复和卸载见[缓存提醒说明](cache-miss-notifier/README.md)。速度检测的独立运行方式见[速度检测说明](codex-speed-monitor/README.md)。

## 许可证

[MIT](LICENSE)。
