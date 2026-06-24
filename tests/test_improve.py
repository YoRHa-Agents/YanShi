from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import yanshi.cli as cli_module
from yanshi.cli import app
from yanshi.contracts import (
    AgentState,
    BuiltCommand,
    Capabilities,
    EventKind,
    GateOutcome,
    ImproveResult,
    ImproveSpec,
    RawOutcome,
    RunResult,
    RunSpec,
    SessionMode,
    Usage,
    YanShiEvent,
)
from yanshi.improve import improve_loop, run_argv_gate
from yanshi.registry import AdapterRegistry


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeKernel:
    """Records dispatched specs and returns canned results in order."""

    def __init__(self, results: list[RunResult]) -> None:
        self.results = list(results)
        self.specs: list[RunSpec] = []

    async def run(
        self,
        spec: RunSpec,
        *,
        agent_id: str | None = None,
        skip_preflight: bool = False,
    ) -> RunResult:
        self.specs.append(spec)
        return self.results.pop(0)


class RaisingKernel:
    async def run(
        self,
        spec: RunSpec,
        *,
        agent_id: str | None = None,
        skip_preflight: bool = False,
    ) -> RunResult:
        raise RuntimeError("dispatch exploded")


def _gate_runner(
    outcomes: list[GateOutcome],
) -> Callable[..., Awaitable[GateOutcome]]:
    async def runner(
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int | None = None,
        output_limit: int = 4000,
    ) -> GateOutcome:
        return outcomes.pop(0)

    return runner


class ScriptedCritic:
    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.prompts: list[str] = []

    async def critique(self, prompt: str) -> tuple[float, str]:
        self.prompts.append(prompt)
        score = self.scores.pop(0)
        return score, f"feedback for score {score}"


def _result(
    *,
    state: AgentState = AgentState.SUCCEEDED,
    is_error: bool = False,
    session_id: str | None = None,
    cost: float | None = 0.1,
) -> RunResult:
    return RunResult(
        agent_id="placeholder",
        cli="fake",
        state=state,
        is_error=is_error,
        reply="ok",
        usage=Usage(input_tokens=1, output_tokens=1),
        cost_usd=cost,
        session_id=session_id,
    )


def _spec(cli: str = "fake") -> RunSpec:
    return RunSpec(cli=cli, prompt="do the task")


# --------------------------------------------------------------------------- #
# improve_loop unit tests (FakeKernel)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_gate_passes_first_iteration() -> None:
    kernel = FakeKernel([_result()])
    plan = ImproveSpec(spec=_spec(), check_command=["true"], max_iterations=3)
    gates = [GateOutcome(ran=True, passed=True, exit_code=0)]
    result = await improve_loop(plan, kernel=kernel, gate_runner=_gate_runner(gates))
    assert result.succeeded is True
    assert result.stop_reason == "gate_passed"
    assert len(result.iterations) == 1
    assert len(kernel.specs) == 1


@pytest.mark.asyncio
async def test_fail_twice_then_pass_embeds_gate_output_and_aggregates() -> None:
    kernel = FakeKernel([_result(), _result(), _result()])
    gates = [
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="ERR1"),
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="ERR2"),
        GateOutcome(ran=True, passed=True, exit_code=0),
    ]
    plan = ImproveSpec(spec=_spec(), check_command=["check"], max_iterations=3)
    result = await improve_loop(plan, kernel=kernel, gate_runner=_gate_runner(gates))
    assert result.succeeded is True
    assert result.stop_reason == "gate_passed"
    assert len(result.iterations) == 3
    # Refined prompts must embed the prior gate output tail (low-context feedback).
    assert "ERR1" in kernel.specs[1].prompt
    assert "ERR2" in kernel.specs[2].prompt
    # Original task is preserved across refinements.
    assert kernel.specs[1].prompt.startswith("do the task")
    # Usage and cost aggregated across all iterations.
    assert result.total_usage.total == 6
    assert result.total_cost_usd == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_gate_never_passes_stops_at_max_iterations() -> None:
    kernel = FakeKernel([_result(), _result()])
    gates = [
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="x"),
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="x"),
    ]
    plan = ImproveSpec(spec=_spec(), check_command=["check"], max_iterations=2)
    result = await improve_loop(plan, kernel=kernel, gate_runner=_gate_runner(gates))
    assert result.succeeded is False
    assert result.stop_reason == "max_iterations"
    assert len(result.iterations) == 2


@pytest.mark.asyncio
async def test_critic_only_crosses_threshold() -> None:
    kernel = FakeKernel([_result(), _result()])
    critic = ScriptedCritic([0.5, 0.9])
    plan = ImproveSpec(spec=_spec(), use_critic=True, critic_threshold=0.8, max_iterations=3)
    result = await improve_loop(plan, kernel=kernel, critic_client=critic)
    assert result.succeeded is True
    assert result.stop_reason == "critic_threshold"
    assert len(result.iterations) == 2
    assert result.iterations[0].critic_score == 0.5
    assert result.iterations[1].critic_score == 0.9
    assert result.iterations[1].critic_feedback == "feedback for score 0.9"


