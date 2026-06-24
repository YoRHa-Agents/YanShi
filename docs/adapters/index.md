# Adapters

An **adapter** is the only vendor-specific code in YanShi. It translates a vendor-neutral `RunSpec`
into a CLI's argv, normalizes that CLI's event stream into `YanShiEvent`s, and parses a terminal
`RunResult`. Adding a new CLI means writing one adapter; nothing else in the kernel changes.

YanShi ships four adapters: `claude`, `codex`, `cursor`, and `gemini`.

## Per-CLI mapping

The table below is grounded in the shipped adapters. Prompts are delivered via stdin except for
Cursor, which takes the prompt as the final argument.

| Dimension | `claude` | `codex` | `cursor` | `gemini` |
|---|---|---|---|---|
| Executable | `claude` | `codex` | `cursor-agent` → `agent` | `gemini` |
| Prompt mode | stdin | stdin | argument | stdin |
| Headless base | `claude -p …` | `codex … exec … -` | `cursor-agent -p --trust …` | `gemini -p …` |
| Structured output flag | `--output-format stream-json --verbose` | `--json` | `--output-format stream-json` | `--output-format stream-json` |
| Model flag | `--model` | `--model` | `--model` (effort folded in) | `--model` |
| Effort translation | `--effort <level>` (flag) | `-c model_reasoning_effort="<level>"` (config) | folded into model name, e.g. `gpt-5.5-high` (model suffix) | `--model-thinking-level <level>` |
| Read-only permission | `--allowedTools Read,Grep,Glob,LS,WebFetch,WebSearch` | `--sandbox read-only --ask-for-approval never --search` | `--mode plan` | `--approval-mode plan` |
| Yolo permission | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | `--force` | `--approval-mode yolo` |
| Session resume | `--resume <id>` (new id via `--session-id`) | `exec resume <id>` | `--resume <id>` | `--resume <id>` (new id via `--session-id`) |
| Terminal event | `result` (`is_error`) | `turn.completed` / `turn.failed` | `result` (`is_error`) | `result` (+ exit `1` / `42` / `53`) |
| Event vocabulary | system / assistant / user / result / stream_event | thread.* / turn.* / item.* | system / assistant / tool_call / result | init / message / tool_use / result |

!!! note "Sandbox flags precede `exec` for Codex"
    Codex's permission flags are emitted *before* the `exec` subcommand, e.g.
    `codex --sandbox read-only --ask-for-approval never --search exec --json --skip-git-repo-check -`.

## Declared capabilities

Each adapter declares its capabilities (data-driven via a per-adapter TOML file). The dispatch policy
reads these **before** spawning to validate a `RunSpec` and emit downgrade warnings for anything the
CLI cannot express.

| Capability | `claude` | `codex` | `cursor` | `gemini` |
|---|---|---|---|---|
| `effort` mode | `flag` | `config` | `model_suffix` | `thinking_level` |
| `context_window_flag` | `false` | `false` | `false` | `false` |
| `session_resume` | `true` | `true` | `true` | `true` |
| `preassign_session_id` | `true` | `false` | `false` | `true` |
| `output_schema` | `true` | `true` | `false` | `true` |
| `stream_json` | `true` | `true` | `true` | `true` |
| `permission_modes` | read-only, yolo | read-only, yolo | read-only, yolo | read-only, yolo |

!!! note "Declared capability vs. wired flag"
    Capabilities describe what a CLI *can* express and drive preflight validation. The actual flag is
    emitted by the adapter's command builder — for example, `claude` wires `--json-schema` when an
    `output_schema` is supplied. No CLI exposes a context-window control, so a request that depends on
    one always produces a structured warning.

## The `cursor-agent` → `agent` fallback

Cursor's installer places both `cursor-agent` and a short `agent` alias (two symlinks to the same
binary). The Cursor adapter **must** resolve the executable in the order `cursor-agent` then
`agent`, using whichever exists, and **must not** hard-code a single name — otherwise preflight would
falsely report Cursor as missing on a machine that only installed one of the two.

## Effort vs. an explicit user model

Some CLIs (Cursor, and any CLI that folds reasoning effort into the model name) cannot express effort
as a separate flag. When a caller supplies **both** an explicit `model` and a `reasoning_effort`:

- The **explicit `model` wins.** YanShi must not rewrite a user-specified model.
- The effort that cannot be expressed is recorded as a structured warning
  (`cursor_effort_model_conflict`).

Only when the caller does **not** supply a model may the adapter synthesize a suffixed model name
from the effort (for Cursor, the base defaults to `gpt-5.5`, and the suffix is applied only to
`gpt-` models).

## Success determination is layered

Because vendors disagree on how to report failure, success is decided in layers:

1. **Process exit code** (Gemini is the richest: `0` ok, `1` generic, `42` auth, `53` server).
2. **Terminal event** flags (`result.is_error`, `turn.failed`).
3. **Error-string classification** of the remaining text into a governance category
   (`rate_limit`, `auth`, `billing`, `server_error`, …).

## Related reading

- [Safety & Policy](../concepts/safety.md) — what `read-only` and `yolo` inject per CLI.
- [Monitoring](../concepts/monitoring.md) — how normalized events drive the FSM.
- [Contributing](../contributing.md) — writing and testing a new adapter.
