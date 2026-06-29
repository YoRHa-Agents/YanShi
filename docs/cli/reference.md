# CLI Reference

The `yanshi` command is a thin wrapper over the library. Every verb prints machine-readable output
(JSON, except `summary`, which prints the summary text) and uses exit codes to signal failure, so it
composes cleanly in scripts and orchestrators.

!!! note "Conventions"
    - `AGENT_ID` is the id printed in a `RunResult` / `AgentStatus` (also discoverable via `list`).
    - `status`, `summary`, `wait`, and `list` are **pure disk reads** of `$YANSHI_HOME`.
    - Reading agents means pulling `status` + `summary` only — never the raw stream
      (see [Monitoring](../concepts/monitoring.md)).

## doctor

Check every registered adapter's executable and authentication state.

```text
yanshi doctor
```

Prints one JSON object per adapter (`cli`, `status`, `executable`, `version`, `errors`, `warnings`)
and exits non-zero if **any** adapter fails preflight.

```bash
yanshi doctor
```

## init

Write a commented starter config (the template documented in
[Configuration](../reference/configuration.md)). Writes the local `./.yanshi.toml` by default, or the
global `$YANSHI_HOME/config.toml` with `--global`.

```text
yanshi init [--global | --local] [--force]
```

| Option | Default | Description |
|---|---|---|
| `--global` / `--local` | `--local` | Write the global `$YANSHI_HOME/config.toml` instead of `./.yanshi.toml`. |
| `--force` | off | Overwrite an existing config file. |

**Refuses to overwrite** an existing target unless `--force` is passed: it prints
`refusing to overwrite existing config: <path> (use --force)` to stderr and exits `1`. On success it
writes the template and prints the resolved path to stdout.

```bash
yanshi init                 # write ./.yanshi.toml
yanshi init --global        # write $YANSHI_HOME/config.toml
yanshi init --force         # overwrite an existing file
```

## skill register

Register the YanShi skill (`SKILL.md`) into agent skills homes so a parent agent (Cursor / Claude /
...) can discover YanShi as a skill. This is what makes the dispatch verbs usable from an agent — the
installer runs it automatically, and you can re-run it any time.

```text
yanshi skill register [--skills-dir DIR] [--dry-run] [--best-effort]
```

| Option | Default | Description |
|---|---|---|
| `--skills-dir` | auto-detect | Skills home to register into. When omitted, detect installed homes (`~/.cursor/skills`, `~/.claude/skills`, `~/.agents/skills`). |
| `--dry-run` | off | Plan the registration (report the source and target homes) without touching disk. |
| `--best-effort` | off | Do not exit non-zero when no agent skills home is found (used by the installer). |

Prints a JSON report (`skill`, `source`, `files`, `targets`, `registered`, `dry_run`). Exits `1` when
the source `SKILL.md` cannot be located or a copy fails, and (unless `--best-effort`) when nothing was
registered. Registration is idempotent — an existing registration is overwritten in place.

```bash
yanshi skill register                          # register into detected agent homes
yanshi skill register --skills-dir ~/.cursor/skills
yanshi skill register --dry-run                # preview targets without writing
```

## config

Print the effective layered configuration and its provenance as JSON — built-in defaults < global
`$YANSHI_HOME/config.toml` < local `./.yanshi.toml`. Useful for answering "where did this default come
from?".

```text
yanshi config
```

The JSON has four keys: `config` (the fully resolved document), `sources` (the files that contributed,
low → high), `provenance` (each top-level section mapped to `builtin` or the file that last set it),
and `enabled_adapters` (the `[adapters].enabled` list, or `null` for all). See
[Configuration](../reference/configuration.md) for the schema and precedence rules.

```bash
yanshi config
```

## dispatch

Run a single blocking dispatch through the monitor kernel and print the terminal `RunResult`.

```text
yanshi dispatch [OPTIONS] [PROMPT]
```

| Option | Default | Description |
|---|---|---|
| `PROMPT` | `""` | Positional prompt passed to the agent CLI (sent via stdin). |
| `--cli` | `claude` | Adapter name: `claude`, `codex`, `cursor`, or `gemini`. |
| `--model` | — | Model id passed through to the adapter. |
| `--effort` | — | Reasoning effort: `low`, `medium`, `high`, or `xhigh`. |
| `--allow` | `read-only` | Permission mode: `read-only` or `yolo`. |
| `--profile` | — | Named config profile to apply (`[profiles.NAME]`); an unknown name warns and is ignored. |
| `--workdir` | — | Child process working directory. |
| `--timeout` | — | Wall-clock timeout in seconds. |
| `--wait` / `--no-wait` | `--wait` | CLI dispatch is blocking; `--no-wait` is rejected (use the library for background runs). |

