from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from yanshi.adapters.claude import ClaudeAdapter
from yanshi.contracts import EventKind
from yanshi.stream import StreamPump, parse_line


async def _aiter(values: list[str]) -> AsyncIterator[str]:
    for value in values:
        await asyncio.sleep(0)
        yield value


def test_parse_line_turns_invalid_json_into_unknown_event() -> None:
    pumped = parse_line("stdout", "not json", ClaudeAdapter())
    assert pumped.parse_error is not None
    assert pumped.event is not None
    assert pumped.event.kind == EventKind.UNKNOWN


@pytest.mark.asyncio
async def test_stream_pump_reads_stdout_and_stderr_without_dropping() -> None:
    stdout = [f'{{"type":"system","subtype":"init","session_id":"s{i}"}}\n' for i in range(50)]
    stderr = ["plain stderr\n" for _ in range(50)]
    events = [
        item
        async for item in StreamPump().pump(
            _aiter(stdout),
            _aiter(stderr),
            ClaudeAdapter(),
            max_lines=1_000,
        )
    ]
    assert len(events) == 100
    assert sum(1 for item in events if item.source == "stdout") == 50
    assert sum(1 for item in events if item.parse_error is not None) == 50


@pytest.mark.asyncio
async def test_stream_pump_emits_max_lines_error() -> None:
    events = [
        item
        async for item in StreamPump().pump(
            _aiter(['{"type":"system"}\n', '{"type":"system"}\n']),
            _aiter([]),
            ClaudeAdapter(),
            max_lines=1,
        )
    ]
    assert any(item.parse_error == "max_lines_exceeded" for item in events)