@pytest.mark.asyncio
async def test_critic_requested_but_unavailable_degrades_with_warning() -> None:
    kernel = FakeKernel([_result(), _result()])
    plan = ImproveSpec(spec=_spec(), use_critic=True, max_iterations=2)
    result = await improve_loop(plan, kernel=kernel, critic_client=None)
    assert result.succeeded is False
    assert result.stop_reason == "max_iterations"
    assert any(w.code == "critic_unavailable" for w in result.warnings)


@pytest.mark.asyncio
async def test_critic_error_is_surfaced_as_warning() -> None:
    class BoomCritic:
        async def critique(self, prompt: str) -> tuple[float, str]:
            raise RuntimeError("no api key")

    kernel = FakeKernel([_result()])
    plan = ImproveSpec(spec=_spec(), use_critic=True, max_iterations=1)
    result = await improve_loop(plan, kernel=kernel, critic_client=BoomCritic())
    assert any(w.code == "critic_error" and "no api key" in w.message for w in result.warnings)


@pytest.mark.asyncio
async def test_no_evaluator_single_pass_success() -> None:
    kernel = FakeKernel([_result(is_error=False)])
    plan = ImproveSpec(spec=_spec(), max_iterations=3)
    result = await improve_loop(plan, kernel=kernel)
    assert result.stop_reason == "no_evaluator"
    assert result.succeeded is True
    assert len(result.iterations) == 1
    assert len(kernel.specs) == 1


@pytest.mark.asyncio
async def test_no_evaluator_single_pass_error() -> None:
    kernel = FakeKernel([_result(state=AgentState.FAILED, is_error=True)])
    plan = ImproveSpec(spec=_spec(), max_iterations=3)
    result = await improve_loop(plan, kernel=kernel)
    assert result.stop_reason == "no_evaluator"
    assert result.succeeded is False


@pytest.mark.asyncio
async def test_dispatch_exception_is_fatal_error_not_swallowed() -> None:
    plan = ImproveSpec(spec=_spec(), check_command=["check"], max_iterations=3)
    result = await improve_loop(plan, kernel=RaisingKernel())
    assert result.succeeded is False
    assert result.stop_reason == "fatal_error"
    assert result.iterations == []
    assert any(w.code == "dispatch_error" and "exploded" in w.message for w in result.warnings)


def test_max_iterations_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ImproveSpec(spec=_spec(), max_iterations=0)


@pytest.mark.asyncio
async def test_session_resume_when_adapter_supports_it() -> None:
    registry = AdapterRegistry()
    registry.register(_FleetAdapter(session_resume=True))
    kernel = FakeKernel([_result(session_id="sess-1"), _result()])
    gates = [
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="boom"),
        GateOutcome(ran=True, passed=True, exit_code=0),
    ]
    plan = ImproveSpec(spec=_spec(cli="fleet"), check_command=["check"], max_iterations=2)
    result = await improve_loop(
        plan, kernel=kernel, gate_runner=_gate_runner(gates), registry=registry
    )
    assert result.succeeded is True
    assert kernel.specs[1].session_mode == SessionMode.RESUME
    assert kernel.specs[1].session_id == "sess-1"


@pytest.mark.asyncio
async def test_no_resume_when_capability_absent() -> None:
    registry = AdapterRegistry()
    registry.register(_FleetAdapter(session_resume=False))
    kernel = FakeKernel([_result(session_id="sess-1"), _result()])
    gates = [
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="boom"),
        GateOutcome(ran=True, passed=True, exit_code=0),
    ]
    plan = ImproveSpec(spec=_spec(cli="fleet"), check_command=["check"], max_iterations=2)
    result = await improve_loop(
        plan, kernel=kernel, gate_runner=_gate_runner(gates), registry=registry
    )
    assert result.succeeded is True
    assert kernel.specs[1].session_mode == SessionMode.NEW
    assert kernel.specs[1].session_id is None


# --------------------------------------------------------------------------- #
# run_argv_gate direct tests (real subprocess, argv-only)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_argv_gate_pass() -> None:
    gate = await run_argv_gate([sys.executable, "-c", "import sys; sys.exit(0)"], timeout_s=10)
    assert gate.ran is True
    assert gate.passed is True
    assert gate.exit_code == 0


@pytest.mark.asyncio
async def test_run_argv_gate_fail_captures_output() -> None:
    gate = await run_argv_gate(
        [sys.executable, "-c", "print('boom'); import sys; sys.exit(3)"], timeout_s=10
    )
    assert gate.ran is True
    assert gate.passed is False
    assert gate.exit_code == 3
    assert "boom" in gate.output_excerpt


