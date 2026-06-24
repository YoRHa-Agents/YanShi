from __future__ import annotations

import json
from pathlib import Path

import pytest

from yanshi.adapters.cursor import CursorAdapter, cursor_model, resolve_cursor_command
from yanshi.contracts import AllowMode, RawOutcome, RunSpec


def test_cursor_resolves_cursor_agent_before_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cursor_agent = tmp_path / "cursor-agent"
    agent = tmp_path / "agent"
    cursor_agent.write_text("#!/bin/sh\n", encoding="utf-8")
    agent.write_text("#!/bin/sh\n", encoding="utf-8")
    cursor_agent.chmod(0o755)
    agent.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_cursor_command() == "cursor-agent"


def test_cursor_effort_model_conflict_keeps_user_model() -> None:
    model, warnings = cursor_model("custom-model", "high")
    assert model == "custom-model"
    assert warnings[0].code == "cursor_effort_model_conflict"


def test_cursor_build_command_read_only_and_yolo() -> None:
    read_only = CursorAdapter().build_command(
        RunSpec(cli="cursor", prompt="hi", reasoning_effort="high")
    )
    assert read_only.command in {"cursor-agent", "agent"}
    assert "--mode" in read_only.args
    assert "gpt-5.5-high" in read_only.args

    yolo = CursorAdapter().build_command(
        RunSpec(cli="cursor", prompt="hi", allow=AllowMode.YOLO)
    )
    assert "--force" in yolo.args


def test_cursor_parse_result_with_warning() -> None:
    adapter = CursorAdapter()
    adapter.build_command(RunSpec(cli="cursor", prompt="hi", model="m", reasoning_effort="high"))
    stdout = "\n".join(
        [
            json.dumps({"type": "system", "session_id": "c1"}),
            json.dumps({"type": "assistant", "text": "hello"}),
            json.dumps(
                {
                    "type": "result",
                    "session_id": "c1",
                    "is_error": False,
                    "result": "done",
                    "usage": {"input": 1, "output": 2},
                }
            ),
        ]
    )
    result = adapter.parse_result(RawOutcome(command="cursor", exit_code=0, stdout=stdout))
    assert result.reply == "done"
    assert result.session_id == "c1"
    assert result.usage.total == 3
    assert result.warnings[0].code == "cursor_effort_model_conflict"


def test_cursor_parse_real_nested_text_and_camelcase_usage() -> None:
    """Regression for cursor-agent nested message content + camelCase usage keys."""

    adapter = CursorAdapter()
    assistant = adapter.parse_event(
        json.dumps(
            {
                "type": "assistant",
                "session_id": "c2",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "pong"}]},
            }
        )
    )
    assert assistant is not None
    assert assistant.text == "pong"

    stdout = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "c2"}),
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": "c2",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "pong"}]},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "c2",
                    "is_error": False,
                    "result": "pong",
                    "usage": {
                        "inputTokens": 100,
                        "outputTokens": 7,
                        "cacheReadTokens": 4,
                        "cacheWriteTokens": 0,
                    },
                }
            ),
        ]
    )
    result = adapter.parse_result(RawOutcome(command="cursor", exit_code=0, stdout=stdout))
    assert result.reply == "pong"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 7
    assert result.usage.cached_input_tokens == 4
    assert result.usage.total == 111
