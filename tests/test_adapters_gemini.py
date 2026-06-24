from __future__ import annotations

import json

from yanshi.adapters.gemini import GeminiAdapter
from yanshi.contracts import AllowMode, RawOutcome, RunSpec, SessionMode


def test_gemini_build_command_read_only_and_session() -> None:
    command = GeminiAdapter().build_command(
        RunSpec(
            cli="gemini",
            prompt="hi",
            model="gemini-pro",
            reasoning_effort="high",
            allow=AllowMode.READ_ONLY,
            session_id="g1",
        )
    )
    assert command.command == "gemini"
    assert command.stdin_text == "hi"
    assert command.args[:5] == ["--model", "gemini-pro", "-p", "--output-format", "stream-json"]
    assert "--session-id" in command.args
    assert "--model-thinking-level" in command.args
    assert command.args[-2:] == ["--approval-mode", "plan"]


def test_gemini_build_command_yolo_resume() -> None:
    command = GeminiAdapter().build_command(
        RunSpec(
            cli="gemini",
            prompt="hi",
            allow=AllowMode.YOLO,
            session_mode=SessionMode.RESUME,
            session_id="g1",
        )
    )
    assert "--resume" in command.args
    assert command.args[-1] == "yolo"


def test_gemini_parse_result_and_exit_failure() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "init", "session_id": "g1"}),
            json.dumps({"type": "message", "text": "hello"}),
            json.dumps(
                {
                    "type": "result",
                    "session_id": "g1",
                    "is_error": False,
                    "result": "done",
                    "usage": {"input": 3, "output": 4},
                }
            ),
        ]
    )
    ok = GeminiAdapter().parse_result(RawOutcome(command="gemini", exit_code=0, stdout=stdout))
    assert ok.state == "succeeded"
    assert ok.usage.total == 7

    failed = GeminiAdapter().parse_result(RawOutcome(command="gemini", exit_code=42, stdout=""))
    assert failed.state == "failed"
    assert failed.is_error is True
