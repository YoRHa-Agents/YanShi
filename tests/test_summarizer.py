from __future__ import annotations

import pytest

from yanshi.contracts import EventKind, YanShiEvent
from yanshi.reducer import initial_status
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
