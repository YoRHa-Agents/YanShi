# Installation

YanShi ships a single CLI, `yanshi`, plus an importable Python library and an optional MCP shim.
The bundled `install.sh` is the recommended entry point: it is **uv-first with a pip + venv
fallback**, defaults to a read-only, no-surprises setup, and never fails the install just because a
vendor CLI is missing.

## Prerequisites

- **Python 3.12+** — the installer probes `python3`/`python` and refuses anything older.
- **[uv](https://docs.astral.sh/uv/)** (recommended) — used for both local and global installs.
  Without it, the installer falls back to `pip`/`venv` (local) or `pipx`/`pip --user` (global).
- The four vendor CLIs (`claude`, `codex`, `cursor-agent`, `gemini`) are **optional** and are only
  *detected*, never installed, by YanShi. See [Vendor CLIs are detected, not installed](#vendor-clis-are-detected-not-installed).

## Quick install with `install.sh`

One-liner (global install via the bundled installer):

```bash
curl -fsSL https://raw.githubusercontent.com/YoRHa-Agents/YanShi/main/install.sh | bash -s -- --global
```

From a checkout, run it directly:

```bash
./install.sh --local --dev
```

### Installer options

| Flag | Effect |
|---|---|
| `--local` | Editable install into a project `.venv` (the default when no scope is given). Must run from a checkout. |
| `--global` | Global tool install via `uv tool install` (falls back to `pipx`, then `pip install --user`). |
| `--with-mcp` | Also print MCP wiring instructions and verify that `skill/mcp_server.py` imports. |
| `--dev` | Include the `dev` dependency group (pytest, ruff, mypy). |
| `--docs` | Include the `docs` dependency group (MkDocs Material + i18n). |
| `--dry-run` | Print every action without changing the system. |
| `--lang zh\|en` | Force the installer's message language (otherwise inferred from `$LANG`). |
| `--help` | Show usage and exit. |

!!! note "Local vs. global scope"
    `--local` does an **editable** install into `<checkout>/.venv` and is best for development. The
    `--dev`/`--docs` groups apply to local installs; they are dev-only and are ignored for a
    `--global` tool install.

## The `uv` path

If you have a checkout and want the standard development environment:

```bash
uv sync --group dev      # core + dev tools (pytest, ruff, mypy)
uv run yanshi doctor     # verify which vendor CLIs are available
```

Add the docs toolchain when you intend to build the site:

```bash
uv sync --group docs
```

## The `pip` fallback

When `uv` is unavailable, a plain virtual environment works too:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs]"   # extras are optional
yanshi doctor
```

## Vendor CLIs are detected, not installed

YanShi dispatches to vendor CLIs but does **not** bundle or install them. After installing YanShi,
run [`yanshi doctor`](../cli/reference.md#doctor) to see which adapters have a working executable and
authentication:

```bash
yanshi doctor
```

`doctor` prints one JSON line per adapter (`cli`, `status`, `executable`, `version`, `errors`,
`warnings`) and exits non-zero if any adapter fails its preflight. This is informational: a missing
`gemini`, for example, does not prevent you from dispatching to `claude`. Install and authenticate
each vendor CLI you intend to use, following that vendor's own instructions.

## `$YANSHI_HOME`

YanShi keeps all run state under `$YANSHI_HOME`, which defaults to `~/.yanshi`. Override it to
relocate the per-agent run records, raw streams, and caches:

```bash
export YANSHI_HOME="$HOME/.local/state/yanshi"
```

See [Configuration](../reference/configuration.md) for the full on-disk layout.

## Next steps

Head to the [Quickstart](quickstart.md) to dispatch your first sub-agent and learn the
low-context polling rule.