@pytest.mark.asyncio
async def test_run_argv_gate_timeout() -> None:
    gate = await run_argv_gate(
        [sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=1
    )
    assert gate.ran is True
    assert gate.passed is False
    assert gate.error is not None
    assert "timed out" in gate.error


@pytest.mark.asyncio
async def test_run_argv_gate_spawn_failure() -> None:
    gate = await run_argv_gate(["definitely-not-a-real-binary-xyz-123"], timeout_s=5)
    assert gate.ran is False
    assert gate.passed is False
    assert gate.error is not None


@pytest.mark.asyncio
async def test_run_argv_gate_empty_argv() -> None:
    gate = await run_argv_gate([], timeout_s=5)
    assert gate.ran is False
    assert gate.error == "empty gate command"


# --------------------------------------------------------------------------- #
# Integration test: real MonitorKernel + fake adapter + injected gate runner
# --------------------------------------------------------------------------- #
@dataclass
class _FleetAdapter:
    name: str = "fleet"
    prompt_mode: str = "stdin"
    seed_paths: list[str] = field(default_factory=list)
    session_resume: bool = False
    capabilities: Capabilities = field(default_factory=lambda: Capabilities(stream_json=True))

    def __post_init__(self) -> None:
        self.capabilities = Capabilities(
            stream_json=True, session_resume=self.session_resume
        )

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        script = (
            "import json, sys;"
            "prompt=sys.stdin.read();"
            "print(json.dumps({'kind':'started'}), flush=True);"
            "print(json.dumps({'kind':'completed','text':prompt}), flush=True)"
        )
        return BuiltCommand(command=sys.executable, args=["-c", script], stdin_text=spec.prompt)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        raw = json.loads(raw_line)
        return YanShiEvent(
            kind=EventKind(raw["kind"]),
            text=raw.get("text", ""),
            usage=Usage(input_tokens=1, output_tokens=1) if raw["kind"] == "completed" else None,
            cost_usd=0.1 if raw["kind"] == "completed" else None,
            is_error=False if raw["kind"] == "completed" else None,
        )

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        return RunResult(
            agent_id="placeholder",
            cli=self.name,
            state=AgentState.SUCCEEDED if outcome.exit_code == 0 else AgentState.FAILED,
            is_error=outcome.exit_code != 0,
            reply=outcome.stdout,
            usage=Usage(input_tokens=1, output_tokens=1),
            cost_usd=0.1,
            exit_code=outcome.exit_code,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        return None


@pytest.mark.asyncio
async def test_integration_real_kernel_refines_prompt(tmp_path: Path) -> None:
    from yanshi.store import StatusStore

    registry = AdapterRegistry()
    registry.register(_FleetAdapter())
    store = StatusStore(tmp_path)
    gates = [
        GateOutcome(ran=True, passed=False, exit_code=1, output_excerpt="GATEFAIL_MARKER"),
        GateOutcome(ran=True, passed=True, exit_code=0),
    ]
    plan = ImproveSpec(spec=_spec(cli="fleet"), check_command=["check"], max_iterations=2)
    result = await improve_loop(
        plan,
        registry=registry,
        store=store,
        gate_runner=_gate_runner(gates),
        skip_preflight=True,
    )
    assert result.succeeded is True
    assert result.stop_reason == "gate_passed"
    assert len(result.iterations) == 2
    assert result.final_agent_id is not None
    # The second dispatch's prompt (echoed back by the adapter) embeds the gate tail.
    final = store.read_result(result.final_agent_id)
    assert final.reply is not None
    assert "GATEFAIL_MARKER" in final.reply


# --------------------------------------------------------------------------- #
# CLI tests
# --------------------------------------------------------------------------- #
def test_cli_improve_parses_check_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, ImproveSpec] = {}

    async def fake_loop(plan: ImproveSpec) -> ImproveResult:
        captured["plan"] = plan
        return ImproveResult(succeeded=True, stop_reason="gate_passed")

    monkeypatch.setattr(cli_module, "improve_loop", fake_loop)
    result = CliRunner().invoke(
        app,
        ["improve", "--cli", "claude", "--check", "pytest -q", "--max-iterations", "2", "fix it"],
    )
    assert result.exit_code == 0
    assert captured["plan"].check_command == ["pytest", "-q"]
    assert captured["plan"].max_iterations == 2
    assert captured["plan"].spec.prompt == "fix it"


def test_cli_improve_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(plan: ImproveSpec) -> ImproveResult:
        return ImproveResult(succeeded=False, stop_reason="max_iterations")

    monkeypatch.setattr(cli_module, "improve_loop", fake_loop)
    result = CliRunner().invoke(app, ["improve", "--check", "false", "task"])
    assert result.exit_code == 1


def test_cli_improve_rejects_zero_iterations() -> None:
    result = CliRunner().invoke(app, ["improve", "--max-iterations", "0", "task"])
    assert result.exit_code == 2
    assert "max-iterations must be >= 1" in result.stderr
