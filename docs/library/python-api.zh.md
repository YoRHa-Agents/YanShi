# Python API

YanShi 首先是一个库;CLI 和 MCP shim 只是它之上的薄封装。在 Python 宿主中,你可以获得两个派发入口、纯磁盘读取器,以及扇出辅助函数——全部以 [pydantic](https://docs.pydantic.dev/) 契约进行类型标注。

## 两种派发方式

### 后台派发(入口 A)

在一个拥有事件循环的长生命周期宿主中,`dispatch_background` 会把监控内核作为一个 `asyncio.Task` spawn 出来,并把一个句柄交给你。内核会持续把状态镜像到磁盘,因此你可以在运行进行中轮询 `status` / `summary`,并 `await` 该任务以获取终态结果。

```python
import asyncio

from yanshi.contracts import RunSpec
from yanshi.dispatch import dispatch_background, status, summary


async def main() -> None:
    handle = dispatch_background(RunSpec(cli="claude", prompt="inspect this repo"))

    snap = status(handle.agent_id)          # pure disk read of AgentStatus
    print(snap.state, snap.usage.total)
    print(summary(handle.agent_id))         # advisory rolling summary

    result = await handle.task              # terminal RunResult
    print(result.state, result.usage.total, result.cost_usd)


asyncio.run(main())
```

`dispatch_background` 返回一个 `DispatchHandle(agent_id, task)`。

### 阻塞派发(入口 B)

当你只想把一个任务运行到完成时,`dispatch_wait` 会内联地运行同一个内核并返回 `RunResult`。状态仍会镜像到磁盘,因此另一个进程可以观察该运行。

```python
import asyncio

from yanshi.contracts import RunSpec
from yanshi.dispatch import dispatch_wait

result = asyncio.run(dispatch_wait(RunSpec(cli="claude", prompt="summarize this repo")))
print(result.state, result.reply)
```

!!! note "一个最小的同步辅助函数"
    `yanshi.dispatch.dispatch(spec)` 是一个小巧的*同步*阻塞调用,它会 spawn CLI 并解析结果,但**不**带监控内核或磁盘镜像。只要你需要磁盘上的状态、轮询或 `wait`,就优先使用 `dispatch_wait` /
    `dispatch_background`。

## 从磁盘轮询

`status`、`summary`、`wait` 和 `list_agents` 都是对 `$YANSHI_HOME` 的纯读取——没有子进程交互,也没有 LLM 调用。

```python
from yanshi.dispatch import list_agents, status, summary, wait

for agent_id in list_agents():
    print(agent_id, status(agent_id).state)

final = await wait(handle.agent_id, timeout_s=300)   # poll until terminal or timeout
print(summary(handle.agent_id))
```

## 高层契约

所有输入与输出都是 `yanshi.contracts` 中的类型化模型:

| 契约 | 作用 | 部分字段 |
|---|---|---|
| `RunSpec` | 派发所需的一切 | `cli`、`prompt`、`model`、`reasoning_effort`、`allow`、`workdir`、`add_dirs`、`env`、`timeout_s`、`session_mode`/`session_id`、`output_schema`、`cost_ceiling_usd` |
| `RunResult` | 终态结果 | `agent_id`、`cli`、`state`、`is_error`、`reply`、`structured_output`、`session_id`、`usage`、`cost_usd`、`pricing_status`、`exit_code`、`duration_ms`、`error_category`、`artifacts`、`log_dir`、`warnings` |
| `AgentStatus` | 上层 agent 拉取的紧凑对象 | `state`、`progress_pct`、`last_event`、`liveness`、`counters`、`usage`、`cost_usd`、`pricing_status`、`errors`、`warnings`、`rolling_summary`、`owner_pid`、`child_pid` |
| `Usage` | 归一化的 token | `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_tokens`,以及派生出的 `total` |

```python
from yanshi.contracts import RunSpec

spec = RunSpec(
    cli="claude",
    prompt="refactor the parser",
    model="sonnet",
    reasoning_effort="high",
    allow="read-only",
    workdir="/path/to/repo",
    timeout_s=600,
)
```

`RunSpec` 会校验其输入:空的 `cli`/`prompt`、非正的超时,以及非正的花费上限,都会在构造时被拒绝。

## 扇出:批量派发、聚合、合并

真正的价值来自以**故障隔离**的方式并行运行多个异构子 agent——一个 agent 失败绝不会中止其它 agent。

```python
import asyncio

from yanshi.contracts import RunSpec
from yanshi.fleet import consolidate, dispatch_many, fleet_status


async def main() -> None:
    specs = [
        RunSpec(cli="claude", prompt="audit module A"),
        RunSpec(cli="gemini", prompt="audit module B"),
    ]
    agent_ids = await dispatch_many(specs, max_parallel=4)

    fleet = fleet_status(agent_ids)         # deterministic, no LLM
    print(fleet.state_counts, fleet.total_usage.total, fleet.total_cost_usd)
    print(fleet.blockers)                   # fatal errors across the fleet

    merged = consolidate(agent_ids)         # {agent_ids, replies, errors, artifacts}
    print(merged["replies"])


asyncio.run(main())
```

- `dispatch_many(specs, *, max_parallel=4)` 在一个带有界 `Semaphore` 的 `asyncio.TaskGroup` 下运行内核,返回 `ys-fleet-…` 形式的 id,并为任何抛出异常的 agent 持久化一个失败结果。
- `fleet_status(agent_ids)` 返回一个 `FleetStatus`,它聚合状态计数、总用量/花费,以及致命阻塞项列表——确定性地完成,不使用任何模型。
- `consolidate(agent_ids)` 把终态结果合并成一个对上层 agent 友好的字典,包含 replies、errors 和 artifacts。上层 agent 读取合并后的观测,绝不读取子 agent 的原始流。

还提供一个透明的 `route(task, *, preferred_cli="claude")` 辅助函数;它返回所选的 CLI 与一个理由,绝不隐藏它的选择。

## 延伸阅读

- [架构](../concepts/architecture.md) —— 这些调用背后的内核。
- [改进循环](../cli/improve-loop.md) —— `improve_loop` 构建于此 API 之上。
- [MCP 与 Skill](../integration/mcp-and-skill.md) —— 把这些调用暴露给上层 agent。
- [配置](../reference/configuration.md) —— 状态与结果存储在何处。
