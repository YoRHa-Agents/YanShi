from __future__ import annotations

import json

from yanshi.adapters.claude import ClaudeAdapter
from yanshi.contracts import (
    AllowMode,
    EventKind,
    PromptMode,
    RawOutcome,
    RunSpec,
    SessionMode,
)


def test_claude_build_command_read_only_uses_stream_json_and_allowed_tools() -> None:
    adapter = ClaudeAdapter()
    spec = RunSpec(
        cli="claude",
        prompt="inspect",
        model="sonnet",
        reasoning_effort="high",
        allow=AllowMode.READ_ONLY,
        session_id="ys-session",
    )

    command = adapter.build_command(spec)

    assert command.command == "claude"
    assert command.stdin_text == "inspect"
    assert command.args == [
        "-p",
        "--model",
        "sonnet",
        "--session-id",
        "ys-session",
        "--output-format",
        "stream-json",
        "--verbose",
        "--effort",
        "high",
        "--allowedTools",
        "Read,Grep,Glob,LS,WebFetch,WebSearch",
    ]


def test_claude_build_command_yolo_and_argument_prompt() -> None:
    adapter = ClaudeAdapter()
    spec = RunSpec(
        cli="claude",
        prompt="change files",
        prompt_mode=PromptMode.ARGUMENT,
        allow=AllowMode.YOLO,
        session_mode=SessionMode.RESUME,
        session_id="resume-id",
    )

    command = adapter.build_command(spec)

    assert command.stdin_text is None
    assert command.args[:3] == ["-p", "change files", "--resume"]
    assert "resume-id" in command.args
    assert "--dangerously-skip-permissions" in command.args
    assert "--allowedTools" not in command.args


def test_claude_build_command_includes_schema_as_single_arg() -> None:
    adapter = ClaudeAdapter()
    spec = RunSpec(
        cli="claude",
        prompt="structured",
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )

    command = adapter.build_command(spec)

    schema_index = command.args.index("--json-schema") + 1
    assert json.loads(command.args[schema_index])["type"] == "object"


def test_claude_parse_assistant_text_and_tool_use() -> None:
    adapter = ClaudeAdapter()
    text = adapter.parse_event(
        json.dumps(
            {
                "type": "assistant",
                "session_id": "s1",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }
        )
    )
    tool = adapter.parse_event(
        json.dumps(
            {
                "type": "assistant",
                "session_id": "s1",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "id": "toolu_1",
                            "input": {"command": "pwd"},
                        }
                    ]
                },
            }
        )
    )

    assert text is not None
    assert text.kind == EventKind.ASSISTANT_TEXT
    assert text.text == "hello"
    assert text.session_id == "s1"
    assert tool is not None
    assert tool.kind == EventKind.TOOL_USE
    assert "Bash" in tool.text
    assert "pwd" in tool.text


def test_claude_parse_user_tool_result_and_stream_usage() -> None:
    adapter = ClaudeAdapter()
    tool_result = adapter.parse_event(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "done",
                        }
                    ]
                },
            }
        )
    )
    usage = adapter.parse_event(
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_delta",
                    "usage": {"input_tokens": 4, "output_tokens": 6},
                },
            }
        )
    )
    delta = adapter.parse_event(
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta"}})
    )

    assert tool_result is not None
    assert tool_result.kind == EventKind.TOOL_RESULT
    assert tool_result.text == "done"
    assert usage is not None
    assert usage.kind == EventKind.USAGE
    assert usage.usage is not None
    assert usage.usage.total == 10
    assert delta is None


def test_claude_unknown_event_is_audited() -> None:
    event = ClaudeAdapter().parse_event(json.dumps({"type": "new_vendor_event"}))
    assert event is not None
    assert event.kind == EventKind.UNKNOWN
    assert event.text == "new_vendor_event"


def test_claude_parse_result_uses_terminal_result_usage_and_cost() -> None:
    adapter = ClaudeAdapter()
    stdout = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": "s1",
                    "message": {"content": [{"type": "text", "text": "draft reply"}]},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "session_id": "s1",
                    "is_error": False,
                    "result": "final reply",
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                    "total_cost_usd": 0.01,
                }
            ),
        ]
    )
    result = adapter.parse_result(
        RawOutcome(command="claude", args=[], exit_code=0, stdout=stdout, duration_ms=12)
    )

    assert result.cli == "claude"
    assert result.state == "succeeded"
    assert result.reply == "final reply"
    assert result.session_id == "s1"
    assert result.usage.total == 5
    assert result.cost_usd == 0.01
    assert result.pricing_status == "native"


def test_claude_parse_result_treats_is_error_as_failure() -> None:
    adapter = ClaudeAdapter()
    stdout = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "result": "not logged in",
            "usage": {},
        }
    )
    result = adapter.parse_result(RawOutcome(command="claude", args=[], exit_code=0, stdout=stdout))

    assert result.is_error is True
    assert result.state == "failed"
    assert result.error_category == "auth"
