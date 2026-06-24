"""Tiny importable wrappers for MCP hosts that expose Python callables.

This module intentionally avoids a hard dependency on a specific MCP framework.
Hosts can import these functions and bind them to their own transport.
"""

from __future__ import annotations

import asyncio

from yanshi.contracts import RunSpec
from yanshi.dispatch import cancel, dispatch_wait, status, summary, wait


def dispatch(prompt: str, cli: str = "claude") -> dict[str, object]:
    """Blocking dispatch wrapper returning a JSON-ready RunResult dict."""

    result = asyncio.run(dispatch_wait(RunSpec(cli=cli, prompt=prompt)))
    return result.model_dump(mode="json")


def get_status(agent_id: str) -> dict[str, object]:
    """Return a JSON-ready AgentStatus dict."""

    return status(agent_id).model_dump(mode="json")


def get_summary(agent_id: str) -> str:
    """Return advisory summary text."""

    return summary(agent_id)


def wait_for(agent_id: str, timeout_s: float | None = None) -> dict[str, object]:
    """Wait for terminal state and return status."""

    return asyncio.run(wait(agent_id, timeout_s=timeout_s)).model_dump(mode="json")


def cancel_agent(agent_id: str) -> dict[str, object]:
    """Cancel an agent and return status."""

    return cancel(agent_id).model_dump(mode="json")
