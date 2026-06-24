# Python API

YanShi is a library first; the CLI and MCP shim are thin wrappers over it. From a Python host you get
the two dispatch entrypoints, the pure-disk readers, and the fan-out helpers — all typed with
[pydantic](https://docs.pydantic.dev/) contracts.

## Two ways to dispatch

### Background dispatch (Entrypoint A)

In a long-lived host that owns an event loop, `dispatch_background` spawns the monitor kernel as an
`asyncio.Task` and hands you a handle. The kernel mirrors status to disk continuously, so you can
poll `status` / `summary` while the run is in flight and `await` the task for the terminal result.

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

`dispatch_background` returns a `DispatchHandle(agent_id, task)`.

### Blocking dispatch (Entrypoint B)

When you just want to run one task to completion, `dispatch_wait` runs the same kernel inline and
returns the `RunResult`. Status is still mirrored to disk, so another process can observe the run.

```python
import asyncio

from yanshi.contracts import RunSpec
from yanshi.dispatch import dispatch_wait

result = asyncio.run(dispatch_wait(RunSpec(cli="claude", prompt="summarize this repo")))
print(result.state, result.reply)
```

!!! note "A minimal synchronous helper"
    `yanshi.dispatch.dispatch(spec)` is a small *synchronous* blocking call that spawns the CLI and
    parses the result **without** the monitor kernel or disk mirroring. Prefer `dispatch_wait` /
    `dispatch_background` whenever you want on-disk status, polling, or `wait`.

## Polling from disk

`status`, `summary`, `wait`, and `list_agents` are pure reads of `$YANSHI_HOME` — no subprocess
interaction, no LLM calls.

```python
from yanshi.dispatch import list_agents, status, summary, wait

for agent_id in list_agents():
    print(agent_id, status(agent_id).state)

final = await wait(handle.agent_id, timeout_s=300)   # poll until terminal or timeout
print(summary(handle.agent_id))
```

## High-level contracts

All inputs and outputs are typed models in `yanshi.contracts`:

| Contract | Role | Selected fields |
|---|---|---|
| `RunSpec` | Everything needed to dispatch | `cli`, `prompt`, `model`, `reasoning_effort`, `allow`, `workdir`, `add_dirs`, `env`, `timeout_s`, `session_mode`/`session_id`, `output_schema`, `cost_ceiling_usd` |
| `RunResult` | Terminal result | `agent_id`, `cli`, `state`, `is_error`, `reply`, `structured_output`, `session_id`, `usage`, `cost_usd`, `pricing_status`, `exit_code`, `duration_ms`, `error_category`, `artifacts`, `log_dir`, `warnings` |
| `AgentStatus` | The compact object parents pull | `state`, `progress_pct`, `last_event`, `liveness`, `counters`, `usage`, `cost_usd`, `pricing_status`, `errors`, `warnings`, `rolling_summary`, `owner_pid`, `child_pid` |
| `Usage` | Normalized tokens | `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, and the derived `total` |

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

`RunSpec` validates its inputs: empty `cli`/`prompt`, non-positive timeouts, and a non-positive cost
ceiling are rejected at construction time.

## Fan-out: dispatch many, aggregate, consolidate

Real value comes from running several heterogeneous sub-agents in parallel with **failure
isolation** — one agent failing never aborts the others.

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

- `dispatch_many(specs, *, max_parallel=4)` runs the kernel under an `asyncio.TaskGroup` with a
  bounded `Semaphore`, returns `ys-fleet-…` ids, and persists a failed result for any agent that
  raised.
- `fleet_status(agent_ids)` returns a `FleetStatus` aggregating state counts, total usage/cost, and
  the list of fatal blockers — deterministically, with no model.
- `consolidate(agent_ids)` merges terminal results into a parent-friendly dict of replies, errors,
  and artifacts. The parent reads the merged observation, never the children's raw streams.

A transparent `route(task, *, preferred_cli="claude")` helper is also available; it returns the
chosen CLI and a reason and never hides its selection.

## Related reading

- [Architecture](../concepts/architecture.md) — the kernel behind these calls.
- [Improve Loop](../cli/improve-loop.md) — `improve_loop` builds on this API.
- [MCP & Skill](../integration/mcp-and-skill.md) — exposing these calls to a parent agent.
- [Configuration](../reference/configuration.md) — where status and results are stored.
