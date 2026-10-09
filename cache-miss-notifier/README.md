# Codex Cache Notifier

监听 ChatGPT Desktop（Codex 任务）和 Codex CLI 写入的本地会话事件，在 cache miss 和缓存命中超过当前上下文窗口 75% 时发送系统通知。

## 本机安装（macOS / Linux）

同时安装缓存提醒与速度检测，并随 ChatGPT App 启停，运行[共同安装器](../codex-speed-monitor/README.md#随-chatgpt-app-启停)：

```bash
python3 ~/.codex/tools/codex-speed-monitor/install_app_services.py
```

仅安装常驻缓存提醒：

```bash
~/.codex/tools/cache-miss-notifier/install.sh
```

macOS 查看状态：

```bash
launchctl print gui/$(id -u)/codex-cache-miss-notifier
```

Linux 查看状态和日志：

```bash
systemctl --user status codex-cache-miss-notifier.service
journalctl --user -u codex-cache-miss-notifier.service -f
```

暂停或重新启动通知服务：

```bash
codex-cache-miss-notifier-toggle
```

卸载：

```bash
~/.codex/tools/cache-miss-notifier/uninstall.sh
```

## 行为边界

- 支持本机 ChatGPT Desktop 中的 Codex 任务和本机 Codex CLI。
- 会话记录以换行符为完整边界；末尾尚未写完的记录（包括启动时已有的半条记录）会保留，等后续写完后再处理，避免漏报或重复通知。
- 同一会话先出现缓存活动、之后 `cached_input_tokens` 变为 `0` 时，才判定为 cache miss；第一次请求和 compaction 后的第一次请求不通知。
- `cached_input_tokens` 严格大于该任务 `model_context_window` 的 75% 时发送 cache hit 通知；等于该值时不通知。缺少上下文窗口信息时不发送 cache hit 通知。
- 不监控普通 ChatGPT 聊天，因为普通聊天不会把逐次上游 token 使用量写入 `~/.codex/sessions`。
- 仅当事件包含 `prompt_cache_diagnostics` 时，通知才会展示 `reason`、`cache_missed_tokens` 和 `comparison_reusable_tokens`。

直接运行（不发送通知，只打印后续 miss）：

```bash
codex-cache-miss-notifier --dry-run --verbose
```