Exits `1` when the result is an error, and `2` for an invalid invocation (for example `--no-wait`
or an invalid `--effort`).

Unset flags are filled from the resolved config: `[defaults]` first, then any `--profile` preset, then
your explicit flags, with `[limits]` clamped last. Resolution warnings — an unknown `--profile`, or a
clamped capability — are printed to **stderr** as JSON `WarningRecord`s and never stop the run. See
[Configuration](../reference/configuration.md).

```bash
yanshi dispatch --cli claude --model sonnet --effort high "Inspect the failing tests"
```

## improve

Run a bounded **dispatch → gate → refine** loop and print the `ImproveResult`. See
[Improve Loop](improve-loop.md) for the full model.

```text
yanshi improve [OPTIONS] [PROMPT]
```

| Option | Default | Description |
|---|---|---|
| `PROMPT` | `""` | Task prompt to iterate on. |
| `--cli` | `claude` | Adapter name. |
| `--model` | — | Model id passed through. |
| `--effort` | — | Reasoning effort: `low`, `medium`, `high`, `xhigh`. |
| `--allow` | `read-only` | Permission mode. |
| `--profile` | — | Named config profile to apply (`[profiles.NAME]`); an unknown name warns and is ignored. |
| `--workdir` | — | Child process working directory. |
| `--timeout` | — | Per-dispatch wall-clock timeout seconds. |
| `--check` | — | Deterministic gate command (exit `0` = pass). Parsed with `shlex`, run argv-only. |
| `--max-iterations` | `3` | Maximum dispatch → gate → refine cycles (must be ≥ 1). |
| `--gate-timeout` | `300` | Gate command timeout seconds. |
| `--critic` / `--no-critic` | `--no-critic` | Enable the advisory LLM critic. |

Exits `1` when the loop did not succeed, and `2` when `--max-iterations` is less than 1.

`--profile` and the config-driven default/limit resolution work exactly as for
[`dispatch`](#dispatch); clamp and unknown-profile warnings go to **stderr**.

```bash
yanshi improve --cli claude "fix failing tests" --check "uv run pytest -q" --max-iterations 3
```

## list

List known agent ids deterministically.

```text
yanshi list
```

```bash
yanshi list
```

## status

Read a deterministic `AgentStatus` snapshot from disk.

```text
yanshi status AGENT_ID
```

```bash
yanshi status ys-12345-1700000000000000000
```

## summary

Read the advisory rolling summary from disk (falls back to the last event's summary when no rolling
summary exists yet).

```text
yanshi summary AGENT_ID
```

```bash
yanshi summary ys-12345-1700000000000000000
```

## wait

Poll disk status until the agent reaches a terminal state or the timeout elapses, then print the
`AgentStatus`.

```text
yanshi wait AGENT_ID [--timeout SECONDS]
```

| Option | Default | Description |
|---|---|---|
| `--timeout` | — | Maximum seconds to wait (omit to wait indefinitely). |

```bash
yanshi wait ys-12345-1700000000000000000 --timeout 300
```

## cancel

Cancel a run: signal the recorded child process (and cancel the in-process task when present), then
finalize the state as `cancelled`.

```text
yanshi cancel AGENT_ID
```

```bash
yanshi cancel ys-12345-1700000000000000000
```

## gc

Garbage-collect terminal run directories older than a threshold, returning the list of removed agent
ids. Only terminal runs are removed.

```text
yanshi gc [--older-than SECONDS]
```

| Option | Default | Description |
|---|---|---|
| `--older-than` | `86400` | Age threshold in seconds (default 1 day). |

```bash
yanshi gc --older-than 604800   # remove terminal runs older than 7 days
```

## record

A maintenance helper for adapter development: run a CLI once and copy its retained raw stream into a
fixture file (used to build offline parser tests).

```text
yanshi record [OPTIONS] [PROMPT]
```

| Option | Default | Description |
|---|---|---|
| `PROMPT` | `hello` | Prompt to record. |
| `--cli` | `claude` | Adapter name. |
| `--output` | `tests/fixtures/recorded.ndjson` | Destination fixture path. |

```bash
yanshi record --cli claude "hello" --output tests/fixtures/claude_hello.ndjson
```

## See also

- [Improve Loop](improve-loop.md) — the iterative loop in depth.
- [Adapters](../adapters/index.md) — how `--cli`, `--model`, `--effort`, and `--allow` map to vendor flags.
- [Configuration](../reference/configuration.md) — what `gc`, `status`, and `wait` read on disk.
