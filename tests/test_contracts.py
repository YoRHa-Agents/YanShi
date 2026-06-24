from __future__ import annotations

import pytest
from pydantic import ValidationError

from yanshi.contracts import (
    AgentState,
    AllowMode,
    Capabilities,
    CapabilityMode,
    RunResult,
    RunSpec,
    Usage,
)


def test_usage_total_includes_all_token_buckets() -> None:
    usage = Usage(
        input_tokens=10,
        cached_input_tokens=3,
        output_tokens=5,
        reasoning_tokens=7,
    )
    assert usage.total == 25


def test_usage_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)


def test_runspec_defaults_match_governance() -> None:
    spec = RunSpec(cli="claude", prompt="hello")
    assert spec.prompt_mode == "stdin"
    assert spec.allow == AllowMode.READ_ONLY
    assert spec.session_mode == "new"
    assert spec.env == {}
    assert spec.add_dirs == []


def test_runspec_rejects_empty_prompt_and_invalid_timeout() -> None:
    with pytest.raises(ValidationError):
        RunSpec(cli="claude", prompt="")
    with pytest.raises(ValidationError):
        RunSpec(cli="claude", prompt="hello", timeout_s=0)


def test_runresult_json_roundtrip() -> None:
    result = RunResult(agent_id="a1", cli="claude", state=AgentState.SUCCEEDED, is_error=False)
    payload = result.model_dump_json()
    parsed = RunResult.model_validate_json(payload)
    assert parsed == result


def test_capabilities_allow_permission_modes() -> None:
    caps = Capabilities(
        effort=CapabilityMode.FLAG,
        stream_json=True,
        permission_modes=[AllowMode.READ_ONLY, AllowMode.YOLO],
    )
    assert caps.effort == CapabilityMode.FLAG
    assert AllowMode.YOLO in caps.permission_modes
