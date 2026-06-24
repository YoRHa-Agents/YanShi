# Safety & Policy

YanShi does not provide worktree or container isolation. Instead, its safety model rests on three
pillars: an **explicit permission policy**, **faithful, strongly-constrained execution**, and
**no silent failures**. Policy is supplied by the caller (through the skill layer or `RunSpec`) and
enforced before and during every dispatch.

## Permission modes: `read-only` vs. `yolo`

The permission model is intentionally binary:

- **`read-only` (default)** — the adapter injects each vendor's least-privilege flags (for example
  Claude's `--allowedTools Read,Grep,Glob,LS,WebFetch,WebSearch`, Codex's `--sandbox read-only`,
  Cursor's `--mode plan`, Gemini's `--approval-mode plan`).
- **`yolo` (explicit only)** — only then are the dangerous vendor flags injected
  (`--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`, `--force`,
  `--approval-mode yolo`).

Policy validation enforces sensible invariants before spawn:

- A `RunSpec` whose `allow` mode is not in the adapter's declared `permission_modes` is rejected.
- A `read-only` dispatch may **not** request writable `add_dirs` — that combination is rejected.

!!! danger "`yolo` removes the vendor's safety rails"
    `yolo` is never implied. Request it explicitly and only for trusted work; it bypasses the
    vendor's approval and sandbox protections. The exact per-CLI flags are listed in
    [Adapters](../adapters/index.md).

## Writable boundaries and a filtered environment

The caller controls the working directory (`workdir`) and any extra writable directories
(`add_dirs`); these are resolved and validated (they must exist, and may be constrained to a trusted
root). The child process is spawned with a **filtered environment** — only an allowlist of variables
(such as `PATH`, `HOME`, `USER`, locale variables) plus any explicit `RunSpec.env` overrides are
passed through, rather than leaking the parent's entire environment.

## argv-only spawning, never `shell=True`

Every subprocess is spawned from an **argv list** with `shell=False` (or the async
`create_subprocess_exec` equivalent). The prompt is delivered via stdin or as a single argv value —
it is **never** interpolated into a shell command line. The same rule applies to the improve loop's
gate command, which is parsed with `shlex` and executed argv-only.

!!! note "Why this matters"
    Shell interpolation is the classic injection vector for agent prompts. Forbidding `shell=True`
    eliminates it structurally: a prompt containing `$(...)`, backticks, or `;` is just text to the
    child process.

## Cost ceilings and the missing-pricing fallback

The supervisor enforces a **per-run cost ceiling** (and a global ceiling at the fleet level). When a
run's accumulated `cost_usd` exceeds the ceiling, the supervisor escalates termination
`SIGINT → SIGTERM → SIGKILL` — the guard against a runaway loop quietly burning budget.

Cost can only be enforced in USD when pricing is known. The `UsageMeter` resolves cost in order:

1. **native** — the CLI reports its own cost.
2. **priced** — a cached/built-in pricing table matches the model.
3. **missing** — neither is available; `cost_usd` is `null`.

!!! warning "Degrading the cost guard when pricing is `missing`"
    When `pricing_status == missing`, a USD ceiling cannot be enforced reliably. YanShi **degrades**
    to a token-based ceiling and records a warning on the status making the degradation explicit. It
    **must not** pretend the USD ceiling is in force. See
    [Troubleshooting](../troubleshooting.md#cost-guard-degrades-when-pricing-is-missing).

## Secret redaction before disk and summarizer

Common secret shapes — `api_key`/`token`/`password`/`secret` assignments, `Bearer` tokens, and
`sk-…` keys — are redacted to `[REDACTED]` **before** raw lines are written to `stream.ndjson` and
**before** any text is handed to the summarizer. Secrets are kept out of both the visibility plane on
disk and the context plane the parent reads.

## Capability mismatches are surfaced, not faked

If a `RunSpec` requests something an adapter cannot express — a `reasoning_effort` on a CLI with no
effort control, an `output_schema` on a CLI without one, or any context-window control (no CLI
exposes it) — YanShi records a structured `WarningRecord` and downgrades. It never silently pretends
an unsupported control took effect.

## No silent failures

Per the project's governance, errors are **always** surfaced:

- Spawn/preflight failures raise categorized errors before any child runs.
- Runtime errors are appended to `AgentStatus.errors` with a category and the raw message.
- In the improve loop, gate/critic/dispatch failures appear in `GateOutcome.error`,
  `ImproveResult.warnings`, or a terminal `fatal_error` — never swallowed.

## Related reading

- [Adapters](../adapters/index.md) — the exact per-CLI permission flags.
- [Monitoring](monitoring.md) — how errors and warnings reach the status object.
- [Configuration](../reference/configuration.md) — `$YANSHI_HOME`, retention, and the pricing cache.
