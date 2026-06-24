from __future__ import annotations

import signal

import pytest

from yanshi.contracts import AgentState, PricingStatus, Usage
from yanshi.errors import ErrorCategory
from yanshi.reducer import initial_status
from yanshi.supervisor import (
    RetryPolicy,
    SupervisorAction,
    Watchdog,
    WatchdogConfig,
    escalation_signal,
)


def test_watchdog_detects_wall_timeout() -> None:
    status = initial_status("a1", "claude", now=0)
    decision = Watchdog(WatchdogConfig(wall_timeout_s=10), clock=lambda: 11).check(status)
    assert decision.action == SupervisorAction.INTERRUPT
    assert decision.reason == "wall_timeout"
    assert decision.signal_to_send == signal.SIGINT


def test_watchdog_distinguishes_rate_limit_long_tool_and_stall() -> None:
    rate_limited = initial_status("a1", "claude", now=0)
    rate_limited.state = AgentState.WAITING_RATE_LIMIT
    rate_limited.updated_at = 0
    watchdog = Watchdog(
        WatchdogConfig(wall_timeout_s=1000, stall_timeout_s=10, long_tool_timeout_s=20),
        clock=lambda: 15,
    )
    assert watchdog.check(rate_limited).reason == "waiting_rate_limit"

    tool = rate_limited.model_copy(deep=True)
    tool.state = AgentState.WAITING_TOOL
    assert watchdog.check(tool).reason == "waiting_tool"

    stalled_tool = tool.model_copy(deep=True)
    stalled_tool.updated_at = -10
    assert watchdog.check(stalled_tool).reason == "long_tool_timeout"

    running = rate_limited.model_copy(deep=True)
    running.state = AgentState.RUNNING
    assert watchdog.check(running).reason == "stall_timeout"


def test_watchdog_cost_and_missing_pricing_token_ceiling() -> None:
    status = initial_status("a1", "claude", now=0)
    status.cost_usd = 2
    decision = Watchdog(
        WatchdogConfig(wall_timeout_s=100, per_run_cost_ceiling_usd=1),
        clock=lambda: 1,
    ).check(status)
    assert decision.reason == "cost_exceeded"

    missing = initial_status("a2", "claude", now=0)
    missing.pricing_status = PricingStatus.MISSING
    missing.usage = Usage(input_tokens=11)
    token_decision = Watchdog(
        WatchdogConfig(wall_timeout_s=100, missing_pricing_token_ceiling=10),
        clock=lambda: 1,
    ).check(missing)
    assert token_decision.reason == "token_ceiling_exceeded_pricing_missing"


def test_retry_policy_and_escalation() -> None:
    policy = RetryPolicy(max_retries=2, base_delay_s=2)
    assert policy.decide(ErrorCategory.RATE_LIMIT, attempt=0).action == SupervisorAction.RETRY
    assert policy.decide(ErrorCategory.RATE_LIMIT, attempt=2).action == SupervisorAction.FAIL
    assert policy.decide(ErrorCategory.AUTH, attempt=0).reason == "non_retryable:auth"
    assert policy.delay(2) == 8
    assert escalation_signal(0) == signal.SIGINT
    assert escalation_signal(6) == signal.SIGTERM
    assert escalation_signal(11) == signal.SIGKILL


def test_retry_policy_rejects_invalid_config() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_retries=-1)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_s=0)
