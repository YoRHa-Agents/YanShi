"""Fan-out dispatch and deterministic fleet aggregation."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from yanshi.contracts import (
    AgentState,
    AgentStatus,
    ErrorRecord,
    FleetStatus,
    RunResult,
    RunSpec,
    Usage,
)
from yanshi.monitor import MonitorKernel
from yanshi.registry import AdapterRegistry
from yanshi.store import StatusStore


async def dispatch_many(
    specs: list[RunSpec],
    *,
    max_parallel: int = 4,
    registry: AdapterRegistry | None = None,
    store: StatusStore | None = None,
    skip_preflight: bool = False,
) -> list[str]:
    """Dispatch many agents with bounded parallelism and failure isolation."""

    if max_parallel <= 0:
        raise ValueError("max_parallel must be positive")
    effective_store = store or StatusStore()
    kernel = MonitorKernel(registry=registry, store=effective_store)
    semaphore = asyncio.Semaphore(max_parallel)
    agent_ids = [f"ys-fleet-{uuid.uuid4()}" for _ in specs]

    async def run_one(agent_id: str, spec: RunSpec) -> None:
        async with semaphore:
            try:
                await kernel.run(spec, agent_id=agent_id, skip_preflight=skip_preflight)
            except Exception as exc:  # noqa: BLE001 - failure isolation requires persisted error.
                _persist_failed_result(effective_store, agent_id, spec, exc)

    async with asyncio.TaskGroup() as task_group:
        for agent_id, spec in zip(agent_ids, specs, strict=True):
            task_group.create_task(run_one(agent_id, spec))
    return agent_ids


def fleet_status(agent_ids: list[str], *, store: StatusStore | None = None) -> FleetStatus:
    """Deterministically aggregate status for a fleet."""

    effective_store = store or StatusStore()
    state_counts: dict[AgentState, int] = {}
    total_usage = Usage()
    total_cost = 0.0
    saw_cost = False
    blockers: list[ErrorRecord] = []
    for agent_id in agent_ids:
        current = effective_store.read_status(agent_id)
        state_counts[current.state] = state_counts.get(current.state, 0) + 1
        total_usage = Usage(
            input_tokens=total_usage.input_tokens + current.usage.input_tokens,
            cached_input_tokens=total_usage.cached_input_tokens
            + current.usage.cached_input_tokens,
            output_tokens=total_usage.output_tokens + current.usage.output_tokens,
            reasoning_tokens=total_usage.reasoning_tokens + current.usage.reasoning_tokens,
        )
        if current.cost_usd is not None:
            saw_cost = True
            total_cost += current.cost_usd
        blockers.extend(error for error in current.errors if error.fatal)
    return FleetStatus(
        agent_ids=agent_ids,
        state_counts=state_counts,
        total_usage=total_usage,
        total_cost_usd=total_cost if saw_cost else None,
        blockers=blockers,
    )


def consolidate(agent_ids: list[str], *, store: StatusStore | None = None) -> dict[str, Any]:
    """Merge terminal results into a parent-friendly observation."""

    effective_store = store or StatusStore()
    results = [effective_store.read_result(agent_id) for agent_id in agent_ids]
    return {
        "agent_ids": agent_ids,
        "replies": {result.agent_id: result.reply for result in results},
        "errors": {
            result.agent_id: result.error_category
            for result in results
            if result.error_category is not None
        },
        "artifacts": {
            result.agent_id: result.artifacts
            for result in results
            if result.artifacts
        },
    }


def route(task: str, *, preferred_cli: str = "claude") -> tuple[str, str]:
    """Minimal transparent route helper that never hides the selected CLI."""

    if any(keyword in task.lower() for keyword in ("test", "python", "refactor")):
        return preferred_cli, "keyword_default"
    return preferred_cli, "default"


def _persist_failed_result(
    store: StatusStore,
    agent_id: str,
    spec: RunSpec,
    exc: Exception,
) -> None:
    result = RunResult(
        agent_id=agent_id,
        cli=spec.cli,
        state=AgentState.FAILED,
        is_error=True,
        error_category="unknown",
        reply=None,
    )
    store.write_result(result)
    store.write_status(
        AgentStatus(
            agent_id=agent_id,
            cli=spec.cli,
            state=AgentState.FAILED,
            errors=[
                ErrorRecord(
                    category="unknown",
                    message=str(exc),
                    fatal=True,
                )
            ],
        )
    )
