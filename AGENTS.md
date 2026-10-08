# 仓库工作约定

本仓库维护 Linux 与 macOS 的 Codex 本地工具。用户当前明确指令优先。

## 文档职责

- `README.md` 面向使用者，负责工具入口、跨机器同步和平台安装步骤。
- 各工具目录的 `README.md` 负责该工具的日常操作与行为说明。
- 本文件面向代理，负责修改边界、验证和部署约定。用法不要在这里再维护一份。

## 修改前

检查当前分支与工作区状态，默认直接在主分支 `master` 修改。保留已有未提交修改；提交和推送按用户授权执行。

涉及部署时，先检查实际服务、进程和安装路径。不要把上次的 PID、服务状态或日志数量当作当前状态。

## 代码与平台边界

- 工具路径从脚本位置或当前用户的主目录生成。共享代码不要写死个人主目录。
- Linux 通过 systemd 用户服务管理监测工具；macOS 通过 LaunchAgent 管理。修改一端时保留另一端的实现与入口。
- App 生命周期以主进程存活为准，不以窗口数量或 Electron 辅助进程为准。
- 缓存提醒规则由 `cache-miss-notifier/cache_miss_notifier.py` 实现；速度测量由 `codex-speed-monitor/monitor.py` 实现。部署任务不应顺带改变这些规则。
- 速度检测没有逐 token 到达记录。界面与报告应明确区分请求平均速度、非推理输出和墙钟吞吐，不把请求完成后的用量更新称作即时文字流速。
- 注释只解释代码本身看不出的原因。文档写最终行为，不记录中间尝试或未经验证的运行状态。

## 同步与本机数据

Git 同步脚本、文档和服务模板。以下内容属于本机运行状态：

- `codex-speed-monitor/data/`：采样数据库、CSV、图表、日志与 PID 文件。
- 仓库外的 Codex 会话、配置、技能切换状态，以及系统服务配置。

保持已有 Git 忽略规则，保留这些数据。部署更新在目标机器重新生成路径和服务配置，不直接复制另一台机器的安装结果。

## 验证

先确定检查能发现哪个具体错误。文档修改只核对命令、相对链接和代码对应关系；不要因此启动服务或运行完整测试。

修改缓存判定时，在 `cache-miss-notifier/` 运行：

```bash
python3 -m unittest discover -s tests
```

修改速度测量或持久化时，在 `codex-speed-monitor/` 运行：

```bash
python3 -m unittest -v
```

修改 App 启停管理时，按当前平台运行对应的集成检查：

```bash
python3 codex-speed-monitor/tests/check_app_services_linux.py
python3 codex-speed-monitor/tests/check_app_services.py
```

第一条用于 Linux，第二条用于 macOS。这些检查使用临时测试 App/进程及服务，避免退出承载当前任务的 ChatGPT App。报告应区分临时进程的启停验证与实际 App 的完整退出验证。

## 部署

共同入口是 `codex-speed-monitor/install_app_services.py`，平台安装步骤以仓库 README 为准。只有任务包含部署、安装或重启时才修改用户服务配置。

部署完成后检查启停监听器、缓存提醒和速度检测的实际状态，确认缓存监听进程与速度图表可用，并保留采样数据。手动暂停缓存提醒的行为应继续有效。
