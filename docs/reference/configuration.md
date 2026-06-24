# Configuration

YanShi keeps all run state on disk under a single root and reads only a small, explicit set of
environment variables. Runtime behavior is driven by `RunSpec`/policy at dispatch time and by
`$YANSHI_HOME` on disk; in addition, an **optional** repo-level `.yanshi.toml` (below) declares the
enabled adapters, summarizer, dispatch defaults, profiles, and limits for a workspace. It only shapes
what is resolved **before** dispatch and never changes a `RunSpec` contract or a runtime invariant.

## Repo-level configuration (`.yanshi.toml`)

A workspace can ship an optional TOML config so that different repositories on the same machine get
different capabilities: which adapters are enabled, how the summarizer runs, and the default/limit
envelope for every dispatch. If no config is found, built-in defaults apply and YanShi behaves exactly
as it does with no file at all.

### Discovery

Two config layers share one **identical** schema:

- **Local (workspace)** — `.yanshi.toml`, discovered by walking **up** from the current working
  directory to the filesystem root and taking the **first** `.yanshi.toml` found (the same way Git
  discovers `.git`). The nearest file wins; if none is found there is no local layer.
- **Global** — `$YANSHI_HOME/config.toml` (default `~/.yanshi/config.toml`, alongside the on-disk
  state root described below).

Both files are TOML, and every section forbids unknown keys. A malformed file — bad syntax, a wrong
type, or an unknown key — raises a clear error naming the offending path; it is **never silently
ignored**.

### Precedence

The effective configuration is composed by deep-merging the layers per section, lowest to highest:

```text
built-in defaults
  └< global   $YANSHI_HOME/config.toml
       └< local   ./.yanshi.toml         (nearest walk-up hit)
            └< --profile <name>          (a [profiles.<name>] preset)
                 └< per-call flags        (CLI options / RunSpec fields)   ← highest
```

Within a section a higher layer overrides a lower layer key-by-key, and `[profiles.*]` merge by name.
The file layers (built-in/global/local) merge whole sections; `--profile` and per-call flags then
resolve the individual `[defaults]` fields at dispatch time. `[limits]` is applied **last** — after
everything above resolves, each requested value is clamped to its cap (see the `[limits]` clamping
rules below).

### Example `.yanshi.toml`

`yanshi init` writes this commented starter verbatim. Every key is optional; omit a key to keep the
built-in default.

```toml
# YanShi repository configuration (.yanshi.toml)
# Every value below is optional; omitted keys fall back to builtin defaults.

[adapters]
# Restrict which agent CLIs YanShi may dispatch to. Remove this key (or the
# whole section) to leave every installed adapter enabled. Names are validated
# against the real adapter registry at dispatch time.
enabled = ["claude", "codex", "cursor", "gemini"]

[summarizer]
# Advisory rolling summaries are OFF by default and never alter status fields.
enabled = false
# CLI used to produce summaries when enabled.
cli = "claude"
# Model for the summarizer CLI; omit to use the CLI's own default.
model = "claude-3-5-haiku-latest"
# Minimum seconds between summary refreshes (debounce).
debounce_s = 5.0
# Minimum number of new significant events before re-summarizing.
min_new_events = 2
# Hard cap on summary length, in tokens.
max_tokens = 150
# Total watcher token budget before falling back to deterministic text.
watcher_token_ceiling = 1000
# Per-summary CLI timeout, in seconds.
timeout_s = 60

[defaults]
# Default reasoning effort for every dispatch: low | medium | high | xhigh.
effort = "medium"
# Default permission model for every dispatch: read-only | yolo.
allow = "read-only"
# Default overall timeout per dispatch, in seconds.
timeout_s = 1800
# Default stall (no-progress) timeout per dispatch, in seconds.
stall_timeout_s = 300
# Optionally pin a default CLI / model / cost ceiling for every dispatch.
# cli = "claude"
# model = "claude-3-7-sonnet-latest"
# cost_ceiling_usd = 5.0

[limits]
# Hard caps enforced on every dispatch regardless of profile or per-call
# overrides. Uncomment to activate; requests above a cap are clamped + warned.
# max_allow = "read-only"
# max_cost_usd = 10.0
# max_timeout_s = 3600

[profiles.cheap]
# A fast, low-cost profile: minimal effort and tight budgets.
effort = "low"
cost_ceiling_usd = 0.5
timeout_s = 600

[profiles.thorough]
# A high-effort profile for hard, long-running tasks.
effort = "high"
timeout_s = 3600
stall_timeout_s = 600
```

