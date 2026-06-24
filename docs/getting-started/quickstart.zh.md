# 快速开始

本演练会派发一个子智能体,然后以 YanShi 的方式监控它:拉取一个紧凑的状态和一段简短的摘要,
而绝不读取子进程的原始流。它假定你已经[安装好 YanShi](installation.md) 以及至少一个厂商 CLI。

## 1. 检查你的适配器

始终从 `doctor` 开始。它会报告哪些适配器拥有可用的可执行文件和有效的鉴权:

```bash
yanshi doctor
```

每一行都是一个 JSON 对象;在派发之前,请修好你打算使用的任何适配器。

```text
{"cli": "claude", "status": "ok", "executable": "/usr/local/bin/claude", "version": "…", "errors": [], "warnings": []}
{"cli": "codex", "status": "failed", "executable": null, "version": null, "errors": ["missing CLI executable: codex"], "warnings": []}
```

## 2. 派发一个任务(阻塞)

该 CLI 会内联运行共享的监控内核,并阻塞直到子进程到达终态,然后打印一个 `RunResult`:

```bash
yanshi dispatch --cli claude --effort high "Summarize the architecture of this repo"
```

一次成功的运行会打印类似下面的内容:

```json
{"agent_id": "…", "cli": "claude", "state": "succeeded", "is_error": false, "reply": "…", "usage": {"input_tokens": 1200, "output_tokens": 340, "...": 0}, "cost_usd": 0.01, "pricing_status": "native", "log_dir": "…/.yanshi/agents/…"}
```

!!! note "CLI 派发始终是 `--wait`"
    `yanshi dispatch` 在设计上就是阻塞的(`--wait` 是默认值;`--no-wait` 会被拒绝)。如果需要在
    长驻宿主中运行*后台*子智能体,请使用库的 `dispatch_background`——见
    [Python API](../library/python-api.md)。

## 3. 以低上下文观察

当一次运行正在进行中(从第二个 shell)或结束之后,该运行都会被记录在磁盘上。列出已知的
agent,然后拉取那两个——也只有那两个——低上下文对象:

```bash
yanshi list                 # JSON array of known agent ids
yanshi status <agent_id>    # deterministic AgentStatus snapshot
yanshi summary <agent_id>   # advisory 1-3 sentence rolling summary
```

`status` 返回确定性快照:`state`、`counters`、`usage`、`cost_usd`、`errors`、`warnings`、
`last_event` 以及存活状态。`summary` 返回建议性的滚动摘要字符串。

!!! warning "低上下文轮询规则"
    上层 agent 应当**只**消费 `status` 和 `summary`。原始事件流保留在
    `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` 以供审计和调试,且**绝不能**被粘贴进上层的
    上下文,除非有人明确要求查看原始日志。正是这条规则让舰队编排保持低廉——见
    [监控](../concepts/monitoring.md)。

## 4. 等待与取消

要阻塞直到一次运行到达终态(轮询磁盘,而不是重新解析流):

```bash
yanshi wait <agent_id> --timeout 300
```

要中断一次运行,YanShi 会向记录在案的子进程发出信号(优雅中断,逐步升级到 `SIGKILL`),
并把状态最终确定为 `cancelled`:

```bash
yanshi cancel <agent_id>
```

## 5. 迭代到通过闸门

`yanshi improve` 把一次性派发变成一个有界的**派发 → 闸门 → 精修**循环。`--check` 命令是
权威的闸门(退出码 `0` 表示通过);失败时,闸门输出被截断的末尾会被反馈进下一个 prompt:

```bash
yanshi improve --cli claude "fix the failing unit tests" \
  --check "uv run pytest -q" --max-iterations 3
```

它会打印一个 `ImproveResult`,其 `stop_reason` 是 `gate_passed`、`critic_threshold`、
`max_iterations`、`fatal_error` 或 `no_evaluator` 之一。完整细节见
[改进循环](../cli/improve-loop.md)。

## 接下来去哪

- [命令参考](../cli/reference.md)——每一个动词和选项。
- [监控](../concepts/monitoring.md)——状态对象保证了什么。
- [Python API](../library/python-api.md)——在 Python 中进行后台派发与扇出。
