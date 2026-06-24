"""Tiny importable wrappers for MCP hosts that expose Python callables.

This module intentionally avoids a hard dependency on a specific MCP framework.
Hosts can import these functions and bind them to their own transport.
"""

from __future__ import annotations

import asyncio

from yanshi.config import enabled_adapter_names, load_config, resolve_dispatch
from yanshi.contracts import RunSpec
from yanshi.dispatch import cancel, dispatch_wait, status, summary, wait


def get_config() -> dict[str, object]:
    """Return the effective layered configuration for host visibility."""

    loaded = load_config()
    return {
        "config": loaded.config.model_dump(mode="json"),
        "enabled_adapters": enabled_adapter_names(loaded.config),
        "sources": [str(path) for path in loaded.sources],
        "provenance": loaded.provenance,
    }


def dispatch(prompt: str, cli: str = "claude", profile: str | None = None) -> dict[str, object]:
    """Blocking dispatch wrapper returning a JSON-ready RunResult dict.

    Dispatch defaults/profile/limits come from the layered repo config; any
    clamp/unknown-profile warnings are appended to the result ``warnings`` list
    so hosts never lose them silently.
    """

    resolved = resolve_dispatch({"cli": cli}, config=load_config().config, profile=profile)
    cli_name = resolved.kwargs.pop("cli", None) or cli
    spec = RunSpec(cli=cli_name, prompt=prompt, **resolved.kwargs)
    result = asyncio.run(dispatch_wait(spec))
    payload = result.model_dump(mode="json")
    warnings = list(payload.get("warnings") or [])
    warnings.extend(warning.model_dump(mode="json") for warning in resolved.warnings)
    payload["warnings"] = warnings
    return payload


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
