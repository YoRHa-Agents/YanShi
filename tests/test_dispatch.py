from __future__ import annotations

import sys

from yanshi.contracts import (
    AgentState,
    BuiltCommand,
    Capabilities,
    EventKind,
    RawOutcome,
    RunResult,
    RunSpec,
    YanShiEvent,
)
from yanshi.dispatch import dispatch
from yanshi.registry import AdapterRegistry


class EchoAdapter:
    name = "echo"
    prompt_mode = "stdin"
    seed_paths: list[str] = []
    capabilities = Capabilities(stream_json=False)

    def build_command(self, spec: RunSpec) -> BuiltCommand:
        return BuiltCommand(
            command=sys.executable,
            args=["-c", "import sys; print(sys.stdin.read())"],
            stdin_text=spec.prompt,
        )

    def parse_event(self, raw_line: str) -> YanShiEvent | None:
        return YanShiEvent(kind=EventKind.ASSISTANT_TEXT, text=raw_line)

    def parse_result(self, outcome: RawOutcome) -> RunResult:
        return RunResult(
            agent_id="echo-1",
            cli=self.name,
            state=AgentState.SUCCEEDED if outcome.exit_code == 0 else AgentState.FAILED,
            is_error=outcome.exit_code != 0,
            reply=outcome.stdout.strip(),
            exit_code=outcome.exit_code,
        )

    def session_id_from_event(self, ev: dict[str, object]) -> str | None:
        return None


def test_dispatch_runs_adapter_command_and_parses_result() -> None:
    registry = AdapterRegistry()
    registry.register(EchoAdapter())
    result = dispatch(
        RunSpec(cli="echo", prompt="hello"),
        registry=registry,
        skip_preflight=True,
    )
    assert result.reply == "hello"
    assert result.state == AgentState.SUCCEEDED
