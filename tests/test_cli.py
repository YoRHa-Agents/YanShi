from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yanshi.cli as cli_module
from yanshi.cli import app
from yanshi.contracts import AgentState, AgentStatus, RunResult, RunSpec
from yanshi.preflight import PreflightResult


def test_cli_doctor_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "doctor_run",
        lambda: [PreflightResult(cli="claude", ok=True, executable="/bin/claude", version="v")],
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"


def test_cli_doctor_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "doctor_run",
        lambda: [PreflightResult(cli="claude", ok=False, errors=["missing"])],
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1


def test_cli_dispatch_invokes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, RunSpec] = {}

    async def fake_dispatch(spec: RunSpec) -> RunResult:
        seen["spec"] = spec
        return RunResult(
            agent_id="a1",
            cli=spec.cli,
            state=AgentState.SUCCEEDED,
            is_error=False,
            reply="ok",
        )

    monkeypatch.setattr(cli_module, "dispatch_wait_run", fake_dispatch)
    result = CliRunner().invoke(
        app,
        ["dispatch", "--cli", "claude", "--effort", "high", "hello"],
    )
    assert result.exit_code == 0
    assert seen["spec"].reasoning_effort == "high"
    assert json.loads(result.stdout)["reply"] == "ok"


def test_cli_dispatch_rejects_no_wait() -> None:
    result = CliRunner().invoke(app, ["dispatch", "--no-wait", "hello"])
    assert result.exit_code == 2
    assert "only supports --wait" in result.stderr


def test_cli_dispatch_rejects_invalid_effort() -> None:
    result = CliRunner().invoke(app, ["dispatch", "--effort", "extreme", "hello"])
    assert result.exit_code == 2
    assert "invalid effort" in result.stderr


def test_cli_read_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    current = AgentStatus(agent_id="a1", cli="claude", state=AgentState.SUCCEEDED)
    monkeypatch.setattr(cli_module, "status_run", lambda agent_id: current)
    monkeypatch.setattr(cli_module, "summary_run", lambda agent_id: "summary")
    monkeypatch.setattr(cli_module, "list_agents_run", lambda: ["a1"])
    assert CliRunner().invoke(app, ["status", "a1"]).exit_code == 0
    assert CliRunner().invoke(app, ["summary", "a1"]).stdout.strip() == "summary"
    assert json.loads(CliRunner().invoke(app, ["list"]).stdout) == ["a1"]


def test_cli_wait_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    current = AgentStatus(agent_id="a1", cli="claude", state=AgentState.CANCELLED)

    async def fake_wait(agent_id: str, timeout_s: float | None = None) -> AgentStatus:
        assert agent_id == "a1"
        assert timeout_s == 1.0
        return current

    monkeypatch.setattr(cli_module, "wait_run", fake_wait)
    monkeypatch.setattr(cli_module, "cancel_run", lambda agent_id: current)
    assert CliRunner().invoke(app, ["wait", "a1", "--timeout", "1"]).exit_code == 0
    assert CliRunner().invoke(app, ["cancel", "a1"]).exit_code == 0


def test_cli_gc(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStore:
        def gc(self, *, older_than_s: float) -> list[str]:
            assert older_than_s == 1.0
            return ["a1"]

    monkeypatch.setattr(cli_module, "StatusStore", lambda: FakeStore())
    result = CliRunner().invoke(app, ["gc", "--older-than", "1"])
    assert json.loads(result.stdout) == ["a1"]


def test_cli_record_copies_stream(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_dir = tmp_path / "run"
    log_dir.mkdir()
    (log_dir / "stream.ndjson").write_text("{}\n", encoding="utf-8")

    async def fake_dispatch(spec: RunSpec) -> RunResult:
        return RunResult(
            agent_id="a1",
            cli=spec.cli,
            state=AgentState.SUCCEEDED,
            is_error=False,
            log_dir=str(log_dir),
        )

    output = tmp_path / "fixture.ndjson"
    monkeypatch.setattr(cli_module, "dispatch_wait_run", fake_dispatch)
    result = CliRunner().invoke(app, ["record", "--output", str(output), "hello"])
    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8") == "{}\n"


def test_console_module_import_keeps_python_executable_visible() -> None:
    assert sys.executable
