from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

import yanshi.runner as runner_module
from yanshi.contracts import BuiltCommand
from yanshi.runner import build_child_env, run_blocking


def test_build_child_env_filters_ambient_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("PATH", "/bin")
    env = build_child_env({"EXPLICIT": "ok"})
    assert env["PATH"] == "/bin"
    assert env["EXPLICIT"] == "ok"
    assert "ANTHROPIC_API_KEY" not in env


def test_run_blocking_passes_prompt_via_stdin() -> None:
    command = BuiltCommand(
        command=sys.executable,
        args=["-c", "import sys; print(sys.stdin.read().upper())"],
        stdin_text="hello",
    )
    outcome = run_blocking(command, timeout_s=5)
    assert outcome.exit_code == 0
    assert outcome.stdout.strip() == "HELLO"
    assert outcome.timed_out is False


def test_run_blocking_reads_stdin_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("file prompt", encoding="utf-8")
    command = BuiltCommand(
        command=sys.executable,
        args=["-c", "import sys; print(sys.stdin.read())"],
        stdin_file=str(prompt_file),
    )
    outcome = run_blocking(command, timeout_s=5)
    assert outcome.stdout.strip() == "file prompt"


def test_run_blocking_reports_timeout() -> None:
    command = BuiltCommand(
        command=sys.executable,
        args=["-c", "import time; time.sleep(2)"],
    )
    outcome = run_blocking(command, timeout_s=1)
    assert outcome.timed_out is True
    assert outcome.exit_code is None


def test_runner_source_never_uses_shell_true() -> None:
    source = inspect.getsource(runner_module)
    assert "shell=True" not in source
