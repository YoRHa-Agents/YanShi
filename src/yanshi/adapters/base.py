"""Adapter protocol and helper functions."""

from __future__ import annotations

import json
from typing import Protocol

from yanshi.contracts import BuiltCommand, Capabilities, RawOutcome, RunResult, RunSpec, YanShiEvent
from yanshi.errors import AdapterError, ErrorCategory


class Adapter(Protocol):
    """Protocol implemented by every CLI adapter."""

    name: str
    prompt_mode: str
    seed_paths: list[str]
    capabilities: Capabilities

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        """Build argv/env/stdin for a run without spawning a process."""

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        """Parse one raw output line into a normalized event."""

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        """Parse a completed subprocess outcome into a terminal result."""

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        """Extract a native CLI session/thread id from a raw event dict."""


def parse_json_object(raw_line: str) -> dict[str, object]:
    """Parse a raw JSON line into an object or raise an adapter error."""

    try:
        value = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise AdapterError(
            f"invalid JSON event: {exc.msg}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"line": raw_line[:200]},
        ) from exc
    if not isinstance(value, dict):
        raise AdapterError(
            "JSON event is not an object",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"line": raw_line[:200]},
        )
    return value


def compact_json(value: dict[str, object]) -> str:
    """Serialize a JSON object deterministically for single-argv schema flags."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
