# Quickstart

This walkthrough dispatches a sub-agent, then monitors it the YanShi way: by pulling a compact
status and a short summary, never by reading the raw child stream. It assumes you have already
[installed YanShi](installation.md) and at least one vendor CLI.

## 1. Check your adapters

Always start with `doctor`. It reports which adapters have a working executable and valid
authentication:

```bash
yanshi doctor
```

Each line is a JSON object; fix any adapter you intend to use before dispatching.

```text
{"cli": "claude", "status": "ok", "executable": "/usr/local/bin/claude", "version": "…", "errors": [], "warnings": []}
{"cli": "codex", "status": "failed", "executable": null, "version": null, "errors": ["missing CLI executable: codex"], "warnings": []}
```

## 2. Dispatch a task (blocking)

The CLI runs the shared monitor kernel inline and blocks until the child reaches a terminal state,
then prints a `RunResult`:

```bash
yanshi dispatch --cli claude --effort high "Summarize the architecture of this repo"
```

A successful run prints something like:

```json
{"agent_id": "…", "cli": "claude", "state": "succeeded", "is_error": false, "reply": "…", "usage": {"input_tokens": 1200, "output_tokens": 340, "...": 0}, "cost_usd": 0.01, "pricing_status": "native", "log_dir": "…/.yanshi/agents/…"}
```

!!! note "CLI dispatch is always `--wait`"
    `yanshi dispatch` is blocking by design (`--wait` is the default; `--no-wait` is rejected). For
    *background* sub-agents in a long-lived host, use the library's `dispatch_background` — see the
    [Python API](../library/python-api.md).

## 3. Observe with low context

While a run is in flight (from a second shell) or after it finishes, the run is recorded on disk.
List known agents, then pull the two — and only the two — low-context objects:

```bash
yanshi list                 # JSON array of known agent ids
yanshi status <agent_id>    # deterministic AgentStatus snapshot
yanshi summary <agent_id>   # advisory 1-3 sentence rolling summary
```

`status` returns the deterministic snapshot: `state`, `counters`, `usage`, `cost_usd`, `errors`,
`warnings`, `last_event`, and liveness. `summary` returns the advisory rolling summary string.

!!! warning "The low-context polling rule"
    A parent agent should consume **only** `status` and `summary`. The raw event stream is retained
    at `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` for audit and debugging, and **must not** be
    pasted into the parent's context unless a human explicitly asks for raw logs. This is the rule
    that keeps fleet orchestration cheap — see [Monitoring](../concepts/monitoring.md).

## 4. Wait and cancel

To block until a run reaches a terminal state (polling disk, not re-parsing the stream):

```bash
yanshi wait <agent_id> --timeout 300
```

To interrupt a run, YanShi signals the recorded child process (graceful interrupt, escalating to
`SIGKILL`) and finalizes the state as `cancelled`:

```bash
yanshi cancel <agent_id>
```

## 5. Iterate to a passing gate

`yanshi improve` turns a one-shot dispatch into a bounded **dispatch → gate → refine** loop. The
`--check` command is the authoritative gate (exit code `0` means pass); on failure, a truncated tail
of the gate output is fed back into the next prompt:

```bash
yanshi improve --cli claude "fix the failing unit tests" \
  --check "uv run pytest -q" --max-iterations 3
```

It prints an `ImproveResult` whose `stop_reason` is one of `gate_passed`, `critic_threshold`,
`max_iterations`, `fatal_error`, or `no_evaluator`. Full details are in
[Improve Loop](../cli/improve-loop.md).

## Where to next

- [CLI Reference](../cli/reference.md) — every verb and option.
- [Monitoring](../concepts/monitoring.md) — what the status object guarantees.
- [Python API](../library/python-api.md) — background dispatch and fan-out from Python.
