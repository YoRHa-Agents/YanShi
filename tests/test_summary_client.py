from __future__ import annotations

import sys
from typing import Any

import pytest

from yanshi.contracts import (
    AgentState,
    BuiltCommand,
    Capabilities,
    RawOutcome,
    RunResult,
    RunSpec,
    YanShiEvent,
)
from yanshi.registry import AdapterRegistry
from yanshi.summary_client import AgentCliSummaryClient


class FakeAdapter:
    """Minimal adapter whose one-shot reply is fully controllable."""

    name = "fake"
    prompt_mode = "stdin"
    seed_paths: list[str] = []
    capabilities = Capabilities()

    def __init__(self, *, reply: str | None = "fake summary", is_error: bool = False) -> None:
        self._reply = reply
        self._is_error = is_error
        self.seen_prompt: str | None = None

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        self.seen_prompt = spec.prompt
        return BuiltCommand(command=sys.executable, args=["-c", "pass"], stdin_text=spec.prompt)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        return None

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        return RunResult(
            agent_id="placeholder",
            cli=self.name,
            state=AgentState.FAILED if self._is_error else AgentState.SUCCEEDED,
            is_error=self._is_error,
            reply=self._reply,
            exit_code=outcome.exit_code,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        return None


def _registry(adapter: Any) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(adapter)
    return registry


@pytest.mark.asyncio
async def test_summarize_returns_reply() -> None:
    adapter = FakeAdapter(reply="fake summary")
    client = AgentCliSummaryClient(cli="fake", registry=_registry(adapter))
    assert await client.summarize("events: ...") == "fake summary"


@pytest.mark.asyncio
async def test_summarize_strips_reply_whitespace() -> None:
    client = AgentCliSummaryClient(cli="fake", registry=_registry(FakeAdapter(reply="  spaced  ")))
    assert await client.summarize("events: ...") == "spaced"


@pytest.mark.asyncio
async def test_summarize_raises_runtimeerror_on_error_result() -> None:
    client = AgentCliSummaryClient(cli="fake", registry=_registry(FakeAdapter(is_error=True)))
    with pytest.raises(RuntimeError):
        await client.summarize("events: ...")


@pytest.mark.asyncio
async def test_summarize_raises_runtimeerror_on_empty_reply() -> None:
    client = AgentCliSummaryClient(cli="fake", registry=_registry(FakeAdapter(reply=None)))
    with pytest.raises(RuntimeError):
        await client.summarize("events: ...")


@pytest.mark.asyncio
async def test_summarize_wraps_prompt_and_uses_run_blocking_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(reply="patched summary")
    captured: dict[str, Any] = {}

    def fake_run_blocking(command: BuiltCommand, *, timeout_s: int | None = None) -> RawOutcome:
        captured["timeout_s"] = timeout_s
        captured["stdin"] = command.stdin_text
        return RawOutcome(command=command.command, args=command.args, exit_code=0)

    monkeypatch.setattr("yanshi.summary_client._run_blocking", fake_run_blocking)
    client = AgentCliSummaryClient(cli="fake", registry=_registry(adapter), timeout_s=42)

    result = await client.summarize("events:\n- tool_use: ran pytest")

    assert result == "patched summary"
    assert captured["timeout_s"] == 42
    # The structured digest is wrapped with the monitoring instruction before dispatch.
    assert "monitoring assistant" in (adapter.seen_prompt or "")
    assert "ran pytest" in (adapter.seen_prompt or "")
    assert captured["stdin"] == adapter.seen_prompt


@pytest.mark.asyncio
async def test_summarize_converts_call_failure_to_runtimeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(command: BuiltCommand, *, timeout_s: int | None = None) -> RawOutcome:
        raise ValueError("subprocess exploded")

    monkeypatch.setattr("yanshi.summary_client._run_blocking", boom)
    client = AgentCliSummaryClient(cli="fake", registry=_registry(FakeAdapter()))
    with pytest.raises(RuntimeError):
        await client.summarize("events: ...")
