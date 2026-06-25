"""Advisory rolling summarizer with deterministic fallback."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from yanshi.contracts import AgentStatus, EventKind, Usage, YanShiEvent

if TYPE_CHECKING:
    # config does not import summarizer, so this is cycle-free; kept under
    # TYPE_CHECKING to keep the advisory summarizer free of a runtime config dep.
    from yanshi.config import SummarizerSettings


class SummaryClient(Protocol):
    """Minimal async LLM client protocol."""

    async def summarize(self, prompt: str) -> str:
        """Return a short summary for structured event text."""


@dataclass(frozen=True)
class SummarizerConfig:
    """Summarizer throttling and budget settings."""

    debounce_s: float = 5.0
    min_new_events: int = 2
    max_tokens: int = 150
    watcher_token_ceiling: int = 1_000

    @classmethod
    def from_settings(cls, settings: SummarizerSettings) -> SummarizerConfig:
        """Map a ``SummarizerSettings``-shaped object into a throttle/budget config.

        Only the four throttle/budget fields are mapped; ``enabled``/``cli``/
        ``model``/``timeout_s`` are wiring concerns handled by the caller.
        """

        return cls(
            debounce_s=settings.debounce_s,
            min_new_events=settings.min_new_events,
            max_tokens=settings.max_tokens,
            watcher_token_ceiling=settings.watcher_token_ceiling,
        )


class SummaryResult(BaseModel):
    """Summary output with provenance."""

    model_config = ConfigDict(extra="forbid")

    text: str
    used_llm: bool
    usage: Usage = Usage()
    warning: str | None = None


class RollingSummarizer:
    """Throttle advisory summaries and fall back deterministically."""

    def __init__(
        self,
        config: SummarizerConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or SummarizerConfig()
        self.clock = clock or time.time
        self.last_summary_at = 0.0
        self.last_event_count = 0
        self.watcher_usage = Usage()

    def should_trigger(self, events: list[YanShiEvent], *, now: float | None = None) -> bool:
        """Return True when debounce and new-significant-event thresholds are met."""

        current_time = self.clock() if now is None else now
        new_events = len(_significant(events)[self.last_event_count :])
        return (
            current_time - self.last_summary_at >= self.config.debounce_s
            and new_events >= self.config.min_new_events
        )

    async def summarize(
        self,
        status: AgentStatus,
        events: list[YanShiEvent],
        *,
        client: SummaryClient | None = None,
        now: float | None = None,
    ) -> SummaryResult:
        """Produce an advisory summary without affecting deterministic status fields."""

        significant = _significant(events)
        current_time = self.clock() if now is None else now
        if not self.should_trigger(events, now=current_time):
            return SummaryResult(text=status.rolling_summary, used_llm=False, warning="throttled")
        prompt = _structured_prompt(status, significant)
        if self.watcher_usage.total >= self.config.watcher_token_ceiling:
            return self._fallback(significant, current_time, warning="watcher_budget_exceeded")
        if client is None:
            return self._fallback(significant, current_time, warning="llm_unavailable")
        try:
            text = await client.summarize(prompt)
        except Exception as exc:  # noqa: BLE001 - failures are surfaced as fallback warnings.
            return self._fallback(significant, current_time, warning=f"llm_error:{exc}")
        bounded = _bound_text(text, self.config.max_tokens)
        self.last_summary_at = current_time
        self.last_event_count = len(significant)
        usage = Usage(input_tokens=len(prompt.split()), output_tokens=len(bounded.split()))
        self.watcher_usage = Usage(
            input_tokens=self.watcher_usage.input_tokens + usage.input_tokens,
            output_tokens=self.watcher_usage.output_tokens + usage.output_tokens,
        )
        return SummaryResult(text=bounded, used_llm=True, usage=usage)

    def _fallback(
        self,
        significant: list[YanShiEvent],
        now: float,
        *,
        warning: str,
    ) -> SummaryResult:
        text = " · ".join(_event_summary(event) for event in significant[-5:])
        bounded = _bound_text(text or "No significant events yet.", self.config.max_tokens)
        self.last_summary_at = now
        self.last_event_count = len(significant)
        return SummaryResult(text=bounded, used_llm=False, warning=warning)


def _significant(events: list[YanShiEvent]) -> list[YanShiEvent]:
    significant_kinds = {
        EventKind.TOOL_USE,
        EventKind.TOOL_RESULT,
        EventKind.ERROR,
        EventKind.COMPLETED,
        EventKind.FILE_CHANGE,
    }
    return [event for event in events if event.kind in significant_kinds]


def _structured_prompt(status: AgentStatus, events: list[YanShiEvent]) -> str:
    lines = [
        f"agent_id={status.agent_id}",
        f"cli={status.cli}",
        f"state={status.state.value}",
        "events:",
    ]
    lines.extend(f"- {event.kind.value}: {_event_summary(event)}" for event in events[-10:])
    return "\n".join(lines)


def _event_summary(event: YanShiEvent) -> str:
    if event.err:
        return event.err
    if event.text:
        return event.text
    if event.usage:
        return f"usage={event.usage.total}"
    return event.kind.value


def _bound_text(text: str, max_tokens: int) -> str:
    words = text.split()
    if len(words) <= max_tokens:
        return text
    return " ".join(words[:max_tokens])
