from __future__ import annotations

from yanshi.contracts import EventKind, Usage, YanShiEvent
from yanshi.reducer import StatusReducer, initial_status


def test_reducer_tracks_fsm_counters_usage_and_cost() -> None:
    reducer = StatusReducer()
    status = initial_status("a1", "claude", now=1.0)
    status = reducer.apply(status, YanShiEvent(kind=EventKind.STARTED, session_id="s1"), now=2.0)
    status = reducer.apply(
        status,
        YanShiEvent(kind=EventKind.ASSISTANT_TEXT, text="working"),
        now=3.0,
    )
    status = reducer.apply(status, YanShiEvent(kind=EventKind.TOOL_USE, text="pwd"), now=4.0)
    status = reducer.apply(status, YanShiEvent(kind=EventKind.TOOL_RESULT, text="/tmp"), now=5.0)
    status = reducer.apply(
        status,
        YanShiEvent(
            kind=EventKind.COMPLETED,
            text="done",
            usage=Usage(input_tokens=2, output_tokens=3),
            cost_usd=0.01,
            is_error=False,
        ),
        now=6.0,
    )

    assert status.state == "succeeded"
    assert status.session_id == "s1"
    assert status.counters["events"] == 5
    assert status.counters["tool_calls"] == 1
    assert status.usage.total == 5
    assert status.cost_usd == 0.01
    assert status.pricing_status == "native"
    assert status.progress_pct is None


def test_reducer_records_errors_and_unknown_events() -> None:
    reducer = StatusReducer()
    status = initial_status("a1", "claude")
    status = reducer.apply(
        status,
        YanShiEvent(kind=EventKind.UNKNOWN, text="new-event"),
    )
    status = reducer.apply(
        status,
        YanShiEvent(kind=EventKind.ERROR, err="HTTP 429 rate limit"),
    )
    assert status.state == "failed"
    assert status.counters["unknown_events"] == 1
    assert status.errors[0].category == "rate_limit"


def test_reducer_rejects_transition_out_of_terminal_state() -> None:
    reducer = StatusReducer()
    status = initial_status("a1", "claude")
    status = reducer.apply(status, YanShiEvent(kind=EventKind.COMPLETED, is_error=False))
    status = reducer.apply(status, YanShiEvent(kind=EventKind.ASSISTANT_TEXT, text="late"))
    assert status.state == "succeeded"
    assert status.errors
    assert "terminal" in status.errors[0].message
