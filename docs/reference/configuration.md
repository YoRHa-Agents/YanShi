# Configuration

YanShi keeps all run state on disk under a single root and reads only a small, explicit set of
environment variables. There is no config file to manage: behavior is driven by `RunSpec`/policy at
dispatch time and by `$YANSHI_HOME` on disk.

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
