"""Public dispatch API for M0 blocking runs."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass

from yanshi.contracts import TERMINAL_STATES, AgentState, AgentStatus, RunResult, RunSpec
from yanshi.monitor import MonitorKernel
from yanshi.preflight import PreflightResult, preflight_adapter
from yanshi.preflight import doctor as run_doctor
from yanshi.registry import AdapterRegistry, default_registry
from yanshi.runner import run_blocking
from yanshi.store import StatusStore


@dataclass(frozen=True)
class DispatchHandle:
    """Handle returned by background library dispatch."""

    agent_id: str
    task: asyncio.Task[RunResult]


_BACKGROUND_TASKS: dict[str, asyncio.Task[RunResult]] = {}


def dispatch(
    spec: RunSpec,
    *,
    registry: AdapterRegistry | None = None,
    skip_preflight: bool = False,
) -> RunResult:
    """Dispatch a single task and block until the CLI exits."""

    effective_registry = registry or default_registry()
    adapter = effective_registry.get(spec.cli)
    if not skip_preflight:
        preflight_adapter(adapter, env=spec.env).require_ok()
    command = adapter.build_command(spec)
    outcome = run_blocking(command, timeout_s=spec.timeout_s)
    return adapter.parse_result(outcome)


async def dispatch_wait(
    spec: RunSpec,
    *,
    registry: AdapterRegistry | None = None,
    store: StatusStore | None = None,
    skip_preflight: bool = False,
) -> RunResult:
    """Entry B: run the shared monitor kernel inline until terminal result."""

    kernel = MonitorKernel(registry=registry, store=store)
    return await kernel.run(spec, skip_preflight=skip_preflight)


def dispatch_background(
    spec: RunSpec,
    *,
    registry: AdapterRegistry | None = None,
    store: StatusStore | None = None,
    skip_preflight: bool = False,
) -> DispatchHandle:
    """Entry A: spawn a background monitor task in the current event loop."""

    kernel = MonitorKernel(registry=registry, store=store)
    agent_id = f"ys-{os.getpid()}-{time.time_ns()}"
    task = asyncio.create_task(
        kernel.run(spec, agent_id=agent_id, skip_preflight=skip_preflight),
        name=f"yanshi-{agent_id}",
    )
    _BACKGROUND_TASKS[agent_id] = task

    def _cleanup(done: asyncio.Task[RunResult]) -> None:
        _ = done
        _BACKGROUND_TASKS.pop(agent_id, None)

    task.add_done_callback(_cleanup)
    return DispatchHandle(agent_id=agent_id, task=task)


def status(agent_id: str, *, store: StatusStore | None = None) -> AgentStatus:
    """Pure-disk status read."""

    return (store or StatusStore()).read_status(agent_id)


def summary(agent_id: str, *, store: StatusStore | None = None) -> str:
    """Pure-disk summary read."""

    current = status(agent_id, store=store)
    if current.rolling_summary:
        return current.rolling_summary
    return current.last_event.summary or ""


async def wait(
    agent_id: str,
    *,
    store: StatusStore | None = None,
    timeout_s: float | None = None,
    poll_interval_s: float = 0.1,
) -> AgentStatus:
    """Poll disk status until terminal state or timeout."""

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    effective_store = store or StatusStore()
    while True:
        try:
            current = effective_store.read_status(agent_id)
        except FileNotFoundError:
            if deadline is not None and time.monotonic() >= deadline:
                raise
            await asyncio.sleep(poll_interval_s)
            continue
        if current.state in TERMINAL_STATES:
            return current
        if deadline is not None and time.monotonic() >= deadline:
            return current
        await asyncio.sleep(poll_interval_s)


def list_agents(*, store: StatusStore | None = None) -> list[str]:
    """Pure-disk agent listing."""

    return (store or StatusStore()).list_agent_ids()


def cancel(agent_id: str, *, store: StatusStore | None = None) -> AgentStatus:
    """Cancel a running agent by task handle or recorded child pid."""

    task = _BACKGROUND_TASKS.get(agent_id)
    if task is not None:
        task.cancel()
    effective_store = store or StatusStore()
    try:
        current = effective_store.read_status(agent_id)
    except FileNotFoundError:
        current = AgentStatus(agent_id=agent_id, cli="unknown", state=AgentState.CANCELLED)
    if current.child_pid is not None:
        _signal_pid(current.child_pid, signal.SIGINT)
    cancelled = current.model_copy(deep=True)
    cancelled.state = AgentState.CANCELLED
    cancelled.updated_at = time.time()
    effective_store.write_status(cancelled)
    return cancelled


def doctor(registry: AdapterRegistry | None = None) -> list[PreflightResult]:
    """Run adapter preflight checks."""

    return run_doctor(registry)


def _signal_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
