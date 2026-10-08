# Codex 共享后台维护

定期更新独立安装的 Codex CLI，并把共享 app-server 的安装包和运行版本同步到 CLI。macOS 使用 LaunchAgent，Linux 使用 systemd 用户定时器；不依赖 ChatGPT Desktop 是否启动。

## 使用

Python 环境需要 `websockets>=14`，CLI 需要官方独立安装版 `~/.local/bin/codex`。安装器使用调用它的 Python 解释器生成本机服务配置。

macOS 安装检查任务：

```sh
python3 ~/.codex/tools/codex-daemon-maintenance/install.py
```

### Fedora / Linux

按[仓库同步说明](../README.md#更新与命令入口)将代码更新到 Linux 的 `~/.codex/tools`。

在 Fedora 上安装官方独立版 CLI，并选择它作为终端入口。官方安装步骤见 [Codex CLI 文档](https://learn.chatgpt.com/docs/cli)。

```sh
curl -fsSL https://chatgpt.com/codex/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
~/.local/bin/codex --version
```

确认当前 Python 支持所需模块，然后安装用户定时器：

```sh
python3 -c 'from websockets.asyncio.client import unix_connect'
python3 ~/.codex/tools/codex-daemon-maintenance/install.py
systemctl --user list-timers codex-daemon-maintenance.timer
```

若 Python 缺少模块，可在工具目录建立虚拟环境并用它安装：

```sh
python3 -m venv ~/.codex/tools/codex-daemon-maintenance/.venv
~/.codex/tools/codex-daemon-maintenance/.venv/bin/pip install 'websockets>=14'
~/.codex/tools/codex-daemon-maintenance/.venv/bin/python ~/.codex/tools/codex-daemon-maintenance/install.py
```

安装器先用 `systemd-analyze` 校验生成的配置，通过后才替换服务文件、启用定时器并立即运行一次维护。Linux 的后台启动和升级命令在独立的 systemd scope（进程组）中执行，后台在维护任务结束后继续运行。退出所有登录会话后继续运行需要用户管理器保持启动，可查看 `loginctl show-user "$USER" -p Linger`；需要时执行 `loginctl enable-linger "$USER"`。

后续运行 `maintain.py` 时，使用安装器所选的同一个 Python；使用虚拟环境时，将下文的 `python3` 替换为该环境的 Python 路径。

每 15 分钟检查一次；登录后也检查一次。空闲时每天运行一次官方 `codex update` 检查新版本；发现后台版本或组件不一致时，使用官方 `codex app-server daemon update --from-cli --yes` 同步，并重新读取运行版本确认结果。网络更新前先修复本地版本差异。

查看实时版本、延后原因和最近执行结果：

```sh
python3 ~/.codex/tools/codex-daemon-maintenance/maintain.py status
```

立即执行检查，或提前检查新版：

```sh
python3 ~/.codex/tools/codex-daemon-maintenance/maintain.py run
python3 ~/.codex/tools/codex-daemon-maintenance/maintain.py run --check-updates
```

CLI 使用 `~/.local/bin/codex`，由官方独立安装程序维护。终端和后台版本可分别查看：

```sh
type -a codex
codex --version
~/.local/bin/codex app-server daemon version
```

## 会话与更新

维护器通过共享后台的 `thread/loaded/list` 查询全部已加载会话。有任意已加载会话时，即使当前没有生成回答，也延后更新；关闭该后台中的 CLI 会话后，下次检查会重试。独立运行的 standalone CLI 进程也会阻止升级，避免旧版辅助程序在任务进行中被清理。Linux 使用控制套接字的内核进程凭据确认共享后台身份，再通过父子进程关系识别它的辅助程序；空闲共享后台的辅助程序不会被误判为独立会话。身份查询失败时停止维护。

查询失败、超时或返回格式无法识别时停止维护，记录错误。进程锁避免两个维护器同时执行；下载后再次检查会话。会话查询与官方更新命令之间没有原子的“禁止新任务”操作，因此维护切换的短窗口内新建连接仍可能重连。任务运行期间请保留 CLI 会话。

已停止的托管后台会在空闲检查时恢复启动。尚未安装托管包时，维护器调用官方 `bootstrap`。对于仍运行的非托管旧后台，官方 `stop` 可能拒绝停止；这种状态会记录为错误，需要先核对进程身份及已加载会话，再完成一次迁移。维护器不会自动杀掉未知后台。

## 运行记录与停用

`data/state.json` 保存最近结果、检查时间和每日更新检查时间。`status` 的 `healthy_now` 表示实时后台版本和组件是否正常，`last_run` 只表示上次维护结果。失败记录保留原始错误和可读取的版本信息；非托管后台错误另附一次性迁移提示。运行数据不进入 Git。

macOS 输出保存在 `data/launchd.log` 与 `data/launchd-error.log`，查看和停用：

```sh
launchctl print gui/$(id -u)/xyz.chesszyh.codex-daemon-maintenance
launchctl bootout gui/$(id -u)/xyz.chesszyh.codex-daemon-maintenance
```

停用后，移除 `~/Library/LaunchAgents/xyz.chesszyh.codex-daemon-maintenance.plist` 可防止下次登录重新加载。此操作只停用维护器。

Linux 输出保存在用户日志中，查看和停用：

```sh
journalctl --user -u codex-daemon-maintenance.service -n 30 --no-pager
systemctl --user disable --now codex-daemon-maintenance.timer
```

若一次维护仍在执行，等待结束或执行 `systemctl --user stop codex-daemon-maintenance.service`。服务文件位于 `~/.config/systemd/user/`。

## 验证

```sh
cd ~/.codex/tools/codex-daemon-maintenance
python3 -m unittest -v
```

Linux 还可运行真实 systemd 生命周期检查：

```sh
python3 ~/.codex/tools/codex-daemon-maintenance/check_linux_lifecycle.py
```

检查使用临时服务和模拟后台进程，先复现直接启动的后台在维护任务退出后被清理，再验证独立 scope 中的后台继续存活；结束时清理测试进程和临时服务，不修改正式 Codex 后台。
