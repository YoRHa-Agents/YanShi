"""Gemini CLI adapter."""

from __future__ import annotations

import time
import uuid

from yanshi.adapters.base import Adapter, parse_json_object
from yanshi.contracts import (
    AgentState,
    AllowMode,
    BuiltCommand,
    Capabilities,
    CapabilityMode,
    EventKind,
    PricingStatus,
    RawOutcome,
    RunResult,
    RunSpec,
    Usage,
    YanShiEvent,
)
from yanshi.errors import classify_error_text


class GeminiAdapter(Adapter):
    """Adapter for `gemini --output-format stream-json`."""

    name = "gemini"
    prompt_mode = "stdin"
    seed_paths = [
        ".gemini/google_accounts.json",
        ".gemini/settings.json",
        ".gemini/state.json",
    ]
    capabilities = Capabilities(
        effort=CapabilityMode.THINKING_LEVEL,
        session_resume=True,
        preassign_session_id=True,
        output_schema=True,
        stream_json=True,
        permission_modes=[AllowMode.READ_ONLY, AllowMode.YOLO],
    )

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        args: list[str] = []
        if spec.model:
            args.extend(["--model", spec.model])
        args.extend(["-p", "--output-format", "stream-json"])
        if spec.session_id and spec.session_mode == "resume":
            args.extend(["--resume", spec.session_id])
        elif spec.session_id:
            args.extend(["--session-id", spec.session_id])
        if spec.reasoning_effort:
            args.extend(["--model-thinking-level", spec.reasoning_effort])
        args.extend(["--approval-mode", "plan" if spec.allow == AllowMode.READ_ONLY else "yolo"])
        return BuiltCommand(command="gemini", args=args, cwd=spec.workdir, stdin_text=spec.prompt)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        raw = parse_json_object(raw_line.strip())
        kind = _text(raw.get("type"))
        ts = time.time()
        session_id = self.session_id_from_event(raw)
        if kind in {"init", "started"}:
            return YanShiEvent(kind=EventKind.STARTED, raw=raw_line, ts=ts, session_id=session_id)
        if kind in {"message", "assistant"}:
            return YanShiEvent(
                kind=EventKind.ASSISTANT_TEXT,
                text=_text(raw.get("text") or raw.get("message")),
                raw=raw_line,
                ts=ts,
                session_id=session_id,
            )
        if kind in {"tool_use", "tool_call"}:
            return YanShiEvent(
                kind=EventKind.TOOL_USE,
                text=_text(raw.get("name") or raw.get("command")),
                raw=raw_line,
                ts=ts,
                session_id=session_id,
            )
        if kind == "result":
            failed = bool(raw.get("is_error"))
            return YanShiEvent(
                kind=EventKind.ERROR if failed else EventKind.COMPLETED,
                text=_text(raw.get("result") or raw.get("text")),
                err=_text(raw.get("error")) if failed else None,
                usage=_usage(_dict(raw.get("usage"))),
                raw=raw_line,
                ts=ts,
                session_id=session_id,
                is_error=failed,
            )
        return YanShiEvent(kind=EventKind.UNKNOWN, text=kind, raw=raw_line, ts=ts)

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        events = _events_from_stdout(self, outcome.stdout)
        terminal = next((event for event in reversed(events) if _terminal(event)), None)
        failed_exit = outcome.exit_code not in (0, None)
        is_error = failed_exit or bool(terminal and terminal.is_error)
        error_text = (
            (terminal.err if terminal else None)
            or outcome.stderr
            or _exit_reason(outcome.exit_code)
        )
        return RunResult(
            agent_id=str(uuid.uuid4()),
            cli=self.name,
            state=AgentState.FAILED if is_error else AgentState.SUCCEEDED,
            is_error=is_error,
            reply=terminal.text if terminal and terminal.text else _last_text(events),
            session_id=next((event.session_id for event in events if event.session_id), None),
            usage=terminal.usage if terminal and terminal.usage else Usage(),
            pricing_status=PricingStatus.MISSING,
            exit_code=outcome.exit_code,
            duration_ms=outcome.duration_ms,
            error_category=classify_error_text(error_text).value if is_error else None,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        for key in ("session_id", "sessionId"):
            value = ev.get(key)
            if isinstance(value, str) and value:
                return value
        return None


def _exit_reason(exit_code: int | None) -> str:
    if exit_code is None:
        return "unknown failure"
    return {
        1: "generic failure",
        42: "auth failure",
        53: "server error",
    }.get(exit_code, "unknown failure")


def _usage(raw: dict[str, object]) -> Usage:
    return Usage(
        input_tokens=_int(raw.get("input_tokens") or raw.get("input")),
        cached_input_tokens=_int(raw.get("cached_input_tokens") or raw.get("cached")),
        output_tokens=_int(raw.get("output_tokens") or raw.get("output")),
        reasoning_tokens=_int(raw.get("reasoning_tokens") or raw.get("reasoning")),
    )


def _last_text(events: list[YanShiEvent]) -> str | None:
    for event in reversed(events):
        if event.kind == EventKind.ASSISTANT_TEXT and event.text:
            return event.text
    return None


def _events_from_stdout(adapter: GeminiAdapter, stdout: str) -> list[YanShiEvent]:
    return [event for line in stdout.splitlines() if (event := adapter.parse_event(line))]


def _terminal(event: YanShiEvent) -> bool:
    return event.kind in {EventKind.COMPLETED, EventKind.ERROR}


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0
