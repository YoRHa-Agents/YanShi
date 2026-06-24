"""Deterministic status reducer."""

from __future__ import annotations

import time
from collections.abc import Mapping

from yanshi.contracts import (
    TERMINAL_STATES,
    AgentState,
    AgentStatus,
    ErrorRecord,
    EventKind,
    LastEvent,
    PricingStatus,
    Usage,
    YanShiEvent,
)
from yanshi.errors import classify_error_text

_LEGAL_TRANSITIONS: Mapping[AgentState, frozenset[AgentState]] = {
    AgentState.PENDING: frozenset(
        {AgentState.STARTING, AgentState.RUNNING, AgentState.SUCCEEDED, AgentState.FAILED}
    ),
    AgentState.STARTING: frozenset({AgentState.RUNNING, AgentState.FAILED, AgentState.CANCELLED}),
    AgentState.RUNNING: frozenset(
        {
            AgentState.WAITING_RATE_LIMIT,
            AgentState.WAITING_TOOL,
            AgentState.SUCCEEDED,
            AgentState.FAILED,
            AgentState.STALLED,
            AgentState.CANCELLED,
            AgentState.KILLED,
        }
    ),
    AgentState.WAITING_RATE_LIMIT: frozenset(
        {AgentState.RUNNING, AgentState.FAILED, AgentState.CANCELLED, AgentState.KILLED}
    ),
    AgentState.WAITING_TOOL: frozenset(
        {AgentState.RUNNING, AgentState.FAILED, AgentState.CANCELLED, AgentState.KILLED}
    ),
    AgentState.SUCCEEDED: frozenset(),
    AgentState.FAILED: frozenset(),
    AgentState.STALLED: frozenset(),
    AgentState.CANCELLED: frozenset(),
    AgentState.KILLED: frozenset(),
}


def initial_status(agent_id: str, cli: str, *, now: float | None = None) -> AgentStatus:
    """Create an initial pending status."""

    ts = time.time() if now is None else now
    return AgentStatus(agent_id=agent_id, cli=cli, started_at=ts, updated_at=ts)


class StatusReducer:
    """Pure reducer for normalized events."""

    def apply(
        self,
        status: AgentStatus,
        event: YanShiEvent,
        *,
        now: float | None = None,
    ) -> AgentStatus:
        """Apply one event without mutating the input status."""

        ts = time.time() if now is None else now
        next_status = status.model_copy(deep=True)
        _increment(next_status.counters, "events")
        _increment(next_status.counters, f"kind_{event.kind.value}")
        next_status.updated_at = ts
        next_status.last_event = LastEvent(
            kind=event.kind,
            summary=_summarize(event),
            ts=event.ts or ts,
        )
        if event.session_id:
            next_status.session_id = event.session_id
        if event.usage:
            next_status.usage = _add_usage(next_status.usage, event.usage)
        if event.cost_usd is not None:
            next_status.cost_usd = (next_status.cost_usd or 0.0) + event.cost_usd
            next_status.pricing_status = PricingStatus.NATIVE

        target = _target_state_for_event(next_status.state, event)
        if target is not None:
            _transition(next_status, target)

        if event.kind == EventKind.TOOL_USE:
            _increment(next_status.counters, "tool_calls")
        elif event.kind == EventKind.FILE_CHANGE:
            _increment(next_status.counters, "files_changed")
        elif event.kind == EventKind.UNKNOWN:
            _increment(next_status.counters, "unknown_events")
        elif event.kind == EventKind.ERROR:
            category = classify_error_text(event.err or event.text).value
            next_status.errors.append(
                ErrorRecord(category=category, message=event.err or event.text, fatal=True)
            )

        return next_status


def _transition(status: AgentStatus, target: AgentState) -> None:
    if status.state == target:
        return
    if status.state in TERMINAL_STATES:
        status.errors.append(
            ErrorRecord(
                category="invalid_request",
                message=f"illegal transition from terminal {status.state} to {target}",
                fatal=False,
            )
        )
        return
    if target not in _LEGAL_TRANSITIONS[status.state]:
        status.errors.append(
            ErrorRecord(
                category="invalid_request",
                message=f"illegal transition {status.state}->{target}",
                fatal=False,
            )
        )
        return
    status.state = target


def _target_state_for_event(current: AgentState, event: YanShiEvent) -> AgentState | None:
    if event.kind == EventKind.STARTED:
        return AgentState.STARTING if current == AgentState.PENDING else AgentState.RUNNING
    if event.kind in {EventKind.ASSISTANT_TEXT, EventKind.REASONING, EventKind.USAGE}:
        return AgentState.RUNNING
    if event.kind == EventKind.TOOL_USE:
        return AgentState.WAITING_TOOL
    if event.kind == EventKind.TOOL_RESULT:
        return AgentState.RUNNING
    if event.kind == EventKind.ERROR:
        return AgentState.FAILED
    if event.kind == EventKind.COMPLETED:
        return AgentState.FAILED if event.is_error else AgentState.SUCCEEDED
    return None


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
    )


def _increment(counters: dict[str, int], key: str) -> None:
    counters[key] = counters.get(key, 0) + 1


def _summarize(event: YanShiEvent) -> str:
    if event.err:
        return event.err
    if event.text:
        return event.text[:200]
    if event.usage:
        return f"usage total={event.usage.total}"
    return event.kind.value
