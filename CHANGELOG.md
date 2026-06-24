# Changelog

All notable changes to YanShi are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-06-25

Repo-level initialization & configuration. Addresses
`.local/feedbacks/feedback_for_v1.0.0.md` (an init phase + per-workspace
configuration: which adapters are enabled, how summarization runs, and
configurable invocation defaults/levels). See spec §14 and governance G11.

### Added

- **Layered configuration** — local `.yanshi.toml` discovered by walking up from
  `cwd` to the filesystem root (first hit wins, git-style), layered over the
  global `$YANSHI_HOME/config.toml`. Precedence is deterministic, low→high:
  built-in defaults < global < local < per-call override.
- **`[adapters].enabled`** — choose which agent CLIs (subset of
  `{claude, codex, cursor, gemini}`, default all) are active. Only enabled
  adapters are registered and `doctor`-checked; requesting a disabled or unknown
  adapter fails fast and lists the current enabled set (no silent fallback).
- **`[defaults]` / `[profiles.<name>]` / `[limits]`** — configurable invocation
  defaults, named presets selected via `--profile`, and hard workspace ceilings.
  `[limits]` clamps last (`max_allow`, `max_cost_usd`, `max_timeout_s`); every
  actual clamp emits a structured `WarningRecord` (never silently tightened).
- **`[summarizer]`** — opt-in summarizer config (`cli`, `model`, `debounce_s`,
  `min_new_events`, `max_tokens`, `watcher_token_ceiling`, `timeout_s`);
  defaults to `enabled = false` for backward compatibility.
- **CLI** — new `yanshi init [--global|--local] [--force]` to scaffold a config
  file (refuses to overwrite an existing file without `--force`), and
  `yanshi config` to print the resolved layered config with per-value provenance
  (built-in / global / local / override). Added `--profile <name>` to
  `yanshi dispatch` and `yanshi improve`.
- **MCP** — new `get_config()` tool and an optional `profile` argument on
  `dispatch`.
- **New modules** — `src/yanshi/config.py` (discovery, deep-merge, resolution,
  clamping) and `src/yanshi/summary_client.py` (`AgentCliSummaryClient`).

### Changed

- **Rolling summary** is now realized as an opt-in ultra-light one-shot
  agent-CLI call (`AgentCliSummaryClient`) wired into `MonitorKernel`, throttled
  and budget-bounded; any error, timeout, or budget exhaustion degrades to the
  deterministic "concatenate salient events" fallback without blocking or
  slowing the monitored run. Disabled by default (`[summarizer].enabled = false`),
  so behavior is unchanged for existing installs.
- **Dispatch/improve** now resolve a final `RunSpec` from the layered config
  (defaults → profile → per-call override → `[limits]` clamp) before policy
  preflight; resolution is deterministic and reproducible for `yanshi config`
  replay/audit. Explicit per-call values are never overridden by config.

## [1.0.0] - 2026-06-25

- Initial release of YanShi: vendor-neutral agent-CLI dispatch layer with
  deterministic low-context monitoring (`dispatch`, `status`, `summary`, `wait`,
  `cancel`, `doctor`, `improve`), the bounded dispatch→evaluate→refine `improve`
  loop, MCP surface, and the bilingual userguide system. Released as commit
  `c24c828` ("Release v1.0.0: version bump + full install integration tests").

[1.1.0]: https://github.com/YoRHa-Agents/YanShi/compare/c24c828...HEAD
[1.0.0]: https://github.com/YoRHa-Agents/YanShi/commit/c24c828
