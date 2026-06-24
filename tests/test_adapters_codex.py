from __future__ import annotations

import json

from yanshi.adapters.codex import CodexAdapter
from yanshi.contracts import AllowMode, RawOutcome, RunSpec, SessionMode


def test_codex_build_command_read_only_effort_and_resume() -> None:
    command = CodexAdapter().build_command(
        RunSpec(
            cli="codex",
            prompt="hi",
            model="gpt-5.3-codex",
            reasoning_effort="high",
            allow=AllowMode.READ_ONLY,
            session_mode=SessionMode.RESUME,
            session_id="thread-1",
        )
    )
    assert command.command == "codex"
    assert command.stdin_text == "hi"
    assert command.args[:5] == ["--sandbox", "read-only", "--ask-for-approval", "never", "--search"]
    assert "exec" in command.args
    assert command.args[6:8] == ["resume", "thread-1"]
    assert "-c" in command.args
    assert command.args[-3:] == ["--json", "--skip-git-repo-check", "-"]


def test_codex_build_command_yolo() -> None:
    command = CodexAdapter().build_command(
        RunSpec(cli="codex", prompt="hi", allow=AllowMode.YOLO)
    )
    assert command.args[0] == "--dangerously-bypass-approvals-and-sandbox"


def test_codex_parse_fixture_result() -> None:
    stdout = "\n".join(
        [
            json.dumps({"method": "thread.started", "params": {"threadId": "t1"}}),
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {"item": {"type": "agentMessage", "text": "hello"}},
                }
            ),
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {"item": {"type": "commandExecution", "command": "pwd"}},
                }
            ),
            json.dumps(
                {
                    "method": "turn.completed",
                    "params": {"usage": {"input": 2, "output": 3}},
                }
            ),
        ]
    )
    result = CodexAdapter().parse_result(RawOutcome(command="codex", exit_code=0, stdout=stdout))
    assert result.state == "succeeded"
    assert result.reply == "hello"
    assert result.session_id == "t1"
    assert result.usage.total == 5


def test_codex_parse_real_snake_case_items_and_reasoning_usage() -> None:
    """Regression for codex-cli 0.140.0 snake_case item types + reasoning_output_tokens."""

    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "th_real"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": "echo hi"},
                }
            ),
            json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "pong"}}
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 2,
                        "output_tokens": 3,
                        "reasoning_output_tokens": 5,
                    },
                }
            ),
        ]
    )
    result = CodexAdapter().parse_result(RawOutcome(command="codex", exit_code=0, stdout=stdout))
    assert result.state == "succeeded"
    assert result.reply == "pong"
    assert result.session_id == "th_real"
    assert result.usage.reasoning_tokens == 5
    assert result.usage.total == 20
