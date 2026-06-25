# YanShi Sub-Agent Dispatch Skill

> Last-Modified: 2026-06-25

YanShi dispatches work to external agent CLIs (`claude`, `codex`, `cursor`, `gemini`) and lets
the parent agent monitor them through compact status objects. Parent agents must poll
`status`/`summary`; they must not read raw child streams into context.

## Core contract

1. Call `yanshi dispatch --wait <prompt>` for foreground CLI use, or the Python library
   `dispatch_background(spec)` from a long-lived host for background sub-agents.
2. Poll `yanshi status <agent_id>` for deterministic fields: state, counters, usage, errors,
   warnings, last event, and liveness.
3. Poll `yanshi summary <agent_id>` for the advisory rolling summary.
4. Use `yanshi wait <agent_id>` to block until a terminal state.
5. Use `yanshi cancel <agent_id>` to interrupt a child process by recorded pid.

## Policy arguments

- `--cli`: `claude`, `codex`, `cursor`, or `gemini`.
- `--model`: pass-through model id.
- `--effort`: `low|medium|high|xhigh` where the adapter can express it.
- `--allow`: defaults to `read-only`; `yolo` must be explicit.
- `--timeout`: wall-clock timeout in seconds.

Capability mismatches are surfaced as structured warnings; YanShi does not silently pretend an
unsupported control worked.

## Low-context monitoring rule

The parent agent should only consume:

```bash
yanshi status <agent_id>
yanshi summary <agent_id>
```

Raw streams are retained under `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` for audit/debugging
and must not be pasted into the parent context unless a human explicitly asks for raw logs.

## Iterative improve loop

`yanshi improve` turns a single-shot dispatch into a bounded **dispatch → evaluate → refine**
loop: it dispatches a sub-agent, runs a deterministic gate, and - if the gate fails - re-dispatches
with the failure fed back into the prompt, until the gate passes or `--max-iterations` is reached.

- The **gate** (`--check "<command>"`) is authoritative: exit code `0` means pass. The command is
  parsed into argv with `shlex` and spawned argv-only (never through a shell).
- The optional LLM **critic** (`--critic`) is advisory only and consulted just for the success
  decision when no gate is configured (mirrors the rolling summarizer).
- Low-context is preserved: only a truncated tail of the gate output (and any critic feedback)
  re-enters the next prompt - never the raw child stream.
- The loop is always bounded (`--max-iterations`, default 3) and the gate wait is bounded by
  `--gate-timeout` (default 300s).

```bash
yanshi improve --cli claude "fix the failing unit tests" \
  --check "uv run pytest -q" --max-iterations 3
```

Library entrypoint:

```python
from yanshi.contracts import ImproveSpec, RunSpec
from yanshi.improve import improve_loop

plan = ImproveSpec(
    spec=RunSpec(cli="claude", prompt="fix the failing unit tests"),
    check_command=["uv", "run", "pytest", "-q"],
    max_iterations=3,
)
result = await improve_loop(plan)  # -> ImproveResult(succeeded, stop_reason, iterations, ...)
```

`ImproveResult.stop_reason` is one of `gate_passed`, `critic_threshold`, `max_iterations`,
`fatal_error`, or `no_evaluator`. Gate/critic/dispatch failures are surfaced in
`GateOutcome.error` and `ImproveResult.warnings` (never silently swallowed).

## Repo-level configuration

Callers can scaffold a workspace config so different repos expose different defaults
and capabilities. `yanshi init` writes a starter config; `--local` (default) creates
`.yanshi.toml` in the current directory, `--global` creates `$YANSHI_HOME/config.toml`.
It refuses to overwrite an existing file without `--force`.

```bash
yanshi init            # scaffold ./.yanshi.toml
yanshi init --global   # scaffold $YANSHI_HOME/config.toml
yanshi config          # print the resolved config + per-value provenance
```

`dispatch` and `improve` resolve a final spec from layered config before running,
with deterministic precedence (low→high): built-in defaults < global config <
local `.yanshi.toml` (discovered by walking up from `cwd`) < per-call flags. The
config honors:

- `[adapters].enabled` — subset of `{claude, codex, cursor, gemini}` that may be
  registered and `doctor`-checked; requesting a disabled/unknown CLI fails fast.
- `[defaults]` and `[profiles.<name>]` — invocation defaults and named presets;
  select a preset with `--profile <name>` on `dispatch`/`improve`. Explicit flags
  always win over config; config never overrides a model you passed yourself.
- `[limits]` — hard ceilings (`max_allow`, `max_cost_usd`, `max_timeout_s`) clamped
  last; every actual clamp emits a structured warning (no silent tightening).
- `[summarizer]` — opt-in (`enabled = false` by default). When on, the rolling
  summary is produced by one ultra-light agent-CLI call; any error/timeout/budget
  exhaustion degrades to the deterministic fallback without slowing the main run.

`yanshi config` replays the effective values and shows which layer each came from
(built-in / global / local / override). Over MCP, use `get_config()` to inspect the
resolved config and pass an optional `profile` to `dispatch`.

## Examples

```bash
yanshi init --local
yanshi config
yanshi doctor
yanshi dispatch --cli claude --model sonnet --effort high "Inspect failing tests"
yanshi dispatch --profile cheap "Summarize the diff"
yanshi improve --cli claude "fix failing tests" --check "pytest -q" --max-iterations 3
yanshi list
yanshi status ys-...
yanshi summary ys-...
yanshi wait ys-... --timeout 300
yanshi cancel ys-...
```
