English | [简体中文](QUICKSTART.zh-CN.md)

# YanShi Quickstart

> Last-Modified: 2026-06-25

Zero to your first monitored dispatch in a few minutes. Full documentation lives at
<https://yorha-agents.github.io/YanShi/>; this guide is the fast path.

YanShi dispatches a task to a headless agent CLI (`claude` / `codex` / `cursor` / `gemini`) and lets
you monitor it through a small, deterministic status object instead of raw logs.

## 1. Install

Global install via the bundled installer (no checkout needed):

```bash
curl -fsSL https://raw.githubusercontent.com/YoRHa-Agents/YanShi/main/install.sh | bash -s -- --global
```

Or, from a clone for local development:

```bash
git clone https://github.com/YoRHa-Agents/YanShi.git
cd YanShi
./install.sh --local --dev
```

The installer is `uv`-first with a `pip` + `venv` fallback. You can also install directly with
`uv tool install .`, `uv sync`, or `pip install .`.

The installer also **registers the skill** (`SKILL.md`) into your agent's skills home so a parent
agent can discover YanShi. If you installed directly (without `install.sh`), register it yourself:

```bash
yanshi skill register        # detect ~/.cursor/skills, ~/.claude/skills, ~/.agents/skills
```

## 2. Initialize a workspace config — `yanshi init`

YanShi reads an optional repo-level `.yanshi.toml` (discovered by walking up from the current
directory, like `.git`). Scaffold a commented starter file:

```bash
yanshi init                  # writes ./.yanshi.toml; refuses to overwrite without --force
```

Edit it to enable a subset of adapters, turn the advisory summarizer on, and define named profiles:

```toml
[adapters]
enabled = ["claude", "codex"]      # only these are registered and checked by doctor

[summarizer]
enabled = true                     # opt in to the ultra-light agent-CLI summary watcher
cli = "claude"
model = "claude-3-5-haiku-latest"  # cheap tier (haiku / flash / mini)

[profiles.cheap]
effort = "low"
cost_ceiling_usd = 0.5
timeout_s = 600

[profiles.thorough]
effort = "high"
timeout_s = 3600
```

Then verify the resolved layers and dispatch with a profile:

```bash
yanshi config                                        # resolved config + provenance (JSON)
yanshi dispatch --profile cheap "Explain this repo"  # applies [profiles.cheap]
```

`--profile` selects a `[profiles.<name>]` bundle; an unknown name is ignored with a warning, and
per-call flags still win over the profile and `[defaults]`.

## 3. Check your environment — `yanshi doctor`

```bash
yanshi doctor
```

Each registered adapter reports its executable, version, and authentication state as one JSON line:

```json
{"cli": "claude", "status": "ok", "executable": "/usr/local/bin/claude", "version": "…", "errors": [], "warnings": []}
```

`doctor` exits non-zero if any adapter is `failed`. YanShi **detects** vendor CLIs but never
installs or authenticates them for you — fix any `failed` adapter (install the binary, log in)
before dispatching to it.

## 4. Your first dispatch — `yanshi dispatch --wait`

```bash
yanshi dispatch --cli claude --effort high --wait \
  "Summarize the architecture of this repository"
```

CLI dispatch is **blocking**: it runs the monitor kernel inline until the agent reaches a terminal
state, then prints a `RunResult` JSON object (state, reply, usage, cost, `log_dir`, …) and exits
non-zero on error. Note: `--wait` is the default and the only supported CLI mode; `--no-wait` is
rejected — use the Python library for background dispatch.

Common options (shared with `improve`):

| Option | Meaning |
| --- | --- |
| `--cli` | Adapter: `claude` / `codex` / `cursor` / `gemini` (default `claude`). |
| `--model` | Model id to pass through to the CLI. |
| `--effort` | Reasoning effort: `low` / `medium` / `high` / `xhigh`. |
| `--allow` | Permission mode: `read-only` (default) or `yolo` (must be explicit). |
| `--workdir` | Child process working directory. |
| `--timeout` | Wall-clock timeout in seconds. |

## 5. Monitor with low context — `yanshi status` / `yanshi summary`

While a run is active (or after it, since reads are pure-disk), inspect it with two tiny pulls.
First, find the id:

```bash
yanshi list                  # -> ["ys-12345-...", ...]
```

Then pull the deterministic snapshot and the advisory narrative:

```bash
yanshi status  <agent_id>    # deterministic AgentStatus: state, counters, usage, cost, errors
yanshi summary <agent_id>    # advisory 1-3 sentence rolling summary
```

> **The one rule that matters:** poll only `status` and `summary`. Raw streams are retained under
> `$YANSHI_HOME/agents/<agent_id>/stream.ndjson` for audit/debugging and must **not** be pasted into
> the parent agent's context unless a human explicitly asks for raw logs.

## 6. Block or stop — `yanshi wait` / `yanshi cancel`

```bash
yanshi wait   <agent_id> --timeout 300    # block until terminal state (or timeout); prints AgentStatus
yanshi cancel <agent_id>                  # graceful signal -> SIGKILL, then finalize as cancelled
```

`wait` simply polls the on-disk `AgentStatus.state` until it is terminal or the timeout elapses; it
never re-parses the stream. When you are done with old runs, reclaim disk with
`yanshi gc --older-than 604800` (here, runs older than 7 days).

## 7. Iterate until a gate passes — `yanshi improve`

`improve` turns a single dispatch into a bounded **dispatch → gate → refine** loop:

```bash
yanshi improve --cli claude "fix the failing unit tests" \
  --check "uv run pytest -q" --max-iterations 3
```

- The **gate** (`--check`) is authoritative: exit code `0` means pass. The command is parsed with
  `shlex` and spawned argv-only (never through a shell).
- If the gate fails, only a **truncated tail** of its output is fed back into the next prompt —
  never the raw child stream — so context stays small.
- The loop is always bounded by `--max-iterations` (default `3`) and each gate is bounded by
  `--gate-timeout` (default `300`s). Add `--critic` to enable the advisory LLM critic.

It prints an `ImproveResult` whose `stop_reason` is one of `gate_passed`, `critic_threshold`,
`max_iterations`, `fatal_error`, or `no_evaluator`, and exits non-zero unless it succeeded.

## Next steps

- [README](./README.md) — overview, features, and the CLI cheat-sheet.
- [Skill contract](./skill/SKILL.md) — how a parent agent should drive YanShi.
- Full docs: [Installation](https://yorha-agents.github.io/YanShi/getting-started/installation/) ·
  [Architecture](https://yorha-agents.github.io/YanShi/concepts/architecture/) ·
  [CLI Reference](https://yorha-agents.github.io/YanShi/cli/reference/) ·
  [Python API](https://yorha-agents.github.io/YanShi/library/python-api/)
- Design spec (source of truth): [`.local/memory/specs/yanshi/spec.md`](./.local/memory/specs/yanshi/spec.md)
