from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yanshi.contracts import (
    AgentState,
    BuiltCommand,
    Capabilities,
    EventKind,
    RawOutcome,
    RunResult,
    RunSpec,
    Usage,
    YanShiEvent,
)
from yanshi.fleet import consolidate, dispatch_many, fleet_status, route
from yanshi.registry import AdapterRegistry
from yanshi.store import StatusStore


@dataclass
class FleetAdapter:
    name: str = "fleet"
    prompt_mode: str = "stdin"
    seed_paths: list[str] = field(default_factory=list)
    capabilities: Capabilities = field(default_factory=lambda: Capabilities(stream_json=True))

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        if spec.prompt == "fail-build":
            raise RuntimeError("boom")
        script = (
            "import json, sys;"
            "prompt=sys.stdin.read();"
            "print(json.dumps({'kind':'started'}), flush=True);"
            "print(json.dumps({'kind':'completed','text':prompt}), flush=True)"
        )
        return BuiltCommand(command=sys.executable, args=["-c", script], stdin_text=spec.prompt)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        raw = json.loads(raw_line)
        return YanShiEvent(
            kind=EventKind(raw["kind"]),
            text=raw.get("text", ""),
            usage=Usage(input_tokens=1, output_tokens=1) if raw["kind"] == "completed" else None,
            cost_usd=0.1 if raw["kind"] == "completed" else None,
            is_error=False if raw["kind"] == "completed" else None,
        )

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        return RunResult(
            agent_id="placeholder",
            cli=self.name,
            state=AgentState.SUCCEEDED if outcome.exit_code == 0 else AgentState.FAILED,
            is_error=outcome.exit_code != 0,
            reply=outcome.stdout,
            usage=Usage(input_tokens=1, output_tokens=1),
            cost_usd=0.1,
            exit_code=outcome.exit_code,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        return None


def _registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(FleetAdapter())
    return registry


@pytest.mark.asyncio
async def test_dispatch_many_isolates_failures_and_fleet_status(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    agent_ids = await dispatch_many(
        [
            RunSpec(cli="fleet", prompt="one"),
            RunSpec(cli="fleet", prompt="fail-build"),
            RunSpec(cli="fleet", prompt="two"),
        ],
        max_parallel=2,
        registry=_registry(),
        store=store,
        skip_preflight=True,
    )
    assert len(agent_ids) == 3
    status = fleet_status(agent_ids, store=store)
    assert status.state_counts[AgentState.SUCCEEDED] == 2
    assert status.state_counts[AgentState.FAILED] == 1
    assert status.total_usage.total == 4
    assert status.total_cost_usd == 0.2
    assert status.blockers[0].message == "boom"


@pytest.mark.asyncio
async def test_consolidate_and_route(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    agent_ids = await dispatch_many(
        [RunSpec(cli="fleet", prompt="one")],
        registry=_registry(),
        store=store,
        skip_preflight=True,
    )
    merged = consolidate(agent_ids, store=store)
    assert list(merged["replies"]) == agent_ids
    assert route("run python tests")[0] == "claude"
    with pytest.raises(ValueError):
        await dispatch_many([], max_parallel=0, registry=_registry(), store=store)
