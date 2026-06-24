from __future__ import annotations

from pathlib import Path

from yanshi.adapters.base import Adapter
from yanshi.adapters.claude import ClaudeAdapter
from yanshi.adapters.codex import CodexAdapter
from yanshi.adapters.cursor import CursorAdapter
from yanshi.contracts import AgentStatus, EventKind, RawOutcome
from yanshi.reducer import StatusReducer, initial_status


def _reduce_fixture(
    adapter: Adapter, fixture: Path, cli: str
) -> tuple[list[EventKind], AgentStatus]:
    events = [
        event
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if (event := adapter.parse_event(line)) is not None
    ]
    status = initial_status("a1", cli)
    reducer = StatusReducer()
    for event in events:
        status = reducer.apply(status, event)
    return [event.kind for event in events], status


def test_claude_fixture_normalizes_to_expected_sequence() -> None:
    fixture = Path("tests/fixtures/claude_stream.ndjson")
    adapter = ClaudeAdapter()
    events = [
        event
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if (event := adapter.parse_event(line)) is not None
    ]
    assert [event.kind for event in events] == [
        EventKind.STARTED,
        EventKind.ASSISTANT_TEXT,
        EventKind.TOOL_USE,
        EventKind.TOOL_RESULT,
        EventKind.COMPLETED,
    ]
    status = initial_status("a1", "claude")
    reducer = StatusReducer()
    for event in events:
        status = reducer.apply(status, event)
    assert status.state == "succeeded"
    assert status.session_id == "claude-session-1"
    assert status.usage.total == 15
    assert status.cost_usd == 0.001
    assert all(event.raw for event in events)
    assert "project" not in status.model_dump_json()


def test_codex_live_fixture_normalizes_snake_case_items_and_reasoning_usage() -> None:
    """Codex 0.140.0 emits snake_case item types and reasoning_output_tokens."""

    kinds, status = _reduce_fixture(
        CodexAdapter(), Path("tests/fixtures/codex_stream.ndjson"), "codex"
    )
    assert EventKind.ASSISTANT_TEXT in kinds
    assert EventKind.TOOL_USE in kinds
    assert status.state == "succeeded"
    assert status.session_id == "codex-thread-live-1"
    # input 16442 + cached 3456 + output 21 + reasoning_output 14.
    assert status.usage.total == 19933
    assert status.usage.reasoning_tokens == 14
    assert status.counters.get("tool_calls") == 1

    result = CodexAdapter().parse_result(
        _outcome(Path("tests/fixtures/codex_stream.ndjson"))
    )
    assert result.reply == "done"
    assert result.usage.total == 19933


def test_cursor_live_fixture_normalizes_nested_text_and_camelcase_usage() -> None:
    """cursor-agent nests assistant text and reports camelCase usage."""

    kinds, status = _reduce_fixture(
        CursorAdapter(), Path("tests/fixtures/cursor_stream.ndjson"), "cursor"
    )
    assert EventKind.ASSISTANT_TEXT in kinds
    assert status.state == "succeeded"
    assert status.session_id == "cursor-session-live-1"
    # inputTokens 22865 + cacheReadTokens 0 + outputTokens 121.
    assert status.usage.total == 22986

    result = CursorAdapter().parse_result(
        _outcome(Path("tests/fixtures/cursor_stream.ndjson"))
    )
    assert result.reply == "pong"
    assert result.usage.input_tokens == 22865
    assert result.usage.output_tokens == 121


def _outcome(fixture: Path) -> RawOutcome:
    return RawOutcome(
        command="cli",
        exit_code=0,
        stdout=fixture.read_text(encoding="utf-8"),
    )
