"""Supervisor watchdog, retry, and cost guard decisions."""

from __future__ import annotations

import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from yanshi.contracts import AgentState, AgentStatus, PricingStatus
from yanshi.errors import ErrorCategory


class SupervisorAction(StrEnum):
    """Action requested by the supervisor."""

    NONE = "none"
    WAIT = "wait"
    WARN = "warn"
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"
    KILL = "kill"
    RETRY = "retry"
    FAIL = "fail"


@dataclass(frozen=True)
class SupervisorDecision:
    """Watchdog decision with explicit reason."""

    action: SupervisorAction
    reason: str
    signal_to_send: signal.Signals | None = None


@dataclass(frozen=True)
class WatchdogConfig:
    """Thresholds for lifecycle and cost supervision."""

    wall_timeout_s: float = 1800
    stall_timeout_s: float = 300
    long_tool_timeout_s: float = 900
    per_run_cost_ceiling_usd: float | None = None
    missing_pricing_token_ceiling: int | None = None


class Watchdog:
    """Classify timeout, stall, and cost states using an injectable clock."""

    def __init__(
        self,
        config: WatchdogConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or WatchdogConfig()
        self.clock = clock or time.time

    def check(self, status: AgentStatus) -> SupervisorDecision:
        """Return the most severe current supervisory action."""

        now = self.clock()
        if self._wall_elapsed(status, now) >= self.config.wall_timeout_s:
            return SupervisorDecision(
                SupervisorAction.INTERRUPT,
                "wall_timeout",
                signal.SIGINT,
            )
        cost_decision = self._check_cost(status)
        if cost_decision.action != SupervisorAction.NONE:
            return cost_decision
        idle = self._idle_seconds(status, now)
        if status.state == AgentState.WAITING_RATE_LIMIT:
            return SupervisorDecision(SupervisorAction.WAIT, "waiting_rate_limit")
        if status.state == AgentState.WAITING_TOOL and idle < self.config.long_tool_timeout_s:
            return SupervisorDecision(SupervisorAction.WAIT, "waiting_tool")
        if status.state == AgentState.WAITING_TOOL and idle >= self.config.long_tool_timeout_s:
            return SupervisorDecision(
                SupervisorAction.INTERRUPT,
                "long_tool_timeout",
                signal.SIGINT,
            )
        if idle >= self.config.stall_timeout_s:
            return SupervisorDecision(SupervisorAction.INTERRUPT, "stall_timeout", signal.SIGINT)
        return SupervisorDecision(SupervisorAction.NONE, "healthy")

    def _check_cost(self, status: AgentStatus) -> SupervisorDecision:
        if (
            self.config.per_run_cost_ceiling_usd is not None
            and status.cost_usd is not None
            and status.cost_usd > self.config.per_run_cost_ceiling_usd
        ):
            return SupervisorDecision(SupervisorAction.INTERRUPT, "cost_exceeded", signal.SIGINT)
        if (
            self.config.missing_pricing_token_ceiling is not None
            and status.pricing_status == PricingStatus.MISSING
            and status.usage.total > self.config.missing_pricing_token_ceiling
        ):
            return SupervisorDecision(
                SupervisorAction.INTERRUPT,
                "token_ceiling_exceeded_pricing_missing",
                signal.SIGINT,
            )
        return SupervisorDecision(SupervisorAction.NONE, "cost_ok")

    def _wall_elapsed(self, status: AgentStatus, now: float) -> float:
        return now - (status.started_at if status.started_at is not None else now)

    def _idle_seconds(self, status: AgentStatus, now: float) -> float:
        baseline = status.updated_at
        if baseline is None:
            baseline = status.started_at
        if baseline is None:
            baseline = now
        return now - baseline


class RetryPolicy:
    """Bounded retry classifier."""

    def __init__(self, *, max_retries: int = 2, base_delay_s: float = 1.0) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if base_delay_s <= 0:
            raise ValueError("base_delay_s must be positive")
        self.max_retries: int = max_retries
        self.base_delay_s: float = base_delay_s

    def decide(self, category: ErrorCategory, *, attempt: int) -> SupervisorDecision:
        """Return retry/fail for an error category and attempt number."""

        if not category.retryable:
            return SupervisorDecision(SupervisorAction.FAIL, f"non_retryable:{category.value}")
        if attempt >= self.max_retries:
            return SupervisorDecision(SupervisorAction.FAIL, "retry_budget_exhausted")
        return SupervisorDecision(SupervisorAction.RETRY, f"retry_after:{self.delay(attempt)}")

    def delay(self, attempt: int) -> float:
        """Exponential backoff delay."""

        return float(self.base_delay_s * (2**attempt))


def escalation_signal(since_first_interrupt_s: float) -> signal.Signals:
    """SIGINT → SIGTERM → SIGKILL escalation ladder."""

    if since_first_interrupt_s < 5:
        return signal.SIGINT
    if since_first_interrupt_s < 10:
        return signal.SIGTERM
    return signal.SIGKILL
