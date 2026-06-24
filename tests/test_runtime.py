from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

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
from yanshi.dispatch import (
    cancel,
    dispatch_background,
    dispatch_wait,
    list_agents,
    status,
    summary,
    wait,
)
from yanshi.registry import AdapterRegistry
from yanshi.store import StatusStore


class JsonLineAdapter:
    name = "jsonline"
    prompt_mode = "stdin"
    seed_paths: list[str] = []
    capabilities = Capabilities(stream_json=True)

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        script = (
            "import json, sys, time;"
            "print(json.dumps({'kind':'started','session_id':'s1'}), flush=True);"
            "print(json.dumps({'kind':'assistant_text','text':sys.stdin.read()}), flush=True);"
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


class SleepAdapter(JsonLineAdapter):
    name = "sleepy"

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        script = (
            "import json, time;"
            "print(json.dumps({'kind':'started','session_id':'s2'}), flush=True);"
            "time.sleep(10)"
        )
        return BuiltCommand(command=sys.executable, args=["-c", script])


def _registry(*adapters: Any) -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


@pytest.mark.asyncio
async def test_dispatch_wait_runs_monitor_and_persists_status(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    result = await dispatch_wait(
        RunSpec(cli="jsonline", prompt="hello"),
        registry=_registry(JsonLineAdapter()),
        store=store,
        skip_preflight=True,
    )
    assert result.state == "succeeded"
    persisted = status(result.agent_id, store=store)
    assert persisted.state == "succeeded"
    assert summary(result.agent_id, store=store) == "done"
    assert list_agents(store=store) == [result.agent_id]


@pytest.mark.asyncio
async def test_background_dispatch_wait_and_cancel(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    handle = dispatch_background(
        RunSpec(cli="sleepy", prompt="sleep"),
        registry=_registry(SleepAdapter()),
        store=store,
        skip_preflight=True,
    )
    child_pid: int | None = None
    first_state = ""
    cancelled_state = ""
    try:
        first = await wait(handle.agent_id, store=store, timeout_s=2)
        first_state = first.state.value
        child_pid = first.child_pid
        cancelled = cancel(handle.agent_id, store=store)
        cancelled_state = cancelled.state.value
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(handle.task, timeout=3)
    finally:
        if not handle.task.done():
            cancel(handle.agent_id, store=store)
            handle.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(handle.task, timeout=3)
    assert first_state in {"running", "starting"}
    assert cancelled_state == "cancelled"
    assert child_pid is not None
    assert not _pid_alive(child_pid)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
