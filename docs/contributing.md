# Contributing

YanShi is a small, strict codebase: typed contracts, deterministic monitoring, and no silent
failures. Contributions are expected to keep the test, lint, and type gates green and to follow the
project's governance.

## Development setup

Install the core package plus the `dev` dependency group with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --group dev
```

This provides `pytest`, `ruff`, and `mypy`. A `pip`/`venv` setup works too (see
[Installation](getting-started/installation.md)).

## The quality gates

Run these before opening a pull request; CI runs the same set.

```bash
uv run pytest -m "not live" --cov     # tests with coverage (offline)
uv run ruff check .                   # lint
uv run mypy --strict src tests        # strict type checking
```

- **Tests** use `pytest` (with `pytest-asyncio`). Coverage is configured to fail under its threshold,
  so add tests alongside new logic.
- **Lint** uses `ruff` (line length 100; rule sets `E`, `F`, `I`, `UP`, `B`, `SIM`).
- **Types** must pass `mypy --strict` for both `src` and `tests`.

!!! note "Mandatory verification"
    Never skip or mark verification as a TODO to bypass it. New logic ships with tests, and all three
    gates must pass.

## Live tests are gated by `YANSHI_LIVE`

End-to-end tests that spawn real vendor CLIs are marked `live` and are **excluded by default**
(`-m "not live"`). They require authenticated CLIs and an explicit opt-in:

```bash
YANSHI_LIVE=1 uv run pytest -m live
```

Recording fixtures for offline parser tests is done with the maintenance command
`yanshi record` (see the [CLI Reference](cli/reference.md#record)).

## Install tests are gated by `YANSHI_INSTALL_IT`

`tests/test_install_sh.py` checks `install.sh` offline (syntax, `--help`, `--dry-run`, unknown
flags). The full end-to-end suite in `tests/test_install_integration.py` actually runs
`install.sh --local` and `--global` against an isolated copy of the repo and asserts the resulting
`yanshi` CLI works (correct version, `--help`, `--with-mcp`, `--docs`, idempotency). These build the
package and create environments, so they are marked `install_it` and are opt-in:

```bash
YANSHI_INSTALL_IT=1 uv run pytest -m install_it
```

## Adding an adapter

A new CLI is one adapter plus its capability metadata — the kernel doesn't change:

1. Implement the `Adapter` protocol (`build_command`, `parse_event`, `parse_result`,
   `session_id_from_event`).
2. Declare capabilities in the adapter's TOML data file and register it in the default registry.
3. Keep all vendor dialect (flags, model suffixes, event vocabulary) inside that one adapter.
4. Honor the safety invariants: argv-only spawning, `read-only` by default, structured warnings for
   unsupported controls, and no swallowed errors.

See [Adapters](adapters/index.md) for the mapping every adapter must provide.

## Documentation workflow

The docs site is MkDocs Material with `mkdocs-static-i18n`. Install the docs toolchain, then serve or
build:

```bash
uv sync --group docs
mkdocs serve                 # live preview at http://127.0.0.1:8000
mkdocs build --strict        # the build CI enforces: any warning fails
```

!!! warning "`--strict` fails on any warning"
    Broken internal links, missing pages, and other issues abort `mkdocs build --strict`. Use
    relative links between pages, give code fences a language tag, and write Mermaid diagrams as
    fenced ```mermaid blocks with valid node ids.

### Bilingual `*.zh.md` convention

The site is bilingual using the i18n **suffix** layout. An English page lives at `path/page.md`; its
Simplified Chinese translation lives beside it as `path/page.zh.md`. The navigation is defined once in
`mkdocs.yml` (with `nav_translations` for the Chinese labels) and is shared by both languages — do not
duplicate the nav per language. When a translation is absent, the i18n plugin falls back silently to
the English page.

## Governance reminders

- **No silent failures** — log, re-raise, or return an explicit error/warning; never swallow errors.
- **Protected branches** — never push directly to protected branches; open a merge/pull request from a
  feature branch.
- **Source of truth** — the design lives in `.local/memory/specs/yanshi/`; implement those decisions
  rather than changing them.
