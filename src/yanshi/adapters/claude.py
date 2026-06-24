"""Claude Code adapter."""

from __future__ import annotations

import time
import uuid
from typing import Any

from yanshi.adapters.base import Adapter, compact_json, parse_json_object
from yanshi.contracts import (
    AgentState,
    AllowMode,
    BuiltCommand,
    Capabilities,
    CapabilityMode,
    EventKind,
    PricingStatus,
    PromptMode,
    RawOutcome,
    RunResult,
    RunSpec,
    SessionMode,
    Usage,
    WarningRecord,
    YanShiEvent,
)
from yanshi.errors import classify_error_text

_CLAUDE_READ_ONLY_TOOLS = "Read,Grep,Glob,LS,WebFetch,WebSearch"


class ClaudeAdapter(Adapter):
    """Adapter for `claude -p --output-format stream-json --verbose`."""

    name = "claude"
    prompt_mode = PromptMode.STDIN.value
    seed_paths = [
        ".claude.json",
        ".claude/settings.json",
        ".claude/.credentials.json",
        ".claude/auth.json",
    ]
    capabilities = Capabilities(
        effort=CapabilityMode.FLAG,
        context_window_flag=False,
        session_resume=True,
        preassign_session_id=True,
        output_schema=True,
        stream_json=True,
        permission_modes=[AllowMode.READ_ONLY, AllowMode.YOLO],
    )

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        """Build the Claude command without executing it."""

        args = ["-p"]
        stdin_text: str | None = spec.prompt
        if spec.prompt_mode == PromptMode.ARGUMENT:
            args.append(spec.prompt)
            stdin_text = None

        if spec.model:
            args.extend(["--model", spec.model])
        if spec.session_mode == SessionMode.RESUME and spec.session_id:
            args.extend(["--resume", spec.session_id])
        elif spec.session_mode == SessionMode.NEW and spec.session_id:
            args.extend(["--session-id", spec.session_id])

        args.extend(["--output-format", "stream-json", "--verbose"])

        if spec.reasoning_effort:
            args.extend(["--effort", spec.reasoning_effort])
        if spec.output_schema is not None:
            args.extend(["--json-schema", compact_json(spec.output_schema)])

        if spec.allow == AllowMode.READ_ONLY:
            args.extend(["--allowedTools", _CLAUDE_READ_ONLY_TOOLS])
        elif spec.allow == AllowMode.YOLO:
            args.append("--dangerously-skip-permissions")

        return BuiltCommand(
            command="claude",
            args=args,
            env=spec.env or None,
            cwd=spec.workdir,
            stdin_text=stdin_text,
        )

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        """Normalize one Claude stream-json event."""

        stripped = raw_line.strip()
        if not stripped:
            return None
        raw = parse_json_object(stripped)
        event_type = _str(raw.get("type"))
        session_id = self.session_id_from_event(raw)
        timestamp = time.time()

        if event_type == "system":
            subtype = _str(raw.get("subtype"))
            if subtype in {"init", "started"} or session_id:
                return YanShiEvent(
                    kind=EventKind.STARTED,
                    text=subtype or "claude session started",
                    raw=stripped,
                    ts=timestamp,
                    session_id=session_id,
                )
            return None

        if event_type == "assistant":
            message = _dict(raw.get("message"))
            for item in _list(message.get("content")):
                content = _dict(item)
                content_type = _str(content.get("type"))
                if content_type == "text":
                    text = _str(content.get("text"))
                    if text:
                        return YanShiEvent(
                            kind=EventKind.ASSISTANT_TEXT,
                            text=text,
                            raw=stripped,
                            ts=timestamp,
                            session_id=session_id,
                        )
                if content_type == "tool_use":
                    name = _str(content.get("name")) or "tool"
                    tool_id = _str(content.get("id"))
                    tool_input = _dict(content.get("input"))
                    command = _str(tool_input.get("command"))
                    details = f"tool_use name=`{name}`"
                    if tool_id:
                        details = f"{details} id=`{tool_id}`"
                    if command:
                        details = f"{details} command=`{command}`"
                    return YanShiEvent(
                        kind=EventKind.TOOL_USE,
                        text=details,
                        raw=stripped,
                        ts=timestamp,
                        session_id=session_id,
                    )
            return None

        if event_type == "user":
            message = _dict(raw.get("message"))
            for item in _list(message.get("content")):
                content = _dict(item)
                if _str(content.get("type")) == "tool_result":
                    text = _str(content.get("content")) or _str(content.get("tool_use_id"))
                    return YanShiEvent(
                        kind=EventKind.TOOL_RESULT,
                        text=text,
                        raw=stripped,
                        ts=timestamp,
                        session_id=session_id,
                    )
            return None

        if event_type == "result":
            usage = _usage_from_mapping(_dict(raw.get("usage")))
            is_error = bool(raw.get("is_error"))
            err = _str(raw.get("error")) or (_str(raw.get("result")) if is_error else None)
            return YanShiEvent(
                kind=EventKind.ERROR if is_error else EventKind.COMPLETED,
                text=_str(raw.get("result")),
                usage=usage,
                err=err,
                raw=stripped,
                ts=timestamp,
                session_id=session_id,
                cost_usd=_float(raw.get("total_cost_usd")),
                is_error=is_error,
            )

        if event_type == "stream_event":
            return _stream_event(stripped, raw, timestamp, session_id)

        return YanShiEvent(
            kind=EventKind.UNKNOWN,
            text=event_type or "unknown",
            raw=stripped,
            ts=timestamp,
            session_id=session_id,
        )

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        """Parse Claude NDJSON output into a terminal result."""

        events: list[YanShiEvent] = []
        parse_warnings: list[WarningRecord] = []
        for line in outcome.stdout.splitlines():
            try:
                event = self.parse_event(line)
            except Exception as exc:  # noqa: BLE001 - surface parse errors as structured warnings.
                parse_warnings.append(
                    WarningRecord(
                        code="claude_parse_error",
                        message=str(exc),
                        detail={"line": line[:200]},
                    )
                )
                continue
            if event is not None:
                events.append(event)

        terminal = next((event for event in reversed(events) if _is_terminal_event(event)), None)
        usage = terminal.usage if terminal and terminal.usage else Usage()
        cost = terminal.cost_usd if terminal else None
        reply = terminal.text if terminal and terminal.text else _last_text(events)
        session_id = next((event.session_id for event in events if event.session_id), None)
        is_error = bool(outcome.timed_out or (outcome.exit_code not in (0, None)))
        if terminal is not None and terminal.is_error is not None:
            is_error = is_error or terminal.is_error

        error_text = ""
        if terminal and terminal.err:
            error_text = terminal.err
        elif outcome.stderr:
            error_text = outcome.stderr
        error_category = classify_error_text(error_text).value if is_error else None

        return RunResult(
            agent_id=str(uuid.uuid4()),
            cli=self.name,
            state=AgentState.FAILED if is_error else AgentState.SUCCEEDED,
            is_error=is_error,
            reply=reply,
            session_id=session_id,
            usage=usage,
            cost_usd=cost,
            pricing_status=PricingStatus.NATIVE if cost is not None else PricingStatus.MISSING,
            exit_code=outcome.exit_code,
            duration_ms=outcome.duration_ms,
            error_category=error_category,
            warnings=parse_warnings,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        """Extract a Claude session id from a raw event."""

        direct = ev.get("session_id")
        if isinstance(direct, str) and direct:
            return direct
        message = ev.get("message")
        if isinstance(message, dict):
            nested = message.get("session_id")
            if isinstance(nested, str) and nested:
                return nested
        return None


def _stream_event(
    stripped: str,
    raw: dict[str, object],
    timestamp: float,
    session_id: str | None,
) -> YanShiEvent | None:
    payload = _dict(raw.get("event"))
    event_type = _str(payload.get("type"))
    if event_type == "content_block_delta":
        return None
    if event_type == "content_block_stop":
        return None
    if event_type == "message_delta":
        usage = _usage_from_mapping(_dict(payload.get("usage")))
        return YanShiEvent(
            kind=EventKind.USAGE,
            usage=usage,
            raw=stripped,
            ts=timestamp,
            session_id=session_id,
        )
    return YanShiEvent(
        kind=EventKind.UNKNOWN,
        text=event_type or "stream_event",
        raw=stripped,
        ts=timestamp,
        session_id=session_id,
    )


def _usage_from_mapping(value: dict[str, object]) -> Usage:
    return Usage(
        input_tokens=_int(value.get("input_tokens")),
        cached_input_tokens=_int(value.get("cache_read_input_tokens"))
        + _int(value.get("cached_input_tokens")),
        output_tokens=_int(value.get("output_tokens")),
        reasoning_tokens=_int(value.get("reasoning_tokens")),
    )


def _last_text(events: list[YanShiEvent]) -> str | None:
    for event in reversed(events):
        if event.kind == EventKind.ASSISTANT_TEXT and event.text:
            return event.text
    return None


def _is_terminal_event(event: YanShiEvent) -> bool:
    return event.kind in {EventKind.COMPLETED, EventKind.ERROR}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
