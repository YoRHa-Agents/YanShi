# Troubleshooting

Most problems fall into one of a few buckets: an adapter that isn't ready, a vendor error that needs
classifying, or a run that looks stuck. YanShi is deterministic about all three, so the status object
usually tells you exactly what happened.

## Start with `yanshi doctor`

`doctor` runs each adapter's preflight and prints one JSON line per CLI:

```bash
yanshi doctor
```

```text
{"cli": "claude", "status": "ok", "executable": "/usr/local/bin/claude", "version": "…", "errors": [], "warnings": []}
{"cli": "codex", "status": "failed", "executable": null, "version": null, "errors": ["missing CLI executable: codex"], "warnings": []}
```

It exits non-zero if any adapter fails. A failure here is informational — other adapters still work.

## Preflight failures

Preflight runs **before** any child is spawned and fails fast, so a misconfigured CLI never produces
a half-run.

- **Missing binary** — `missing CLI executable: <cli>`. The executable isn't on `PATH`. Install the
  vendor CLI (YanShi does not install it) and re-run `doctor`. Remember Cursor resolves
  `cursor-agent` first and then the `agent` alias — installing either is enough.
- **Authentication** — for example `claude authentication seed not found`. Provide credentials via
  `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`, or a `CLAUDE_CONFIG_DIR` (or `~/.claude*`)
  containing `.credentials.json` / `auth.json`. Auth failures are categorized as `auth`.
- **Version not detected** — a non-fatal warning (`could not detect version for <cli>`); dispatch
  still proceeds.

See [Installation](getting-started/installation.md) for setup details.

## Error categories

When a run fails, `error_category` (and each `errors[].category`) classifies vendor error text into a
governance category:

| Category | Typical triggers | Retryable? |
|---|---|---|
| `rate_limit` | `rate limit`, `429`, `too many requests` | yes |
| `overloaded` | `overloaded`, `capacity`, `busy` | yes |
| `server_error` | `server error`, `5xx`, `500`–`504` | yes |
| `auth` | `unauthorized`, `not logged in`, `login` | no |
| `billing` | `billing`, `quota`, `payment`, `credit` | no |
| `invalid_request` | `invalid request`, `bad request`, `schema` | no |
| `max_output_tokens` | `max output`, `output tokens` | no |
| `unknown` | anything unclassified (raw message preserved) | no |

The supervisor only retries the retryable categories, with bounded exponential backoff; non-retryable
categories fail fast. The raw message is always preserved alongside the category — classification
never discards information.

## `stalled` vs. `waiting_*`

A run that isn't producing output is not necessarily stuck. The supervisor distinguishes three cases:

- **`waiting_rate_limit`** — the child is intentionally paused on a rate limit. The supervisor
  **waits**; it does not kill it.
- **`waiting_tool`** — a tool call is in progress. The supervisor waits up to a long-tool timeout
  (≈900s) before treating it as stuck.
- **`stalled`** — no output past the stall timeout (≈300s), so the supervisor interrupts; or the run
  was corrected to `stalled` because its monitoring host died (below). A `wall_timeout` (≈1800s)
  also triggers termination.

If you see a premature `stalled`, the child likely produced no parseable events for the stall window;
raise the relevant timeout on the `RunSpec` or inspect `stream.ndjson` for the silent period.

## Stale running corrected to stalled

Each run records an `owner_pid` (the monitoring host) and a `child_pid`. If a reader observes a
non-terminal `running` state but the `owner_pid` is **no longer alive**, it deterministically
rewrites the state to `stalled` and appends a fatal error explaining that the owner pid is gone. This
is how an orphaned child (left behind when its monitoring host crashed) is surfaced honestly instead
of appearing to run forever. There is no separate heartbeat thread to trust — liveness is derived
from the owner pid at read time.

## Cost guard degrades when pricing is missing

The per-run cost ceiling can only be enforced in USD when cost is known (`pricing_status` is `native`
or `priced`). When `pricing_status` is `missing`, YanShi **does not** pretend the USD ceiling is in
force. Instead it degrades to a **token ceiling** and records a warning making the degradation
explicit. If you rely on a hard USD ceiling, ensure the model is covered by native cost reporting or
by an entry in `pricing-cache.json`; otherwise set a token ceiling that matches your risk tolerance.
See [Safety & Policy](concepts/safety.md) and [Configuration](reference/configuration.md).

## Where to look

- [Monitoring](concepts/monitoring.md) — what each status field means.
- [Adapters](adapters/index.md) — per-CLI exit codes and terminal events.
- [Configuration](reference/configuration.md) — where `stream.ndjson` and run records live.
