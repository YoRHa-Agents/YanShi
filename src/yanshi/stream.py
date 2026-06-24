"""Stream pumping and tolerant event parsing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from yanshi.adapters.base import Adapter
from yanshi.contracts import EventKind, YanShiEvent

StreamName = Literal["stdout", "stderr"]


class PumpedEvent(BaseModel):
    """A raw stream line and its optional normalized event."""

    model_config = ConfigDict(extra="forbid")

    source: StreamName
    raw: str
    event: YanShiEvent | None = None
    parse_error: str | None = None


class StreamPump:
    """Read stdout/stderr concurrently and parse NDJSON lines through an adapter."""

    async def pump(
        self,
        stdout: AsyncIterable[str],
        stderr: AsyncIterable[str],
        adapter: Adapter,
        *,
        max_lines: int = 100_000,
    ) -> AsyncIterator[PumpedEvent]:
        """Yield parsed events from both streams without letting one pipe block the other."""

        queue: asyncio.Queue[PumpedEvent | None] = asyncio.Queue(maxsize=1024)

        async def consume(source: StreamName, lines: AsyncIterable[str]) -> None:
            count = 0
            async for line in lines:
                count += 1
                if count > max_lines:
                    await queue.put(
                        PumpedEvent(
                            source=source,
                            raw="",
                            event=YanShiEvent(
                                kind=EventKind.ERROR,
                                err=f"max_lines exceeded for {source}",
                            ),
                            parse_error="max_lines_exceeded",
                        )
                    )
                    break
                await queue.put(_parse_line(source, line, adapter))
            await queue.put(None)

        pending_readers = 2
        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(consume("stdout", stdout))
            task_group.create_task(consume("stderr", stderr))
            while pending_readers:
                item = await queue.get()
                if item is None:
                    pending_readers -= 1
                    continue
                yield item


def parse_line(source: StreamName, line: str, adapter: Adapter) -> PumpedEvent:
    """Parse a single stream line with explicit parse-error reporting."""

    return _parse_line(source, line, adapter)


def _parse_line(source: StreamName, line: str, adapter: Adapter) -> PumpedEvent:
    raw = line.rstrip("\n")
    try:
        event = adapter.parse_event(raw)
    except Exception as exc:  # noqa: BLE001 - parse errors are audited as events.
        return PumpedEvent(
            source=source,
            raw=raw,
            event=YanShiEvent(kind=EventKind.UNKNOWN, text="parse_error", raw=raw),
            parse_error=str(exc),
        )
    return PumpedEvent(source=source, raw=raw, event=event)
