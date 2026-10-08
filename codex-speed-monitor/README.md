# Codex 会话速度监测

读取本机 Codex 日志，按会话记录模型请求的输出速度，提供实时网页图表和可保存的历史数据。使用 Python 3.10+ 标准库。

```bash
python3 ~/.codex/tools/codex-speed-monitor/monitor.py
```

打开终端显示的地址，默认是 <http://127.0.0.1:8766/>。按 `Ctrl+C` 停止；数据持续保存，重新运行会保留之前的记录。

运行期间，保存目录的 `monitor.pid` 记录进程号。若在后台运行，停止默认目录的监测进程：

```bash
kill "$(cat ~/.codex/tools/codex-speed-monitor/data/monitor.pid)"
```

网页支持按模型、会话、时间范围筛选，悬停查看请求详情，保存当前图表为 PNG 或 SVG，导出 CSV 和可离线打开的网页。

只回填并保存最近两小时：

```bash
python3 ~/.codex/tools/codex-speed-monitor/monitor.py --once
```

只采样十分钟，不启动网页：

```bash
python3 ~/.codex/tools/codex-speed-monitor/monitor.py --no-server --duration 600
```

指定保存位置和回填范围：

```bash
python3 ~/.codex/tools/codex-speed-monitor/monitor.py --output ~/Documents/codex-speed --lookback-minutes 30
```

所有选项见 `monitor.py --help`。

## 随 ChatGPT App 启停

Linux 与 macOS 的依赖、首次安装、代码同步后的更新和状态查看，统一见[仓库安装说明](../README.md#安装缓存提醒与速度检测)。

服务意外退出后会自动重启；单独使用 Codex CLI 时不会自动启动服务。Linux 上手动暂停缓存提醒会保持暂停，下一次打开 App 时恢复。

## 测量口径

每隔 2 秒检查日志。一次模型请求完成后，新增一个速度点；生成中和工具执行中显示已等待时间，不填入虚假的即时 token 数。

- **请求平均速度**：该请求的输出 token 数，除以从本地发出请求到最后一条输出完成事件的时间。包含首段等待和推理；排除最后一条输出完成之后的工具执行时间。若多次工具调用与生成交错，中间等待仍可能计入。
- **非推理输出速度**：从输出 token 数中扣除推理 token，使用相同耗时。包含回复文字和工具调用参数，不能当作纯文字显示速度。
- **最近 5 次速度**：当前模型和推理档位的最近五次请求，总输出 token 数除以总请求耗时，不是五个速度的算术平均。
- **60 秒墙钟吞吐**：最近 60 秒内完成并上报的请求输出 token 总数除以 60，包含工具、等待和空闲。长请求的 token 在完成时整批计入，因此会呈现突增。

本机现有 `logs_2.sqlite` 和会话 JSONL 日志没有逐 token 到达时间，无法由这些日志恢复每秒文字流速。图表实时刷新的是已完成请求的测量值。缺少请求开始或输出完成事件时，不生成速度点。

自动发现本机日志里新活跃的 Codex 会话，也包括子代理会话。模型和推理档位按每个请求保存；不会向会话发送消息。远程主机的会话需要在对应主机运行本工具。

## 保存内容

默认保存在脚本旁的 `data/`，该目录已被 Git 忽略：

| 文件 | 内容 |
| --- | --- |
| `metrics.sqlite3` | 全部请求测量值和定时状态记录 |
| `requests.csv` | 全部已保存的请求数据 |
| `observations.csv` | 每次采样的会话状态、等待时间和吞吐 |
| `latest.svg` | 已保存的会话速度曲线 |
| `snapshot.html` | 可离线打开的交互图表快照 |
| `latest.json` | 最近一次导出的图表数据 |

每次有新请求或间隔 15 秒更新图表快照；停止时再保存一次。请求按日志记录 ID 去重，重启和重复回填不会重复记录请求。数据记录会话标题、ID、模型、时间和 token 用量，不保存聊天正文或工具参数。
