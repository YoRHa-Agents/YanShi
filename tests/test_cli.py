from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yanshi.cli as cli_module
from yanshi.cli import app
from yanshi.config import parse_config_file
from yanshi.contracts import AgentState, AgentStatus, AllowMode, RunResult, RunSpec
from yanshi.preflight import PreflightResult


def test_cli_help_uses_yanshi_framing() -> None:
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "YanShi (偃师)" in root.stdout
    assert "parent agent remains" in root.stdout
    assert "control threads" in root.stdout

    dispatch = runner.invoke(app, ["dispatch", "--help"])
    assert dispatch.exit_code == 0
    assert "argv-structured dispatch" in dispatch.stdout
    assert "Enabled adapter mechanism name" in dispatch.stdout
    assert "read-only unless configured" in dispatch.stdout

    status = runner.invoke(app, ["status", "--help"])
    assert status.exit_code == 0
    assert "status control thread" in status.stdout


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


# --------------------------------------------------------------------------- #
# config-driven CLI: init / config / --profile (G11.x)
# --------------------------------------------------------------------------- #


def _ok_result(spec: RunSpec) -> RunResult:
    return RunResult(
        agent_id="a1",
        cli=spec.cli,
        state=AgentState.SUCCEEDED,
        is_error=False,
        reply="ok",
    )


def test_cli_init_local_writes_parses_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    config_path = tmp_path / ".yanshi.toml"
    assert config_path.is_file()
    # The emitted template must parse cleanly through the public parser.
    parsed = parse_config_file(config_path)
    assert parsed.adapters.enabled == ["claude", "codex", "cursor", "gemini"]
    original = config_path.read_text(encoding="utf-8")

    # No --force: refuse (exit 1, No Silent Failures) and leave the file intact.
    second = runner.invoke(app, ["init"])
    assert second.exit_code == 1
    assert "refusing to overwrite" in second.stderr
    assert config_path.read_text(encoding="utf-8") == original

    # --force: overwrite is allowed.
    forced = runner.invoke(app, ["init", "--force"])
    assert forced.exit_code == 0
    assert config_path.is_file()


def test_cli_init_global_writes_to_yanshi_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YANSHI_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["init", "--global"])
    assert result.exit_code == 0
    config_path = tmp_path / "config.toml"
    assert config_path.is_file()
    parse_config_file(config_path)  # parses cleanly


def test_cli_config_outputs_layered_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(home))  # isolate: no global config.toml
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yanshi.toml").write_text('[adapters]\nenabled = ["claude"]\n', encoding="utf-8")

    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {"config", "sources", "provenance", "enabled_adapters"} <= payload.keys()
    assert payload["enabled_adapters"] == ["claude"]
    assert payload["config"]["adapters"]["enabled"] == ["claude"]


def test_cli_dispatch_profile_applies_config_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yanshi.toml").write_text('[profiles.cheap]\neffort = "low"\n', encoding="utf-8")

    seen: dict[str, RunSpec] = {}

    async def fake_dispatch(spec: RunSpec) -> RunResult:
        seen["spec"] = spec
        return _ok_result(spec)

    monkeypatch.setattr(cli_module, "dispatch_wait_run", fake_dispatch)

    result = CliRunner().invoke(app, ["dispatch", "--profile", "cheap", "hello"])
    assert result.exit_code == 0
    assert seen["spec"].reasoning_effort == "low"


def test_cli_dispatch_unknown_profile_warns_but_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    seen: dict[str, RunSpec] = {}

    async def fake_dispatch(spec: RunSpec) -> RunResult:
        seen["spec"] = spec
        return _ok_result(spec)

    monkeypatch.setattr(cli_module, "dispatch_wait_run", fake_dispatch)

    result = CliRunner().invoke(app, ["dispatch", "--profile", "bogus", "hello"])
    assert result.exit_code == 0
    assert "spec" in seen  # still dispatched
    assert "profile_unknown" in result.stderr  # warning surfaced (No Silent Failures)


def test_cli_dispatch_clamps_allow_to_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yanshi.toml").write_text('[limits]\nmax_allow = "read-only"\n', encoding="utf-8")

    seen: dict[str, RunSpec] = {}

    async def fake_dispatch(spec: RunSpec) -> RunResult:
        seen["spec"] = spec
        return _ok_result(spec)

    monkeypatch.setattr(cli_module, "dispatch_wait_run", fake_dispatch)

    result = CliRunner().invoke(app, ["dispatch", "--allow", "yolo", "hello"])
    assert result.exit_code == 0
    assert seen["spec"].allow == AllowMode.READ_ONLY
    assert "capability_clamped" in result.stderr


def test_cli_improve_honors_enabled_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """improve must enforce [adapters].enabled (G11.3) like dispatch, instead of
    silently falling back to default_registry() (all adapters)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yanshi.toml").write_text(
        '[adapters]\nenabled = ["claude"]\n', encoding="utf-8"
    )

    # codex is disabled by config; the config-driven kernel must reject it at
    # registry lookup (before any preflight/spawn), surfaced as a fatal_error.
    result = CliRunner().invoke(app, ["improve", "--cli", "codex", "hello"])

    assert result.exit_code == 1
    assert "codex" in result.stdout
    assert "fatal_error" in result.stdout
