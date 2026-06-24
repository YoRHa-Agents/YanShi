from __future__ import annotations

from pathlib import Path

import pytest

from yanshi.contracts import AllowMode, Capabilities, CapabilityMode, RunSpec
from yanshi.errors import YanShiError
from yanshi.policy import DispatchPolicy, policy_from_spec, validate_policy


def test_policy_from_spec_and_positive_cost_validation() -> None:
    spec = RunSpec(cli="claude", prompt="hi", cost_ceiling_usd=1.0)
    policy = policy_from_spec(spec)
    assert policy.allow == AllowMode.READ_ONLY
    assert policy.cost_ceiling_usd == 1.0
    with pytest.raises(ValueError):
        DispatchPolicy(cost_ceiling_usd=0)


def test_validate_policy_rejects_unsupported_allow() -> None:
    spec = RunSpec(cli="claude", prompt="hi", allow=AllowMode.YOLO)
    with pytest.raises(YanShiError):
        validate_policy(spec, Capabilities(permission_modes=[AllowMode.READ_ONLY]))


def test_validate_policy_rejects_read_only_add_dirs(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    spec = RunSpec(cli="claude", prompt="hi", workdir=str(tmp_path), add_dirs=[str(extra)])
    with pytest.raises(YanShiError):
        validate_policy(spec, Capabilities(permission_modes=[AllowMode.READ_ONLY]))


def test_validate_policy_checks_path_boundaries_and_warnings(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    spec = RunSpec(
        cli="limited",
        prompt="hi",
        workdir=str(workdir),
        reasoning_effort="high",
        output_schema={"type": "object"},
    )
    warnings = validate_policy(
        spec,
        Capabilities(
            effort=CapabilityMode.NONE,
            output_schema=False,
            permission_modes=[AllowMode.READ_ONLY],
        ),
        trusted_root=tmp_path,
    )
    assert {warning.code for warning in warnings} == {
        "capability_unavailable",
        "context_window_unavailable",
    }
