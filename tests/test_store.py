from __future__ import annotations

import os
from pathlib import Path

from yanshi.contracts import AgentState, RunResult
from yanshi.reducer import initial_status
from yanshi.store import StatusStore


def test_store_writes_and_reads_status_atomically(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    status = initial_status("a1", "claude")
    status.owner_pid = os.getpid()
    store.write_status(status)
    path = store.agent_dir("a1") / "run.json"
    assert path.stat().st_mode & 0o777 == 0o600
    read = store.read_status("a1")
    assert read.agent_id == "a1"
    assert read.state == "pending"


def test_store_corrects_dead_owner_to_stalled(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    status = initial_status("a1", "claude")
    status.state = AgentState.RUNNING
    status.owner_pid = 999_999_999
    store.write_status(status)
    read = store.read_status("a1")
    assert read.state == "stalled"
    assert read.errors


def test_store_writes_result_and_lists_agents(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    store.write_result(
        RunResult(agent_id="a1", cli="claude", state=AgentState.SUCCEEDED, is_error=False)
    )
    assert store.read_result("a1").state == "succeeded"
    assert store.stream_path("a1").name == "stream.ndjson"
    assert store.list_agent_ids() == ["a1"]


def test_store_gc_removes_terminal_runs(tmp_path: Path) -> None:
    store = StatusStore(tmp_path)
    status = initial_status("a1", "claude")
    status.state = AgentState.SUCCEEDED
    store.write_status(status)
    assert store.gc(older_than_s=0) == ["a1"]
    assert store.list_agent_ids() == []
