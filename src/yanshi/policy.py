"""Dispatch policy validation."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yanshi.contracts import AllowMode, Capabilities, RunSpec, WarningRecord
from yanshi.errors import ErrorCategory, YanShiError
from yanshi.paths import validate_workdir_and_add_dirs


class DispatchPolicy(BaseModel):
    """Caller-controlled safety policy."""

    model_config = ConfigDict(extra="forbid")

    allow: AllowMode = AllowMode.READ_ONLY
    workdir: str | None = None
    add_dirs: list[str] = Field(default_factory=list)
    trusted_root: str | None = None
    cost_ceiling_usd: float | None = None
    redaction: bool = True
    approval_required: bool = False

    @field_validator("cost_ceiling_usd")
    @classmethod
    def _positive_cost(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("cost_ceiling_usd must be positive")
        return value


def policy_from_spec(spec: RunSpec) -> DispatchPolicy:
    """Create a policy view from a RunSpec."""

    return DispatchPolicy(
        allow=spec.allow,
        workdir=spec.workdir,
        add_dirs=spec.add_dirs,
        cost_ceiling_usd=spec.cost_ceiling_usd,
    )


def validate_policy(
    spec: RunSpec,
    capabilities: Capabilities,
    *,
    trusted_root: str | Path | None = None,
) -> list[WarningRecord]:
    """Validate RunSpec policy against paths and adapter capabilities."""

    warnings: list[WarningRecord] = []
    if spec.allow not in capabilities.permission_modes:
        raise YanShiError(
            f"adapter does not support allow={spec.allow}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"allow": spec.allow.value},
        )
    if spec.allow == AllowMode.READ_ONLY and spec.add_dirs:
        raise YanShiError(
            "read-only dispatch cannot request writable add_dirs",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"add_dirs": spec.add_dirs},
        )
    if spec.workdir is not None:
        validate_workdir_and_add_dirs(
            spec.workdir,
            spec.add_dirs,
            root=trusted_root,
        )
    if spec.reasoning_effort and capabilities.effort == "none":
        warnings.append(
            WarningRecord(
                code="capability_unavailable",
                message="adapter cannot express reasoning_effort; request will be downgraded",
                detail={"field": "reasoning_effort"},
            )
        )
    if spec.output_schema and not capabilities.output_schema:
        warnings.append(
            WarningRecord(
                code="capability_unavailable",
                message="adapter cannot express output_schema; request will be downgraded",
                detail={"field": "output_schema"},
            )
        )
    if not capabilities.context_window_flag:
        warnings.append(
            WarningRecord(
                code="context_window_unavailable",
                message=(
                    "agent CLIs do not expose context-window control; "
                    "only input size is controlled"
                ),
            )
        )
    return warnings
