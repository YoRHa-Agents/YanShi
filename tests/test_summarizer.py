from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from yanshi.config import SummarizerSettings
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
from yanshi.monitor import MonitorKernel
from yanshi.reducer import initial_status
from yanshi.registry import AdapterRegistry
from yanshi.store import StatusStore
from yanshi.summarizer import RollingSummarizer, SummarizerConfig


class FakeClient:
    async def summarize(self, prompt: str) -> str:
        assert "events:" in prompt
        return " ".join(["summary"] * 200)


class FailingClient:
    async def summarize(self, prompt: str) -> str:
        raise RuntimeError("no key")


def _events() -> list[YanShiEvent]:
    return [
        YanShiEvent(kind=EventKind.ASSISTANT_TEXT, text="not significant"),
        YanShiEvent(kind=EventKind.TOOL_USE, text="ran pytest"),
        YanShiEvent(kind=EventKind.ERROR, err="failed test"),
    ]


def test_summarizer_throttles_until_threshold() -> None:
    summarizer = RollingSummarizer(
        SummarizerConfig(debounce_s=5, min_new_events=3),
        clock=lambda: 10,
    )
    assert summarizer.should_trigger(_events()) is False


@pytest.mark.asyncio
async def test_summarizer_uses_llm_and_bounds_output() -> None:
    status = initial_status("a1", "claude")
    summarizer = RollingSummarizer(
        SummarizerConfig(debounce_s=0, min_new_events=1, max_tokens=3),
        clock=lambda: 10,
    )
    result = await summarizer.summarize(status, _events(), client=FakeClient())
    assert result.used_llm is True
    assert result.text == "summary summary summary"
    assert result.usage.total > 0


@pytest.mark.asyncio
async def test_summarizer_fallback_for_no_client_error_and_budget() -> None:
    status = initial_status("a1", "claude")
    summarizer = RollingSummarizer(
        SummarizerConfig(debounce_s=0, min_new_events=1),
        clock=lambda: 10,
    )
    no_client = await summarizer.summarize(status, _events())
    assert no_client.used_llm is False
    assert no_client.warning == "llm_unavailable"
    assert "ran pytest" in no_client.text

    summarizer.last_summary_at = 0
    summarizer.last_event_count = 0
    failure = await summarizer.summarize(status, _events(), client=FailingClient(), now=20)
    assert failure.warning == "llm_error:no key"

    budgeted = RollingSummarizer(
        SummarizerConfig(debounce_s=0, min_new_events=1, watcher_token_ceiling=0),
        clock=lambda: 30,
    )
    budget = await budgeted.summarize(status, _events(), client=FakeClient())
    assert budget.warning == "watcher_budget_exceeded"


def test_summarizer_config_from_settings_maps_throttle_fields() -> None:
    settings = SummarizerSettings(
        debounce_s=1.5,
        min_new_events=4,
        max_tokens=42,
        watcher_token_ceiling=500,
    )
    config = SummarizerConfig.from_settings(settings)
    assert config.debounce_s == 1.5
    assert config.min_new_events == 4
    assert config.max_tokens == 42
    assert config.watcher_token_ceiling == 500


@pytest.mark.asyncio
async def test_summarizer_failing_client_falls_back_with_warning() -> None:
    status = initial_status("a1", "claude")
    summarizer = RollingSummarizer(
        SummarizerConfig(debounce_s=0, min_new_events=1),
        clock=lambda: 10,
    )
    result = await summarizer.summarize(status, _events(), client=FailingClient())
    assert result.used_llm is False
    assert result.warning == "llm_error:no key"
    assert result.text


class _MonitorAdapter:
    """NDJSON adapter emitting enough significant events to trip the summarizer."""

    name = "jsonline"
    prompt_mode = "stdin"
    seed_paths: list[str] = []
    capabilities = Capabilities(stream_json=True)

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        script = (
            "import json;"
            "print(json.dumps({'kind':'started','session_id':'s1'}), flush=True);"
            "print(json.dumps({'kind':'tool_use','text':'ran pytest'}), flush=True);"
            "print(json.dumps({'kind':'tool_result','text':'tests passed'}), flush=True);"
            "print(json.dumps({'kind':'completed','text':'done'}), flush=True)"
        )
        return BuiltCommand(command=sys.executable, args=["-c", script], stdin_text=spec.prompt)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        raw = json.loads(raw_line)
        return YanShiEvent(
            kind=EventKind(raw["kind"]),
            text=raw.get("text", ""),
            session_id=raw.get("session_id"),
            is_error=False if raw["kind"] == "completed" else None,
            raw=raw_line,
        )

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        return RunResult(
            agent_id="placeholder",
            cli=self.name,
            state=AgentState.SUCCEEDED if outcome.exit_code == 0 else AgentState.FAILED,
            is_error=outcome.exit_code != 0,
            reply="done",
            exit_code=outcome.exit_code,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        value = ev.get("session_id")
        return value if isinstance(value, str) else None


class _LiveClient:
    async def summarize(self, prompt: str) -> str:
        return "live summary"


class _BrokenClient:
    async def summarize(self, prompt: str) -> str:
        raise RuntimeError("watcher down")


def _monitor_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(_MonitorAdapter())
    return registry


def _active_summarizer() -> RollingSummarizer:
    return RollingSummarizer(SummarizerConfig(debounce_s=0.0, min_new_events=1))


@pytest.mark.asyncio
async def test_monitor_persists_live_rolling_summary(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    kernel = MonitorKernel(
        registry=_monitor_registry(),
        store=store,
        summarizer=_active_summarizer(),
        summary_client=_LiveClient(),
    )
    result = await kernel.run(RunSpec(cli="jsonline", prompt="hi"), skip_preflight=True)
    persisted = store.read_status(result.agent_id)
    assert persisted.rolling_summary == "live summary"


@pytest.mark.asyncio
async def test_monitor_rolling_summary_degrades_to_fallback(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    kernel = MonitorKernel(
        registry=_monitor_registry(),
        store=store,
        summarizer=_active_summarizer(),
        summary_client=_BrokenClient(),
    )
    result = await kernel.run(RunSpec(cli="jsonline", prompt="hi"), skip_preflight=True)
    persisted = store.read_status(result.agent_id)
    assert persisted.rolling_summary
    assert persisted.rolling_summary != "live summary"


@pytest.mark.asyncio
async def test_monitor_without_summarizer_leaves_summary_empty(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    kernel = MonitorKernel(registry=_monitor_registry(), store=store)
    result = await kernel.run(RunSpec(cli="jsonline", prompt="hi"), skip_preflight=True)
    persisted = store.read_status(result.agent_id)
    assert persisted.rolling_summary == ""
