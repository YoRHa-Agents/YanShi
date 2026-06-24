"""Atomic status/result store for `$YANSHI_HOME`."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from filelock import FileLock

from yanshi.contracts import TERMINAL_STATES, AgentState, AgentStatus, ErrorRecord, RunResult


class StatusStore:
    """Store YanShi run records using atomic writes and per-agent locks."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get("YANSHI_HOME", "~/.yanshi")).expanduser()
        self.agents_dir = self.root / "agents"
        self.agents_dir.mkdir(parents=True, exist_ok=True)

    def agent_dir(self, agent_id: str) -> Path:
        """Return an agent directory, creating it if necessary."""

        path = self.agents_dir / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_status(self, status: AgentStatus) -> None:
        """Atomically write `run.json` with mode 0600."""

        path = self.agent_dir(status.agent_id) / "run.json"
        self._atomic_write_json(path, status.model_dump(mode="json"))

    def read_status(self, agent_id: str) -> AgentStatus:
        """Read status and correct stale running state when owner pid is dead."""

        path = self.agent_dir(agent_id) / "run.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        status = AgentStatus.model_validate(raw)
        owner_dead = status.owner_pid is not None and not _pid_alive(status.owner_pid)
        if status.state not in TERMINAL_STATES and owner_dead:
            corrected = status.model_copy(deep=True)
            corrected.state = AgentState.STALLED
            corrected.errors.append(
                ErrorRecord(
                    category="unknown",
                    message=f"owner pid {status.owner_pid} is not alive",
                    fatal=True,
                )
            )
            return corrected
        return status

    def write_result(self, result: RunResult) -> None:
        """Atomically write `result.json` with mode 0600."""

        path = self.agent_dir(result.agent_id) / "result.json"
        self._atomic_write_json(path, result.model_dump(mode="json"))

    def read_result(self, agent_id: str) -> RunResult:
        """Read a terminal result."""

        path = self.agent_dir(agent_id) / "result.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RunResult.model_validate(raw)

    def stream_path(self, agent_id: str) -> Path:
        """Return the raw stream path for an agent."""

        return self.agent_dir(agent_id) / "stream.ndjson"

    def list_agent_ids(self) -> list[str]:
        """List known agent ids deterministically."""

        return sorted(path.name for path in self.agents_dir.iterdir() if path.is_dir())

    def gc(self, *, older_than_s: float) -> list[str]:
        """Remove terminal agent directories older than `older_than_s`."""

        if older_than_s < 0:
            raise ValueError("older_than_s must be non-negative")
        removed: list[str] = []
        cutoff = time.time() - older_than_s
        for agent_id in self.list_agent_ids():
            run_path = self.agent_dir(agent_id) / "run.json"
            if not run_path.is_file() or run_path.stat().st_mtime > cutoff:
                continue
            status = self.read_status(agent_id)
            if status.state in TERMINAL_STATES:
                shutil.rmtree(self.agent_dir(agent_id))
                removed.append(agent_id)
        return removed

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        lock_path = path.with_suffix(".lock")
        with FileLock(str(lock_path)):
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