### What each section does

| Section | Purpose |
|---|---|
| `[adapters]` | `enabled` is the subset of `claude`, `codex`, `cursor`, and `gemini` that may be dispatched to — and the only adapters `doctor` checks. Omit the key (or the whole section) to leave every installed adapter enabled. Requesting a disabled or unknown adapter fails fast. |
| `[summarizer]` | Settings for the advisory rolling summarizer, which runs as a single lightweight one-shot agent-CLI call. Off by default (`enabled = false`), in which case summaries stay on the deterministic fallback. See [Monitoring](../concepts/monitoring.md). |
| `[defaults]` | The lowest-precedence dispatch values applied to every call: `cli`, `model`, `effort` (mapped to `RunSpec.reasoning_effort`), `allow`, `timeout_s`, `stall_timeout_s`, and `cost_ceiling_usd`. |
| `[profiles.<name>]` | A named preset with the exact same shape as `[defaults]`, selected per call with `--profile <name>`. |
| `[limits]` | Hard caps clamped onto every dispatch regardless of profile or per-call overrides: `max_allow`, `max_cost_usd`, and `max_timeout_s`. |

**Summarizer fields:** `enabled` (default `false`); `cli` and `model` (the agent CLI used to write the
summary — `cli` must be in `[adapters].enabled`); `debounce_s` and `min_new_events` (how often a refresh
may trigger); `max_tokens` (summary length cap); `watcher_token_ceiling` (total token budget for the
summarizer); and `timeout_s` (per-summary wall-clock timeout). Any error or exhausted budget degrades
to the deterministic fallback without blocking the monitored run.

### How `[limits]` clamps (always warns)

`[limits]` is the final gate, applied after `[defaults]`, the selected profile, and per-call flags
resolve:

- `max_allow` caps `allow` (ranked `read-only` < `yolo`): a `yolo` request under
  `max_allow = "read-only"` is clamped back to `read-only`.
- `max_cost_usd` caps `cost_ceiling_usd`.
- `max_timeout_s` caps `timeout_s`.

Whenever a value is **actually** clamped, YanShi appends a structured `capability_clamped`
`WarningRecord` (with `code`, `message`, and `detail`) — clamping is never silent (No Silent Failures).
The CLI prints these warnings to **stderr** as JSON. If a `max_cost_usd`/`max_timeout_s` cap is set
while the corresponding value is unset, the cap is simply adopted as the effective value (nothing was
downgraded, so no warning is emitted).

### Inspecting the resolved config (`yanshi config`)

`yanshi config` prints the merged configuration as JSON, with provenance so you can trace where each
section came from:

```json
{
  "config": { "...": "the fully resolved document" },
  "sources": ["/home/you/.yanshi/config.toml", "/path/to/repo/.yanshi.toml"],
  "provenance": {
    "adapters": "builtin",
    "summarizer": "/path/to/repo/.yanshi.toml",
    "defaults": "/home/you/.yanshi/config.toml",
    "limits": "builtin",
    "profiles": "/path/to/repo/.yanshi.toml"
  },
  "enabled_adapters": ["claude", "codex", "cursor", "gemini"]
}
```

- `sources` lists the files that contributed, in precedence order (low → high).
- `provenance` maps each top-level section to the layer that last set it: `builtin`, or the path of the
  global/local file.
