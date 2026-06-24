# MCP & Skill Integration

YanShi is meant to be driven by a *parent agent*. Two delivery surfaces make that ergonomic: a
`SKILL.md` contract that documents the dispatch/monitor verbs and policy arguments, and a tiny MCP
server shim that exposes those verbs as importable Python callables.

## The `SKILL.md` contract

`skill/SKILL.md` is the progressive-disclosure contract a host agent reads. Its core contract is:

1. Call `yanshi dispatch --wait <prompt>` for foreground CLI use, or the library
   `dispatch_background(spec)` from a long-lived host for background sub-agents.
2. Poll `yanshi status <agent_id>` for the deterministic fields (state, counters, usage, errors,
   warnings, last event, liveness).
3. Poll `yanshi summary <agent_id>` for the advisory rolling summary.
4. Use `yanshi wait <agent_id>` to block until a terminal state.
5. Use `yanshi cancel <agent_id>` to interrupt the child by its recorded pid.

Policy is passed at dispatch time so the *calling* agent controls it: `--cli`, `--model`, `--effort`
(`low|medium|high|xhigh`), `--allow` (defaults to `read-only`; `yolo` must be explicit), and
`--timeout`. Capability mismatches are surfaced as structured warnings — YanShi never pretends an
unsupported control worked.

## The MCP server wrappers

`skill/mcp_server.py` deliberately avoids a hard dependency on any one MCP framework. It exposes five
plain functions that a host binds to its own transport; each returns JSON-ready data:

| Function | Signature | Returns |
|---|---|---|
| `dispatch` | `dispatch(prompt, cli="claude")` | A JSON-ready `RunResult` dict (blocking). |
| `get_status` | `get_status(agent_id)` | A JSON-ready `AgentStatus` dict (pure disk read). |
| `get_summary` | `get_summary(agent_id)` | The advisory summary text (`str`). |
| `wait_for` | `wait_for(agent_id, timeout_s=None)` | A JSON-ready `AgentStatus` dict once terminal/timed out. |
| `cancel_agent` | `cancel_agent(agent_id)` | A JSON-ready `AgentStatus` dict after cancellation. |

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

Bind each function as an MCP tool in your host. The `install.sh --with-mcp` flag prints the wiring
hint and verifies that `skill/mcp_server.py` imports.

## How a parent agent integrates

- **Foreground / one-shot.** Call `dispatch(...)`; it blocks and returns the terminal `RunResult`
  dict (including `agent_id`), then inspect it directly.
- **Background sub-agents.** In a resident host, bind the library's `dispatch_background` (see the
  [Python API](../library/python-api.md)) so dispatch returns immediately with an `agent_id`; then
  expose `get_status` / `get_summary` / `wait_for` / `cancel_agent` for the parent to poll and steer.

In both cases the readers (`get_status`, `get_summary`, `wait_for`) are pure disk reads of
`$YANSHI_HOME`, so any number of host processes can observe a run cheaply.

## The low-context rule (non-negotiable)

!!! warning "Pull status and summary — never the raw stream"
    A parent agent must consume **only** `get_status` and `get_summary`. The raw event stream is
    retained at `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` for audit and debugging and **must
    not** be pasted into the parent's context unless a human explicitly asks for raw logs. This is
    what keeps multi-agent orchestration affordable.

## Related reading

- [Python API](../library/python-api.md) — the calls these wrappers delegate to.
- [CLI Reference](../cli/reference.md) — the equivalent command-line verbs.
- [Monitoring](../concepts/monitoring.md) — what the status object guarantees.
- [Installation](../getting-started/installation.md) — `--with-mcp` wiring.
