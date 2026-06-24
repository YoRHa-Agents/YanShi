"""Live end-to-end integration tests against real agent CLIs.

These tests spawn the actual `codex`, `claude` (Claude Code), and `cursor-agent`
binaries through the full YanShi dispatch path (preflight -> build_command ->
monitor kernel -> StatusStore on disk). They validate the parent-agent contract
documented in ``skill/SKILL.md``: dispatch a real sub-agent, then observe it only
through the compact ``status``/``summary`` pull objects plus the persisted
``RunResult``, never by reading raw child streams into context.

They are gated behind the ``YANSHI_LIVE`` environment variable because they cost
real vendor tokens and require authenticated CLIs. Run them explicitly with::

    YANSHI_LIVE=1 uv run pytest -m live

Each test additionally self-skips when its target CLI fails preflight, so a host
that only has a subset of the CLIs authenticated still runs what it can.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from yanshi.contracts import AgentState, AllowMode, ImproveSpec, RunSpec
from yanshi.dispatch import dispatch_wait
from yanshi.dispatch import status as read_status
from yanshi.dispatch import summary as read_summary
from yanshi.fleet import consolidate, dispatch_many, fleet_status
from yanshi.improve import improve_loop
from yanshi.preflight import preflight_adapter
from yanshi.registry import default_registry
from yanshi.store import StatusStore

pytestmark = pytest.mark.live

LIVE_ENV = "YANSHI_LIVE"
TIMEOUT_ENV = "YANSHI_LIVE_TIMEOUT"
# "agent cli" == cursor-agent; "claude code" == claude; plus codex.
TARGET_CLIS = ("codex", "claude", "cursor")
# Deterministic, tool-free, read-only friendly prompt for a tiny cheap turn.
PROMPT = "Reply with exactly the single word: pong . Use no tools and add nothing else."
EXPECTED_TOKEN = "pong"


def _live_enabled() -> bool:
    return os.environ.get(LIVE_ENV, "").strip().lower() not in {"", "0", "false", "no"}


def _require_live() -> None:
    if not _live_enabled():
        pytest.skip(f"live CLI tests disabled; set {LIVE_ENV}=1 to enable")


def _require_cli(cli: str) -> None:
    _require_live()
    result = preflight_adapter(default_registry().get(cli))
    if not result.ok:
        pytest.skip(f"{cli} preflight failed: {result.errors}")


def _available_clis() -> list[str]:
    registry = default_registry()
    return [cli for cli in TARGET_CLIS if preflight_adapter(registry.get(cli)).ok]


def _timeout() -> int:
    return int(os.environ.get(TIMEOUT_ENV, "180"))


def _is_json_object(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except json.JSONDecodeError:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("cli", TARGET_CLIS)
async def test_live_dispatch_round_trip(cli: str, tmp_path: Path) -> None:
    """A real dispatch succeeds and is fully observable from disk alone."""

    _require_cli(cli)
    store = StatusStore(tmp_path)
    spec = RunSpec(
        cli=cli,
        prompt=PROMPT,
        allow=AllowMode.READ_ONLY,
        timeout_s=_timeout(),
    )

    result = await dispatch_wait(spec, store=store)

    # --- RunResult contract (spec 3.6) ---
    assert result.cli == cli
    assert result.state == AgentState.SUCCEEDED, result.model_dump()
    assert result.is_error is False
    assert result.exit_code == 0
    assert result.reply is not None
    assert EXPECTED_TOKEN in result.reply.lower(), result.reply
    assert result.usage.total > 0, result.usage.model_dump()
    assert result.session_id, "native session id must be captured for resume"
    assert result.duration_ms is not None and result.duration_ms > 0

    # --- Raw stream is retained on disk for audit, not in parent context ---
    log_dir = Path(result.log_dir)
    assert log_dir.is_dir()
    stream = log_dir / "stream.ndjson"
    assert stream.is_file() and stream.stat().st_size > 0
    stream_lines = [
        line for line in stream.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert stream_lines, "expected non-empty raw NDJSON stream"
    assert all(_is_json_object(line) for line in stream_lines)

    # --- Low-context monitoring contract: pure-disk status/summary pulls ---
    snapshot = read_status(result.agent_id, store=store)
    assert snapshot.agent_id == result.agent_id
    assert snapshot.cli == cli
    assert snapshot.state == AgentState.SUCCEEDED
    assert snapshot.counters.get("events", 0) > 0
    assert snapshot.session_id == result.session_id
    # summary is advisory; it must always return a string without raising.
    assert isinstance(read_summary(result.agent_id, store=store), str)

    # --- Terminal RunResult is persisted and consistent with the snapshot ---
    persisted = store.read_result(result.agent_id)
    assert persisted.state == AgentState.SUCCEEDED
    assert persisted.reply == result.reply
    assert persisted.usage.total == result.usage.total


@pytest.mark.asyncio
async def test_live_fleet_parallel_dispatch(tmp_path: Path) -> None:
    """Fan out to every available CLI at once and aggregate deterministically."""

    _require_live()
    available = _available_clis()
    if len(available) < 2:
        pytest.skip(f"need >=2 authenticated CLIs for a fleet, have {available}")

    store = StatusStore(tmp_path)
    specs = [
        RunSpec(cli=cli, prompt=PROMPT, allow=AllowMode.READ_ONLY, timeout_s=_timeout())
        for cli in available
    ]

    agent_ids = await dispatch_many(specs, max_parallel=len(specs), store=store)
    assert len(agent_ids) == len(available)

    fleet = fleet_status(agent_ids, store=store)
    assert fleet.state_counts.get(AgentState.SUCCEEDED, 0) == len(available), fleet.model_dump()
    assert fleet.total_usage.total > 0
    assert fleet.blockers == []

    merged = consolidate(agent_ids, store=store)
    assert set(merged["replies"]) == set(agent_ids)
    for agent_id, reply in merged["replies"].items():
        assert reply is not None, agent_id
        assert EXPECTED_TOKEN in reply.lower(), (agent_id, reply)
    assert merged["errors"] == {}


@pytest.mark.parametrize("cli", TARGET_CLIS)
def test_live_yanshi_cli_binary_end_to_end(cli: str, tmp_path: Path) -> None:
    """Drive the documented skill verbs through the real `yanshi` console script."""

    _require_cli(cli)
    yanshi_bin = shutil.which("yanshi")
    if yanshi_bin is None:
        pytest.skip("yanshi console script not on PATH (use the installed/uv env)")

    env = {**os.environ, "YANSHI_HOME": str(tmp_path)}

    doctor = subprocess.run(
        [yanshi_bin, "doctor"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    doctor_rows = [json.loads(line) for line in doctor.stdout.splitlines() if line.strip()]
    target_row = next(row for row in doctor_rows if row["cli"] == cli)
    assert target_row["status"] == "ok", target_row

    dispatch = subprocess.run(
        [yanshi_bin, "dispatch", "--cli", cli, "--timeout", str(_timeout()), PROMPT],
        capture_output=True,
        text=True,
        env=env,
        timeout=_timeout() + 30,
        check=False,
    )
    assert dispatch.returncode == 0, dispatch.stderr
    payload = json.loads(dispatch.stdout.strip().splitlines()[-1])
    assert payload["state"] == "succeeded", payload
    assert payload["is_error"] is False
    assert EXPECTED_TOKEN in (payload["reply"] or "").lower(), payload["reply"]
    agent_id = payload["agent_id"]

    status_proc = subprocess.run(
        [yanshi_bin, "status", agent_id],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert status_proc.returncode == 0, status_proc.stderr
    assert json.loads(status_proc.stdout)["state"] == "succeeded"

    summary_proc = subprocess.run(
        [yanshi_bin, "summary", agent_id],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert summary_proc.returncode == 0, summary_proc.stderr

    list_proc = subprocess.run(
        [yanshi_bin, "list"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert agent_id in json.loads(list_proc.stdout)


def test_live_yanshi_improve_verb_end_to_end(tmp_path: Path) -> None:
    """Drive the documented `yanshi improve` verb through the real console script."""

    _require_live()
    cli = "claude"  # fastest authenticated round-trip for the binary path.
    if not preflight_adapter(default_registry().get(cli)).ok:
        pytest.skip(f"{cli} preflight failed")
    yanshi_bin = shutil.which("yanshi")
    if yanshi_bin is None:
        pytest.skip("yanshi console script not on PATH (use the installed/uv env)")

    env = {**os.environ, "YANSHI_HOME": str(tmp_path)}
    improve = subprocess.run(
        [
            yanshi_bin,
            "improve",
            "--cli",
            cli,
            "--check",
            "true",  # argv-only always-pass gate (shlex.split -> ["true"])
            "--max-iterations",
            "2",
            "--timeout",
            str(_timeout()),
            PROMPT,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=_timeout() + 30,
        check=False,
    )
    assert improve.returncode == 0, improve.stderr
    payload = json.loads(improve.stdout.strip().splitlines()[-1])
    assert payload["succeeded"] is True, payload
    assert payload["stop_reason"] == "gate_passed", payload
    assert len(payload["iterations"]) == 1, payload


# --------------------------------------------------------------------------- #
# Live improve-loop library: real dispatch -> deterministic gate -> (refine)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("cli", TARGET_CLIS)
async def test_live_improve_round_trip(cli: str, tmp_path: Path) -> None:
    """improve_loop drives a real sub-agent plus an always-pass gate in one cycle."""

    _require_cli(cli)
    store = StatusStore(tmp_path)
    plan = ImproveSpec(
        spec=RunSpec(cli=cli, prompt=PROMPT, allow=AllowMode.READ_ONLY, timeout_s=_timeout()),
        check_command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        gate_timeout_s=60,
        max_iterations=2,
    )

    result = await improve_loop(plan, store=store)

    assert result.succeeded is True, result.model_dump()
    assert result.stop_reason == "gate_passed", result.model_dump()
    assert len(result.iterations) == 1, result.model_dump()
    assert result.total_usage.total > 0, result.total_usage.model_dump()
    assert result.final_agent_id is not None

    first = result.iterations[0]
    assert first.state == AgentState.SUCCEEDED
    assert first.is_error is False
    assert first.gate is not None and first.gate.passed is True

    # improve is built on dispatch: the single cycle used the original prompt,
    # so the persisted sub-agent reply must still carry the expected token.
    persisted = store.read_result(result.final_agent_id)
    assert EXPECTED_TOKEN in (persisted.reply or "").lower(), persisted.reply


@pytest.mark.asyncio
async def test_live_improve_refines_until_max(tmp_path: Path) -> None:
    """An always-fail gate forces real re-dispatch up to the iteration bound."""

    _require_live()
    available = _available_clis()
    if not available:
        pytest.skip("no authenticated CLI available")
    cli = available[0]
    store = StatusStore(tmp_path)
    plan = ImproveSpec(
        spec=RunSpec(cli=cli, prompt=PROMPT, allow=AllowMode.READ_ONLY, timeout_s=_timeout()),
        check_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        gate_timeout_s=60,
        max_iterations=2,
    )

    result = await improve_loop(plan, store=store)

    assert result.succeeded is False, result.model_dump()
    assert result.stop_reason == "max_iterations", result.model_dump()
    assert len(result.iterations) == 2, result.model_dump()
    for iteration in result.iterations:
        assert iteration.gate is not None and iteration.gate.passed is False
