"""Cursor agent adapter."""

from __future__ import annotations

import shutil
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
    WarningRecord,
    YanShiEvent,
)
from yanshi.errors import classify_error_text


class CursorAdapter(Adapter):
    """Adapter for `cursor-agent` or its `agent` alias."""

    name = "cursor"
    prompt_mode = "argument"
    seed_paths = [".cursor/cli-config.json"]
    capabilities = Capabilities(
        effort=CapabilityMode.MODEL_SUFFIX,
        session_resume=True,
        stream_json=True,
        permission_modes=[AllowMode.READ_ONLY, AllowMode.YOLO],
    )

    def __init__(self) -> None:
        self.last_warnings: list[WarningRecord] = []

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        command = resolve_cursor_command()
        model, warnings = cursor_model(spec.model, spec.reasoning_effort)
        self.last_warnings = warnings
        args = ["-p", "--trust", "--output-format", "stream-json"]
        if spec.allow == AllowMode.YOLO:
            args.append("--force")
        if spec.session_id:
            args.extend(["--resume", spec.session_id])
        if model:
            args.extend(["--model", model])
        if spec.allow == AllowMode.READ_ONLY:
            args.extend(["--mode", "plan"])
        args.append(spec.prompt)
        return BuiltCommand(command=command, args=args, cwd=spec.workdir)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        raw = parse_json_object(raw_line.strip())
        kind = _text(raw.get("type"))
        ts = time.time()
        session_id = self.session_id_from_event(raw)
        if kind in {"system", "init"}:
            return YanShiEvent(kind=EventKind.STARTED, raw=raw_line, ts=ts, session_id=session_id)
        if kind in {"assistant", "message"}:
            return YanShiEvent(
                kind=EventKind.ASSISTANT_TEXT,
                text=_message_text(raw),
                raw=raw_line,
                ts=ts,
                session_id=session_id,
            )
        if kind in {"tool_call", "tool_use"}:
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
                text=_text(raw.get("result")),
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
        is_error = outcome.exit_code not in (0, None) or bool(terminal and terminal.is_error)
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
            error_category=classify_error_text(outcome.stderr).value if is_error else None,
            warnings=self.last_warnings,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        for key in ("session_id", "sessionId", "thread_id", "threadId"):
            value = ev.get(key)
            if isinstance(value, str) and value:
                return value
        return None


def resolve_cursor_command() -> str:
    """Resolve cursor executable using required `cursor-agent` → `agent` fallback."""

    if shutil.which("cursor-agent"):
        return "cursor-agent"
    return "agent"


def cursor_model(
    model: str | None,
    effort: str | None,
) -> tuple[str | None, list[WarningRecord]]:
    """Translate Cursor effort into model variants without overriding explicit models."""

    warnings: list[WarningRecord] = []
    if model and effort:
        warnings.append(
            WarningRecord(
                code="cursor_effort_model_conflict",
                message="explicit model wins; reasoning_effort cannot be expressed separately",
                detail={"model": model, "reasoning_effort": effort},
            )
        )
        return model, warnings
    base = model or "gpt-5.5"
    if effort and _supports_reasoning_variant(base):
        return f"{base}-{effort}", warnings
    return base, warnings


def _supports_reasoning_variant(model: str) -> bool:
    return model.startswith("gpt-")


def _message_text(raw: dict[str, object]) -> str:
    """Extract assistant text from flat or nested cursor message payloads."""

    direct = raw.get("text")
    if isinstance(direct, str) and direct:
        return direct
    message = raw.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        parts = [
            _text(block.get("text"))
            for block in _list(message.get("content"))
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(part for part in parts if part)
    return ""


def _usage(raw: dict[str, object]) -> Usage:
    return Usage(
        input_tokens=_int(raw.get("input_tokens") or raw.get("input") or raw.get("inputTokens")),
        cached_input_tokens=_int(
            raw.get("cached_input_tokens") or raw.get("cached") or raw.get("cacheReadTokens")
        ),
        output_tokens=_int(
            raw.get("output_tokens") or raw.get("output") or raw.get("outputTokens")
        ),
        reasoning_tokens=_int(raw.get("reasoning_tokens") or raw.get("reasoning")),
    )


def _last_text(events: list[YanShiEvent]) -> str | None:
    for event in reversed(events):
        if event.kind == EventKind.ASSISTANT_TEXT and event.text:
            return event.text
    return None


def _events_from_stdout(adapter: CursorAdapter, stdout: str) -> list[YanShiEvent]:
    return [event for line in stdout.splitlines() if (event := adapter.parse_event(line))]


def _terminal(event: YanShiEvent) -> bool:
    return event.kind in {EventKind.COMPLETED, EventKind.ERROR}


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0
