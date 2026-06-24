"""Shared monitor kernel for library and CLI entrypoints."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
import uuid
from collections.abc import AsyncIterator

from yanshi.contracts import (
    AgentState,
    AgentStatus,
    RawOutcome,
    RunResult,
    RunSpec,
    YanShiEvent,
)
from yanshi.logsink import RawLogSink
from yanshi.preflight import preflight_adapter
from yanshi.reducer import StatusReducer, initial_status
from yanshi.registry import AdapterRegistry, default_registry
from yanshi.runner import build_child_env
from yanshi.store import StatusStore
from yanshi.stream import StreamPump
from yanshi.summarizer import RollingSummarizer, SummaryClient

_LOGGER = logging.getLogger(__name__)

# Bounded ceiling for the finalize wait on an in-flight summary task (G4.4/G4.5).
_SUMMARY_FINALIZE_TIMEOUT_S = 10.0


class MonitorKernel:
    """One monitor kernel used by library and CLI blocking entrypoints."""

    def __init__(
        self,
        *,
        registry: AdapterRegistry | None = None,
        store: StatusStore | None = None,
        summarizer: RollingSummarizer | None = None,
        summary_client: SummaryClient | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.store = store or StatusStore()
        # Advisory rolling summaries are ACTIVE only when BOTH are provided.
        self.summarizer = summarizer
        self.summary_client = summary_client
        self._rolling_summary = ""

    async def run(
        self,
        spec: RunSpec,
        *,
        agent_id: str | None = None,
        skip_preflight: bool = False,
    ) -> RunResult:
        """Spawn, monitor, persist, and finalize one run."""

        effective_agent_id = agent_id or str(uuid.uuid4())
        adapter = self.registry.get(spec.cli)
        if not skip_preflight:
            preflight_adapter(adapter, env=spec.env).require_ok()
        command = adapter.build_command(spec)
        status = initial_status(effective_agent_id, spec.cli)
        status.owner_pid = os.getpid()
        status.state = AgentState.STARTING
        self.store.write_status(status)

        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            command.command,
            *command.args,
            stdin=asyncio.subprocess.PIPE if command.stdin_text or command.stdin_file else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=command.cwd,
            env=build_child_env(command.env),
            start_new_session=os.name != "nt",
        )
        status.child_pid = proc.pid
        status.state = AgentState.RUNNING
        self.store.write_status(status)
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        log_sink = RawLogSink(self.store.stream_path(effective_agent_id))
        reducer = StatusReducer()
        events: list[YanShiEvent] = []
        self._rolling_summary = ""
        summary_task: asyncio.Task[None] | None = None
        summary_cancelled = False

        async def stdout_iter() -> AsyncIterator[str]:
            async for line in _read_stream(proc.stdout):
                stdout_lines.append(line)
                yield line

        async def stderr_iter() -> AsyncIterator[str]:
            async for line in _read_stream(proc.stderr):
                stderr_lines.append(line)
                yield line

        stdin_task = asyncio.create_task(_feed_stdin(proc, command.stdin_text, command.stdin_file))
        timed_out = False
        timeout_context = (
            asyncio.timeout(spec.timeout_s) if spec.timeout_s else _null_async_context()
        )
        try:
            async with timeout_context:
                async for pumped in StreamPump().pump(stdout_iter(), stderr_iter(), adapter):
                    if pumped.raw:
                        log_sink.append(pumped.raw)
                    if pumped.event is not None:
                        events.append(pumped.event)
                        status = reducer.apply(status, pumped.event)
                        status.agent_id = effective_agent_id
                        status.owner_pid = os.getpid()
                        status.child_pid = proc.pid
                        # Mirror the latest advisory summary; the background task
                        # never writes status directly, avoiding lost-update races.
                        status.rolling_summary = self._rolling_summary
                        self.store.write_status(status)
                        if (
                            self.summarizer is not None
                            and self.summary_client is not None
                            and self.summarizer.should_trigger(events)
                            and (summary_task is None or summary_task.done())
                        ):
                            summary_task = asyncio.create_task(
                                self._summarize(list(events), status.model_copy(deep=True))
                            )
        except TimeoutError:
            timed_out = True
            await terminate_process(proc, final_signal=signal.SIGKILL)
        except asyncio.CancelledError:
            summary_cancelled = True
            await terminate_process(proc, final_signal=signal.SIGKILL)
            raise
        finally:
            await _settle_task(stdin_task)
            # Never leave the watcher running (G4.4): cancel promptly when the run
            # is torn down, otherwise bounded-wait for the latest summary.
            if summary_cancelled:
                await self._cancel_summary_task(summary_task)
            else:
                await self._await_summary_task(summary_task)

        exit_code = await proc.wait()
        duration_ms = int((time.monotonic() - started) * 1000)
        outcome = RawOutcome(
            command=command.command,
            args=command.args,
            exit_code=exit_code,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            duration_ms=duration_ms,
            timed_out=timed_out,
        )
        result = adapter.parse_result(outcome).model_copy(
            update={
                "agent_id": effective_agent_id,
                "state": AgentState.FAILED if timed_out else status.state,
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "log_dir": str(self.store.agent_dir(effective_agent_id)),
            }
        )
        if timed_out:
            result.is_error = True
            result.error_category = "unknown"
        self.store.write_result(result)
        final_status = status.model_copy(deep=True)
        final_status.state = result.state
        final_status.updated_at = time.time()
        final_status.rolling_summary = self._rolling_summary
        self.store.write_status(final_status)
        return result

    async def _summarize(
        self,
        events_snapshot: list[YanShiEvent],
        status_snapshot: AgentStatus,
    ) -> None:
        """Run one advisory summary in the background; update the mirror, never raise.

        ``RollingSummarizer`` already degrades to a deterministic fallback on
        client failure, so this only needs a defensive guard against unexpected
        errors, which are recorded (No Silent Failures) but never propagated --
        the advisory watcher must never abort the monitored run (G2.7).
        """

        if self.summarizer is None:
            return
        try:
            result = await self.summarizer.summarize(
                status_snapshot, events_snapshot, client=self.summary_client
            )
        except Exception:  # noqa: BLE001 - advisory watcher must never abort the run (G2.7).
            _LOGGER.warning("rolling summary task failed", exc_info=True)
            return
        self._rolling_summary = result.text

    async def _await_summary_task(self, task: asyncio.Task[None] | None) -> None:
        """Bounded-wait an in-flight summary task during finalize (G4.4/G4.5)."""

        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_SUMMARY_FINALIZE_TIMEOUT_S)
        except TimeoutError:
            await self._cancel_summary_task(task)
        except asyncio.CancelledError:
            await self._cancel_summary_task(task)
            raise

    async def _cancel_summary_task(self, task: asyncio.Task[None] | None) -> None:
        """Cancel and reap a summary task so none outlives the run (G4.4)."""

        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def terminate_process(
    proc: asyncio.subprocess.Process,
    *,
    final_signal: signal.Signals = signal.SIGTERM,
) -> None:
    """Terminate a subprocess without using process-name killing."""

    if proc.returncode is not None:
        return
    if os.name != "nt":
        with _suppress_process_lookup():
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    else:
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
        return
    except TimeoutError:
        pass
    if os.name != "nt":
        with _suppress_process_lookup():
            os.killpg(os.getpgid(proc.pid), final_signal)
    else:
        proc.kill()
    await proc.wait()


async def _read_stream(stream: asyncio.StreamReader | None) -> AsyncIterator[str]:
    if stream is None:
        return
    while True:
        raw = await stream.readline()
        if not raw:
            break
        yield raw.decode("utf-8", errors="replace")


async def _feed_stdin(
    proc: asyncio.subprocess.Process,
    stdin_text: str | None,
    stdin_file: str | None,
) -> None:
    if proc.stdin is None:
        return
    text = stdin_text
    if stdin_file is not None:
        text = await asyncio.to_thread(_read_text_file, stdin_file)
    if text is None:
        text = ""
    try:
        proc.stdin.write(text.encode("utf-8"))
        await proc.stdin.drain()
    except OSError:
        return
    finally:
        proc.stdin.close()
        await proc.stdin.wait_closed()


async def _settle_task(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, OSError):
        return


class _null_async_context:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        return False


class _suppress_process_lookup:
    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        return exc_type is ProcessLookupError


def _read_text_file(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()
