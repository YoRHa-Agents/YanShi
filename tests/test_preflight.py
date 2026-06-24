from __future__ import annotations

from pathlib import Path

import pytest

from yanshi.adapters.claude import ClaudeAdapter
from yanshi.errors import PreflightError
from yanshi.preflight import preflight_adapter


def test_preflight_missing_executable_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    result = preflight_adapter(ClaudeAdapter(), env={"PATH": ""})
    assert result.ok is False
    assert "missing CLI executable" in result.errors[0]


def test_preflight_claude_auth_env_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\necho claude-test-version\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    result = preflight_adapter(
        ClaudeAdapter(),
        env={"PATH": str(tmp_path), "ANTHROPIC_API_KEY": "test-key"},
    )
    assert result.ok is True
    assert result.executable == str(executable)
    assert result.version == "claude-test-version"


def test_preflight_require_ok_raises_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    result = preflight_adapter(ClaudeAdapter(), env={"PATH": ""})
    with pytest.raises(PreflightError):
        result.require_ok()


def test_preflight_claude_auth_seed_file_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    home = tmp_path / "home"
    auth_dir = home / ".claude"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = preflight_adapter(
        ClaudeAdapter(),
        env={"PATH": str(tmp_path), "HOME": str(home)},
    )
    assert result.ok is True
    assert result.version is None
    assert result.warnings == ["could not detect version for claude"]
