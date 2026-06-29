"""End-to-end proof that an agent can drive ``/devola-flow`` *through* YanShi.

The user-facing acceptance is: after the skill is registered, a parent agent
calls ``yanshi dispatch ... "/devola-flow ..."`` and the slash-command reaches
the child CLI **verbatim** (spec §1.3: prompts are passed through, never
templated). These tests cover three layers:

1. the dispatch kernel actually spawns a child and the prompt round-trips,
2. the ``yanshi dispatch`` CLI forwards the ``/devola-flow`` prompt unchanged,
3. the *registered* SKILL.md documents the ``/devola-flow`` dispatch contract,
   tying registration to the agent-usable behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yanshi.cli as cli_module
from yanshi.cli import app
from yanshi.contracts import (
    AgentState,
    BuiltCommand,
    Capabilities,
    EventKind,
    RawOutcome,
    RunResult,
    RunSpec,
    YanShiEvent,
)
from yanshi.dispatch import dispatch
from yanshi.registry import AdapterRegistry
from yanshi.skill_install import SKILL_ENTRY, SKILL_NAME, register_skill

DEVOLA_PROMPT = "/devola-flow build a REST API for todos"


class _EchoAdapter:
    """Minimal adapter that echoes the prompt back so we can assert passthrough."""

    name = "echo"
    prompt_mode = "stdin"
    seed_paths: list[str] = []
    capabilities = Capabilities(stream_json=False)

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        return BuiltCommand(
            command=sys.executable,
            args=["-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            stdin_text=spec.prompt,
        )

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        return YanShiEvent(kind=EventKind.ASSISTANT_TEXT, text=raw_line)

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        return RunResult(
            agent_id="echo-devola",
            cli=self.name,
            state=AgentState.SUCCEEDED if outcome.exit_code == 0 else AgentState.FAILED,
            is_error=outcome.exit_code != 0,
            reply=outcome.stdout,
            exit_code=outcome.exit_code,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        return None


def test_dispatch_passes_devola_flow_prompt_to_child_verbatim() -> None:
    """The dispatched sub-agent receives the exact ``/devola-flow`` prompt."""
    registry = AdapterRegistry()
    registry.register(_EchoAdapter())
    result = dispatch(
        RunSpec(cli="echo", prompt=DEVOLA_PROMPT),
        registry=registry,
        skip_preflight=True,
    )
    assert result.state == AgentState.SUCCEEDED
    assert result.reply == DEVOLA_PROMPT


def test_cli_dispatch_forwards_devola_flow_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """`yanshi dispatch --cli claude "/devola-flow ..."` forwards the prompt unchanged."""
    seen: dict[str, RunSpec] = {}

    async def fake_dispatch(spec: RunSpec) -> RunResult:
        seen["spec"] = spec
        return RunResult(
            agent_id="a1",
            cli=spec.cli,
            state=AgentState.SUCCEEDED,
            is_error=False,
            reply="dispatched",
        )

    monkeypatch.setattr(cli_module, "dispatch_wait_run", fake_dispatch)
    result = CliRunner().invoke(app, ["dispatch", "--cli", "claude", DEVOLA_PROMPT])
    assert result.exit_code == 0
    assert seen["spec"].cli == "claude"
    assert seen["spec"].prompt == DEVOLA_PROMPT


def test_registered_skill_documents_devola_flow_dispatch(tmp_path: Path) -> None:
    """A registered skill teaches the agent how to dispatch ``/devola-flow``."""
    register_skill(skills_dir=tmp_path / "skills")
    registered = tmp_path / "skills" / SKILL_NAME / SKILL_ENTRY
    text = registered.read_text(encoding="utf-8")
    assert "yanshi dispatch" in text
    assert "/devola-flow" in text
