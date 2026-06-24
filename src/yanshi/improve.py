"""Bounded iterative improve loop: dispatch -> evaluate -> refine.

This module builds a higher-level primitive on top of :class:`MonitorKernel`. It
dispatches a sub-agent, runs a deterministic gate (e.g. tests/linter), and - if
the gate fails - re-dispatches with the failure fed back into the prompt, until
the gate passes or ``max_iterations`` is reached.

It preserves YanShi's deterministic-first / low-context philosophy: the gate is
authoritative; an optional LLM ``Critic`` is advisory only (mirroring the
advisory rolling summarizer). Governance honored: G4.5 (bounded loop + every
wait wrapped in ``asyncio.wait_for``), G8 (argv-only subprocess, never
``shell=True``), and "No Silent Failures" (gate/critic/dispatch errors are
surfaced via ``GateOutcome.error`` / ``WarningRecord`` / a terminal
``fatal_error`` result, never swallowed).
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from yanshi.contracts import (
    GateOutcome,
    ImproveIteration,
    ImproveResult,
    ImproveSpec,
    RunResult,
    RunSpec,
    SessionMode,
    Usage,
    WarningRecord,
)
from yanshi.errors import AdapterError
from yanshi.monitor import MonitorKernel, terminate_process
from yanshi.registry import AdapterRegistry, default_registry
from yanshi.runner import build_child_env
from yanshi.store import StatusStore

GateRunner = Callable[..., Awaitable[GateOutcome]]


class Critic(Protocol):
    """Minimal async LLM critic protocol (advisory only)."""

    async def critique(self, prompt: str) -> tuple[float, str]:
        """Return ``(score, feedback)`` where ``score`` is in ``[0.0, 1.0]``."""


class KernelLike(Protocol):
    """Subset of :class:`MonitorKernel` used by the improve loop."""

    async def run(
        self,
        spec: RunSpec,
        *,
        agent_id: str | None = ...,
        skip_preflight: bool = ...,
    ) -> RunResult:
        """Dispatch one run to terminal state."""


async def run_argv_gate(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int | None = None,
    output_limit: int = 4000,
) -> GateOutcome:
    """Run a deterministic gate as an argv-only subprocess (exit 0 == pass).

    The gate is spawned via ``asyncio.create_subprocess_exec`` (never
    ``shell=True``; G8). The wait is wrapped in ``asyncio.wait_for`` when a
    timeout is set (G4.5). Spawn and timeout failures are returned in
    ``GateOutcome.error`` rather than raised (No Silent Failures).
    """

    if not argv:
        return GateOutcome(ran=False, passed=False, error="empty gate command")

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=build_child_env(env),
            start_new_session=os.name != "nt",
        )
    except (FileNotFoundError, OSError) as exc:
        return GateOutcome(ran=False, passed=False, error=f"gate spawn failed: {exc}")

    try:
        if timeout_s is not None:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        else:
            stdout, _ = await proc.communicate()
    except TimeoutError:
        await terminate_process(proc, final_signal=signal.SIGKILL)
        return GateOutcome(
            ran=True,
            passed=False,
            exit_code=None,
            error=f"gate timed out after {timeout_s}s",
        )

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    exit_code = proc.returncode
    return GateOutcome(
        ran=True,
        passed=exit_code == 0,
        exit_code=exit_code,
        output_excerpt=_tail(output, output_limit),
    )


async def improve_loop(
    plan: ImproveSpec,
    *,
    registry: AdapterRegistry | None = None,
    store: StatusStore | None = None,
    kernel: KernelLike | None = None,
    gate_runner: GateRunner = run_argv_gate,
    critic_client: Critic | None = None,
    skip_preflight: bool = False,
) -> ImproveResult:
    """Run a bounded dispatch -> evaluate -> refine loop.

    The deterministic ``check_command`` gate is authoritative. The optional
    ``critic_client`` is advisory and only consulted when no gate is configured.
    The loop is bounded by ``plan.max_iterations`` (G4.5).
    """

    effective_kernel: KernelLike = kernel or MonitorKernel(registry=registry, store=store)
    base_id = f"ys-improve-{uuid.uuid4()}"
    current_spec = plan.spec

    iterations: list[ImproveIteration] = []
    warnings: list[WarningRecord] = []
    total_usage = Usage()
    total_cost: float | None = None
    final_agent_id: str | None = None

    for index in range(plan.max_iterations):
        agent_id = f"{base_id}-iter{index}"
        final_agent_id = agent_id
        try:
            result = await effective_kernel.run(
                current_spec, agent_id=agent_id, skip_preflight=skip_preflight
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a terminal fatal_error.
            warnings.append(
                WarningRecord(
                    code="dispatch_error",
                    message=str(exc),
                    detail={"agent_id": agent_id, "index": index},
                )
            )
            return ImproveResult(
                iterations=iterations,
                succeeded=False,
                stop_reason="fatal_error",
                final_agent_id=agent_id,
                total_usage=total_usage,
                total_cost_usd=total_cost,
                warnings=warnings,
            )

        gate: GateOutcome | None = None
        if plan.check_command is not None:
            gate = await gate_runner(
                plan.check_command,
                cwd=current_spec.workdir,
                env=current_spec.env,
                timeout_s=plan.gate_timeout_s,
                output_limit=plan.gate_output_limit,
            )
            if gate.error is not None:
                warnings.append(
                    WarningRecord(
                        code="gate_error",
                        message=gate.error,
                        detail={"agent_id": agent_id, "index": index},
                    )
                )

        critic_feedback = ""
        critic_score: float | None = None
        if plan.use_critic:
            if critic_client is None:
                warnings.append(
                    WarningRecord(
                        code="critic_unavailable",
                        message="critic requested but no critic client provided",
                        detail={"agent_id": agent_id, "index": index},
                    )
                )
            else:
                try:
                    critic_score, critic_feedback = await critic_client.critique(
                        _critic_prompt(current_spec, result, gate)
                    )
                except Exception as exc:  # noqa: BLE001 - advisory critic, degrade gracefully.
                    warnings.append(
                        WarningRecord(
                            code="critic_error",
                            message=str(exc),
                            detail={"agent_id": agent_id, "index": index},
                        )
                    )

        iterations.append(
            ImproveIteration(
                index=index,
                agent_id=agent_id,
                state=result.state,
                is_error=result.is_error,
                gate=gate,
                critic_feedback=critic_feedback,
                critic_score=critic_score,
                usage=result.usage,
                cost_usd=result.cost_usd,
            )
        )
        total_usage = _add_usage(total_usage, result.usage)
        total_cost = _add_cost(total_cost, result.cost_usd)

        # Success / stop evaluation (deterministic gate first, critic advisory).
        if gate is not None:
            if gate.ran and gate.passed:
                return _finalize(
                    iterations, True, "gate_passed", agent_id, total_usage, total_cost, warnings
                )
        elif plan.use_critic:
            if critic_score is not None and critic_score >= plan.critic_threshold:
                return _finalize(
                    iterations,
                    True,
                    "critic_threshold",
                    agent_id,
                    total_usage,
                    total_cost,
                    warnings,
                )
        else:
            # Neither a gate nor a critic was configured: single pass.
            return _finalize(
                iterations,
                not result.is_error,
                "no_evaluator",
                agent_id,
                total_usage,
                total_cost,
                warnings,
            )

        if index == plan.max_iterations - 1:
            break

        current_spec = _refine_spec(plan, current_spec, result, gate, critic_feedback, registry)

    return _finalize(
        iterations, False, "max_iterations", final_agent_id, total_usage, total_cost, warnings
    )


def _refine_spec(
    plan: ImproveSpec,
    current_spec: RunSpec,
    result: RunResult,
    gate: GateOutcome | None,
    critic_feedback: str,
    registry: AdapterRegistry | None,
) -> RunSpec:
    """Build the next iteration's spec, resuming the session when supported."""

    refine_prompt = _refine_prompt(plan.spec.prompt, gate, critic_feedback)

    if result.session_id:
        effective_registry = registry or default_registry()
        try:
            caps = effective_registry.capabilities(current_spec.cli)
        except AdapterError:
            caps = None
        if caps is not None and caps.session_resume:
            return current_spec.model_copy(
                update={
                    "prompt": refine_prompt,
                    "session_mode": SessionMode.RESUME,
                    "session_id": result.session_id,
                }
            )

    # Fall back to a fresh dispatch carrying the embedded failure context.
    return plan.spec.model_copy(update={"prompt": refine_prompt})


