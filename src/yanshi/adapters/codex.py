"""Codex CLI adapter."""

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


class CodexAdapter(Adapter):
    """Adapter for `codex exec --json`."""

    name = "codex"
    prompt_mode = "stdin"
    seed_paths = [".codex/auth.json", ".codex/config.toml"]
    capabilities = Capabilities(
        effort=CapabilityMode.CONFIG,
        session_resume=True,
        stream_json=True,
        permission_modes=[AllowMode.READ_ONLY, AllowMode.YOLO],
    )

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        args: list[str] = []
        if spec.allow == AllowMode.READ_ONLY:
            args.extend(["--sandbox", "read-only", "--ask-for-approval", "never", "--search"])
        else:
            args.append("--dangerously-bypass-approvals-and-sandbox")
        args.append("exec")
        if spec.session_id and spec.session_mode == "resume":
            args.extend(["resume", spec.session_id])
        if spec.model:
            args.extend(["--model", spec.model])
        if spec.reasoning_effort:
            args.extend(["-c", f'model_reasoning_effort="{spec.reasoning_effort}"'])
        args.extend(["--json", "--skip-git-repo-check", "-"])
        return BuiltCommand(command="codex", args=args, cwd=spec.workdir, stdin_text=spec.prompt)

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        raw = parse_json_object(raw_line.strip())
        method = _text(raw.get("method") or raw.get("type"))
        params = _dict(raw.get("params") or raw)
        ts = time.time()
        session_id = self.session_id_from_event(raw)
        if method in {"thread.started", "turn.started"}:
            return YanShiEvent(kind=EventKind.STARTED, raw=raw_line, ts=ts, session_id=session_id)
        if method in {"item/completed", "item.completed"}:
            item = _dict(params.get("item"))
            item_type = _text(item.get("type"))
            if item_type in {"agentMessage", "agent_message"}:
                return YanShiEvent(
                    kind=EventKind.ASSISTANT_TEXT,
                    text=_text(item.get("text")),
                    raw=raw_line,
                    ts=ts,
                    session_id=session_id,
                )
            if item_type in {"commandExecution", "command_execution"}:
                return YanShiEvent(
                    kind=EventKind.TOOL_USE,
                    text=_text(item.get("command")),
                    raw=raw_line,
                    ts=ts,
                    session_id=session_id,
                )
        if method in {"turn.completed", "turn.failed"}:
            usage = _usage(_dict(params.get("usage")))
            failed = method == "turn.failed"
            return YanShiEvent(
                kind=EventKind.ERROR if failed else EventKind.COMPLETED,
                text=_text(params.get("message")),
                usage=usage,
                err=_text(params.get("error")) if failed else None,
                raw=raw_line,
                ts=ts,
                session_id=session_id,
                is_error=failed,
            )
        return YanShiEvent(kind=EventKind.UNKNOWN, text=method, raw=raw_line, ts=ts)

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        events = _events_from_stdout(self, outcome.stdout)
        terminal = next((event for event in reversed(events) if _terminal(event)), None)
        is_error = outcome.exit_code not in (0, None) or bool(terminal and terminal.is_error)
        error_text = (terminal.err if terminal else None) or outcome.stderr
        return RunResult(
            agent_id=str(uuid.uuid4()),
            cli=self.name,
            state=AgentState.FAILED if is_error else AgentState.SUCCEEDED,
            is_error=is_error,
            reply=_last_text(events),
            session_id=next((event.session_id for event in events if event.session_id), None),
            usage=terminal.usage if terminal and terminal.usage else Usage(),
            pricing_status=PricingStatus.MISSING,
            exit_code=outcome.exit_code,
            duration_ms=outcome.duration_ms,
            error_category=classify_error_text(error_text).value if is_error else None,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        params = _dict(ev.get("params"))
        for key in ("threadId", "thread_id", "session_id"):
            value = params.get(key) or ev.get(key)
            if isinstance(value, str) and value:
                return value
        return None


def _usage(raw: dict[str, object]) -> Usage:
    return Usage(
        input_tokens=_int(raw.get("input_tokens") or raw.get("input")),
        cached_input_tokens=_int(raw.get("cached_input_tokens") or raw.get("cached")),
        output_tokens=_int(raw.get("output_tokens") or raw.get("output")),
        reasoning_tokens=_int(
            raw.get("reasoning_tokens")
            or raw.get("reasoning")
            or raw.get("reasoning_output_tokens")
        ),
    )


def _last_text(events: list[YanShiEvent]) -> str | None:
    for event in reversed(events):
        if event.kind == EventKind.ASSISTANT_TEXT and event.text:
            return event.text
    return None


def _events_from_stdout(adapter: CodexAdapter, stdout: str) -> list[YanShiEvent]:
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
