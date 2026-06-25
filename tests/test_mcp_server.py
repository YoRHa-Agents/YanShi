"""Regression tests for the MCP wrapper's config/profile resolution.

The MCP ``dispatch`` wrapper lives at ``skill/mcp_server.py`` (not under
``src/``) and is not a package, so it is loaded here via importlib. These tests
pin the fix for the Bugbot finding "MCP dispatch always overrides profile/config
CLI selection": ``cli`` must only act as an explicit override when the caller
actually supplies it, otherwise ``[defaults].cli`` / ``[profiles.<name>].cli``
from the repo config must win (mirroring the CLI path).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from yanshi.contracts import AgentState, RunResult, RunSpec

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_mcp() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "yanshi_mcp_server", REPO_ROOT / "skill" / "mcp_server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, toml: str | None) -> None:
    """Point config discovery at an isolated cwd + empty global home."""

    monkeypatch.setenv("YANSHI_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    if toml is not None:
        (tmp_path / ".yanshi.toml").write_text(toml, encoding="utf-8")


def _capture_dispatch(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_dispatch_wait(spec: RunSpec, **_: Any) -> RunResult:
        captured["spec"] = spec
        return RunResult(agent_id="t", cli=spec.cli, state=AgentState.SUCCEEDED, is_error=False)

    monkeypatch.setattr(module, "dispatch_wait", fake_dispatch_wait)
    return captured


def test_mcp_dispatch_profile_cli_is_respected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A profile's cli must survive through the MCP wrapper (the regression)."""

    module = _load_mcp()
    _isolate_config(monkeypatch, tmp_path, '[profiles.fast]\ncli = "gemini"\n')
    captured = _capture_dispatch(monkeypatch, module)

    module.dispatch("hello", profile="fast")

    assert captured["spec"].cli == "gemini"


def test_mcp_dispatch_defaults_cli_is_respected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """[defaults].cli applies when the caller does not pass cli."""

    module = _load_mcp()
    _isolate_config(monkeypatch, tmp_path, '[defaults]\ncli = "codex"\n')
    captured = _capture_dispatch(monkeypatch, module)

    module.dispatch("hello")

    assert captured["spec"].cli == "codex"


def test_mcp_dispatch_explicit_cli_overrides_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit caller cli still wins over a profile (highest precedence)."""

    module = _load_mcp()
    _isolate_config(monkeypatch, tmp_path, '[profiles.fast]\ncli = "gemini"\n')
    captured = _capture_dispatch(monkeypatch, module)

    module.dispatch("hello", cli="cursor", profile="fast")

    assert captured["spec"].cli == "cursor"


def test_mcp_dispatch_falls_back_to_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no cli anywhere, the wrapper falls back to claude."""

    module = _load_mcp()
    _isolate_config(monkeypatch, tmp_path, toml=None)
    captured = _capture_dispatch(monkeypatch, module)

    module.dispatch("hello")

    assert captured["spec"].cli == "claude"
