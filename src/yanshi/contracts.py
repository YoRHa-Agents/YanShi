"""Core YanShi contracts.

The models in this file mirror the normative design in
`.local/memory/specs/yanshi/spec.md` §3. They are intentionally independent of
any specific CLI dialect; adapters translate between these contracts and vendor
flags/events.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PromptMode(StrEnum):
    """How the prompt is supplied to the child CLI."""

    STDIN = "stdin"
    ARGUMENT = "argument"


class AllowMode(StrEnum):
    """Permission model requested by the caller."""

    READ_ONLY = "read-only"
    YOLO = "yolo"


class SessionMode(StrEnum):
    """Session mode requested for the native CLI."""

    NEW = "new"
    RESUME = "resume"


class AgentState(StrEnum):
    """Normalized YanShi agent state."""

    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_RATE_LIMIT = "waiting_rate_limit"
    WAITING_TOOL = "waiting_tool"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALLED = "stalled"
    CANCELLED = "cancelled"
    KILLED = "killed"


TERMINAL_STATES: frozenset[AgentState] = frozenset(
    {
        AgentState.SUCCEEDED,
        AgentState.FAILED,
        AgentState.STALLED,
        AgentState.CANCELLED,
        AgentState.KILLED,
    }
)


class EventKind(StrEnum):
    """Normalized event kinds produced by adapters."""

    STARTED = "started"
    ASSISTANT_TEXT = "assistant_text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    REASONING = "reasoning"
    FILE_CHANGE = "file_change"
    USAGE = "usage"
    ERROR = "error"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class PricingStatus(StrEnum):
    """Cost-pricing provenance."""

    NATIVE = "native"
    PRICED = "priced"
    MISSING = "missing"


class CapabilityMode(StrEnum):
    """How a capability is represented by a CLI."""

    FLAG = "flag"
    CONFIG = "config"
    MODEL_SUFFIX = "model_suffix"
    THINKING_LEVEL = "thinking_level"
    NONE = "none"


class Usage(BaseModel):
    """Token usage normalized across CLIs."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @field_validator("*")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("token counts must be non-negative")
        return value

    @property
    def total(self) -> int:
        """Total tokens including cached and reasoning tokens."""

        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )


