# MCP 与 Skill 集成

YanShi 旨在由一个*上层 agent* 驱动。两个交付面让这件事变得顺手:一个记录派发/监控动词与策略参数的 `SKILL.md` 契约,以及一个把这些动词暴露为可导入的 Python 可调用对象的小巧 MCP 服务器 shim。

## `SKILL.md` 契约

`skill/SKILL.md` 是宿主 agent 阅读的渐进式披露契约。它的核心契约是:

1. 对于前台 CLI 使用,调用 `yanshi dispatch --wait <prompt>`;对于后台子 agent,从长生命周期宿主调用库函数
   `dispatch_background(spec)`。
2. 轮询 `yanshi status <agent_id>` 以获取确定性字段(state、counters、usage、errors、warnings、最近事件、liveness)。
3. 轮询 `yanshi summary <agent_id>` 以获取建议性的滚动摘要。
4. 使用 `yanshi wait <agent_id>` 阻塞直到进入终态。
5. 使用 `yanshi cancel <agent_id>` 按记录的 pid 中断子进程。

策略在派发时传入,以便由*调用方* agent 控制:`--cli`、`--model`、`--effort`(`low|medium|high|xhigh`)、`--allow`(默认为 `read-only`;`yolo` 必须显式指定)以及 `--timeout`。能力不匹配会以结构化警告的形式暴露——YanShi 绝不假装一个不受支持的控制生效了。

## MCP 服务器封装

`skill/mcp_server.py` 刻意避免对任何单一 MCP 框架的硬依赖。它暴露五个普通函数,供宿主绑定到自己的传输层;每个函数都返回可直接 JSON 化的数据:

| 函数 | 签名 | 返回 |
|---|---|---|
| `dispatch` | `dispatch(prompt, cli="claude")` | 一个可 JSON 化的 `RunResult` 字典(阻塞)。 |
| `get_status` | `get_status(agent_id)` | 一个可 JSON 化的 `AgentStatus` 字典(纯磁盘读取)。 |
| `get_summary` | `get_summary(agent_id)` | 建议性的摘要文本(`str`)。 |
| `wait_for` | `wait_for(agent_id, timeout_s=None)` | 进入终态/超时后,一个可 JSON 化的 `AgentStatus` 字典。 |
| `cancel_agent` | `cancel_agent(agent_id)` | 取消后,一个可 JSON 化的 `AgentStatus` 字典。 |

```python
from skill.mcp_server import (
    cancel_agent,
    dispatch,
    get_status,
    get_summary,
    wait_for,
)

result = dispatch("inspect the failing tests", cli="claude")   # blocks, returns RunResult dict
agent_id = result["agent_id"]

snapshot = get_status(agent_id)        # AgentStatus dict
note = get_summary(agent_id)           # short advisory string
final = wait_for(agent_id, 300)        # AgentStatus dict
# cancel_agent(agent_id)               # if you need to abort
```

把每个函数都绑定为宿主中的一个 MCP 工具。`install.sh --with-mcp` 标志会打印接线提示,并验证 `skill/mcp_server.py` 可以导入。

## 上层 agent 如何集成

- **前台 / 一次性。** 调用 `dispatch(...)`;它会阻塞并返回终态的 `RunResult` 字典(其中包含 `agent_id`),然后直接检查它。
- **后台子 agent。** 在常驻宿主中,绑定库函数 `dispatch_background`(见 [Python API](../library/python-api.md)),使派发立即返回一个 `agent_id`;然后暴露 `get_status` / `get_summary` / `wait_for` / `cancel_agent`,供上层 agent 轮询和操控。

两种情况下,读取器(`get_status`、`get_summary`、`wait_for`)都是对 `$YANSHI_HOME` 的纯磁盘读取,因此任意数量的宿主进程都能以很低的成本观察一次运行。

## 低上下文规则(不可协商)

!!! warning "拉取 status 与 summary —— 绝不拉取原始流"
    上层 agent **只能**消费 `get_status` 和 `get_summary`。原始事件流被保留在
    `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` 以供审计和调试,**绝不**应被粘贴进上层 agent 的上下文,除非有人类明确索取原始日志。这正是让多 agent 编排负担得起的关键。

## 延伸阅读

- [Python API](../library/python-api.md) —— 这些封装所委托的调用。
- [CLI 参考](../cli/reference.md) —— 等价的命令行动词。
- [监控](../concepts/monitoring.md) —— status 对象所保证的内容。
- [安装](../getting-started/installation.md) —— `--with-mcp` 接线。