def _refine_prompt(task: str, gate: GateOutcome | None, critic_feedback: str) -> str:
    sections = [task, "--- Previous attempt did not pass. Please fix the issues and try again. ---"]
    if gate is not None and gate.output_excerpt:
        sections.append(f"Gate output:\n{gate.output_excerpt}")
    if gate is not None and gate.error:
        sections.append(f"Gate error:\n{gate.error}")
    if critic_feedback:
        sections.append(f"Critic feedback:\n{critic_feedback}")
    return "\n\n".join(sections)


def _critic_prompt(spec: RunSpec, result: RunResult, gate: GateOutcome | None) -> str:
    lines = [
        f"task={spec.prompt}",
        f"agent_state={result.state.value}",
        f"is_error={result.is_error}",
    ]
    if result.reply:
        lines.append(f"reply={_tail(result.reply, 1000)}")
    if gate is not None:
        lines.append(f"gate_passed={gate.passed}")
        if gate.output_excerpt:
            lines.append(f"gate_output={gate.output_excerpt}")
    return "\n".join(lines)


def _finalize(
    iterations: list[ImproveIteration],
    succeeded: bool,
    stop_reason: str,
    final_agent_id: str | None,
    total_usage: Usage,
    total_cost: float | None,
    warnings: list[WarningRecord],
) -> ImproveResult:
    return ImproveResult(
        iterations=iterations,
        succeeded=succeeded,
        stop_reason=stop_reason,  # type: ignore[arg-type]
        final_agent_id=final_agent_id,
        total_usage=total_usage,
        total_cost_usd=total_cost,
        warnings=warnings,
    )


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
    )


def _add_cost(total: float | None, cost: float | None) -> float | None:
    if cost is None:
        return total
    return cost if total is None else total + cost


def _tail(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-limit:]