- `enabled_adapters` echoes `[adapters].enabled` (`null` means every adapter is enabled).

See [`yanshi init`](../cli/reference.md#init) and [`yanshi config`](../cli/reference.md#config) in the
CLI reference.

## `$YANSHI_HOME`

All persistent state lives under `$YANSHI_HOME`, which defaults to `~/.yanshi`. Set it to relocate
run records, raw streams, and caches:

```bash
export YANSHI_HOME="$HOME/.local/state/yanshi"
```

## On-disk layout

```text
$YANSHI_HOME/                     # default ~/.yanshi
├── agents/
│   └── <agent_id>/
│       ├── run.json              # run record + AgentStatus snapshot (atomic write, mode 0600)
│       ├── run.lock              # file lock guarding the atomic write
│       ├── stream.ndjson         # raw event stream (ring-buffered, secret-redacted)
│       └── result.json           # terminal RunResult
├── sessions.json                 # alias -> native session id map
└── pricing-cache.json            # cached model pricing
```

- **`run.json`** holds the live run record and the deterministic `AgentStatus` snapshot, mirrored to
  disk as the run progresses. Writes are **atomic** (temp file + rename, guarded by a file lock) and
  created with mode `0600`.
- **`run.lock`** is the per-record lock that serializes those atomic writes.
- **`stream.ndjson`** is the raw event stream (the visibility plane). It is written through a bounded
  ring buffer (default 8 MiB) and is secret-redacted before it ever touches disk. When the window is
  exceeded the oldest bytes are dropped — the truncation is **counted, not silent**.
- **`result.json`** is the terminal `RunResult`, written once the run finishes.
- **`sessions.json`** maps friendly aliases to native CLI session ids (for resume).
- **`pricing-cache.json`** caches model pricing used by the cost meter.

!!! note "Readers are pure disk reads"
    `status`, `summary`, `wait`, `list`, and `fleet_status` only read this tree — no subprocess
    interaction and no LLM calls. See [Architecture](../concepts/architecture.md).

## Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `YANSHI_HOME` | Store | Root for all run state (default `~/.yanshi`). |
| `YANSHI_LIVE` | Tests | Gate for live tests that spawn real CLIs (see [Contributing](../contributing.md)). |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | Claude preflight | Either satisfies Claude's auth check. |
| `CLAUDE_CONFIG_DIR` | Claude preflight | Directory checked for `.credentials.json` / `auth.json`. |
| `LANG` | `install.sh` | Infers installer message language when `--lang` is omitted. |

### The child environment is filtered

Child CLIs are **not** handed the parent's full environment. Only an allowlist is passed through —
`PATH`, `HOME`, `USER`, `USERPROFILE`, `TMPDIR`, `TEMP`, `TMP`, `LANG`, `LC_ALL`, and the Windows
equivalents — plus any explicit `RunSpec.env` overrides the caller supplies. This keeps stray
credentials and configuration out of dispatched processes.

## Pricing and cost provenance

The cost meter resolves a run's `cost_usd` in order: a CLI-reported **native** cost, otherwise a
**priced** estimate from a model pricing table, otherwise **missing** (cost is `null`). The table
combines a small built-in default with any entries loaded from `pricing-cache.json`, where each entry
maps a model prefix to `[input_per_million, output_per_million]` USD. When pricing is `missing`, the
USD cost ceiling degrades to a token ceiling — see
[Safety & Policy](../concepts/safety.md) and
[Troubleshooting](../troubleshooting.md#cost-guard-degrades-when-pricing-is-missing).

## Retention and garbage collection

Terminal run directories are retained until you collect them. `yanshi gc` removes terminal runs whose
record is older than a threshold (default one day) and returns the removed ids:

```bash
yanshi gc --older-than 604800     # remove terminal runs older than 7 days
```

Only terminal runs are eligible; an active run is never garbage-collected. See the
[CLI Reference](../cli/reference.md#gc).