class RunSpec(BaseModel):
    """Everything needed to dispatch a task to an agent CLI."""

    model_config = ConfigDict(extra="forbid")

    cli: str
    prompt: str
    prompt_mode: PromptMode = PromptMode.STDIN
    model: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    allow: AllowMode = AllowMode.READ_ONLY
    workdir: str | None = None
    add_dirs: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: int | None = None
    stall_timeout_s: int | None = None
    session_mode: SessionMode = SessionMode.NEW
    session_id: str | None = None
    session_alias: str | None = None
    output_schema: dict[str, Any] | None = None
    cost_ceiling_usd: float | None = None

    @field_validator("cli", "prompt")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must be non-empty")
        return value

    @field_validator("timeout_s", "stall_timeout_s")
    @classmethod
    def _positive_timeout(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("timeouts must be positive seconds")
        return value

    @field_validator("cost_ceiling_usd")
    @classmethod
    def _positive_cost_ceiling(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("cost ceiling must be positive")
        return value


class BuiltCommand(BaseModel):
    """Subprocess command prepared by an adapter but not executed by it."""

    model_config = ConfigDict(extra="forbid")

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    stdin_file: str | None = None
    stdin_text: str | None = None

    @field_validator("command")
    @classmethod
    def _command_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command must be non-empty")
        return value


class RawOutcome(BaseModel):
    """Raw subprocess outcome before adapter-specific parsing."""

    model_config = ConfigDict(extra="forbid")

    command: str
    args: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int | None = None
    timed_out: bool = False


class YanShiEvent(BaseModel):
    """Normalized event emitted by an adapter."""

    model_config = ConfigDict(extra="forbid")

    kind: EventKind
    text: str = ""
    usage: Usage | None = None
    err: str | None = None
    raw: str = ""
    ts: float = 0.0
    session_id: str | None = None
    cost_usd: float | None = None
    is_error: bool | None = None


class WarningRecord(BaseModel):
    """Structured warning surfaced to callers."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorRecord(BaseModel):
    """Structured error surfaced to callers."""

    model_config = ConfigDict(extra="forbid")

    category: str
    message: str
    fatal: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class LastEvent(BaseModel):
    """Compact last-event reference for parent-agent polling."""

    model_config = ConfigDict(extra="forbid")

    kind: EventKind | None = None
    summary: str | None = None
    ts: float | None = None


class Liveness(BaseModel):
    """Liveness data derived deterministically by the monitor."""

    model_config = ConfigDict(extra="forbid")

    idle_seconds: float | None = None
    stalled: bool = False
    waiting_reason: str | None = None


class AgentStatus(BaseModel):
    """The compact status object parent agents are allowed to pull."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    cli: str
    state: AgentState = AgentState.PENDING
    session_id: str | None = None
    model: str | None = None
    progress_pct: int | None = None
    last_event: LastEvent = Field(default_factory=LastEvent)
    liveness: Liveness = Field(default_factory=Liveness)
    counters: dict[str, int] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None
    pricing_status: PricingStatus = PricingStatus.MISSING
    errors: list[ErrorRecord] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    rolling_summary: str = ""
    owner_pid: int | None = None
    child_pid: int | None = None
    started_at: float | None = None
    updated_at: float | None = None


class Capabilities(BaseModel):
    """Capabilities declared by an adapter."""

    model_config = ConfigDict(extra="forbid")

    effort: CapabilityMode = CapabilityMode.NONE
    context_window_flag: bool = False
    session_resume: bool = False
    preassign_session_id: bool = False
    output_schema: bool = False
    stream_json: bool = False
    permission_modes: list[AllowMode] = Field(default_factory=lambda: [AllowMode.READ_ONLY])


class RunResult(BaseModel):
    """Terminal result of a YanShi dispatch."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    cli: str
    state: AgentState
    is_error: bool
    reply: str | None = None
    structured_output: dict[str, Any] | None = None
    session_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None
    pricing_status: PricingStatus = PricingStatus.MISSING
    exit_code: int | None = None
    duration_ms: int | None = None
    error_category: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    log_dir: str = ""
    warnings: list[WarningRecord] = Field(default_factory=list)


class FleetStatus(BaseModel):
    """Deterministic aggregate status for a group of agents."""

    model_config = ConfigDict(extra="forbid")

    agent_ids: list[str]
    state_counts: dict[AgentState, int] = Field(default_factory=dict)
    total_usage: Usage = Field(default_factory=Usage)
    total_cost_usd: float | None = None
    blockers: list[ErrorRecord] = Field(default_factory=list)


class GateOutcome(BaseModel):
    """Result of running a deterministic improve-loop gate (e.g. tests/linter)."""

    model_config = ConfigDict(extra="forbid")

    ran: bool
    passed: bool
    exit_code: int | None = None
    output_excerpt: str = ""  # truncated stdout+stderr, kept low-context
    error: str | None = None  # gate-execution failure (distinct from a test failure)


class ImproveSpec(BaseModel):
    """Configuration for a bounded iterative improve loop."""

    model_config = ConfigDict(extra="forbid")

    spec: RunSpec  # first-iteration dispatch template
    check_command: list[str] | None = None  # deterministic gate argv; exit 0 = pass
    gate_timeout_s: int | None = 300
    max_iterations: int = 3  # validated >= 1 (governance G4.5)
    use_critic: bool = False
    critic_threshold: float = 0.8
    gate_output_limit: int = 4000  # chars of gate output fed into the refine prompt

    @field_validator("max_iterations")
    @classmethod
    def _at_least_one(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_iterations must be >= 1")
        return value

    @field_validator("gate_timeout_s")
    @classmethod
    def _positive_gate_timeout(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("gate_timeout_s must be positive seconds")
        return value

    @field_validator("critic_threshold")
    @classmethod
    def _threshold_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("critic_threshold must be within [0.0, 1.0]")
        return value

    @field_validator("gate_output_limit")
    @classmethod
    def _positive_output_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("gate_output_limit must be positive")
        return value


class ImproveIteration(BaseModel):
    """One dispatch + evaluation cycle of an improve loop."""

    model_config = ConfigDict(extra="forbid")

    index: int
    agent_id: str
    state: AgentState
    is_error: bool
    gate: GateOutcome | None = None
    critic_feedback: str = ""
    critic_score: float | None = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None


class ImproveResult(BaseModel):
    """Terminal result of an improve loop."""

    model_config = ConfigDict(extra="forbid")

    iterations: list[ImproveIteration] = Field(default_factory=list)
    succeeded: bool
    stop_reason: Literal[
        "gate_passed",
        "critic_threshold",
        "max_iterations",
        "fatal_error",
        "no_evaluator",
    ]
    final_agent_id: str | None = None
    total_usage: Usage = Field(default_factory=Usage)
    total_cost_usd: float | None = None
    warnings: list[WarningRecord] = Field(default_factory=list)
