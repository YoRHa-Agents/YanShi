"""Ultra-light one-shot agent-CLI summary client (governance G2.6/G2.7/G11.4).

This client performs a single, NON-monitored agent-CLI call that turns a compact
event digest into a short natural-language status line. It is deliberately
dependency-light: no monitor kernel, no disk writes, no recursive dispatch
(G11.4). Every failure -- whether the subprocess raised or the CLI returned no
usable reply -- is surfaced as a :class:`RuntimeError` so the calling
``RollingSummarizer`` degrades to its deterministic fallback (G2.7) instead of
ever blocking or aborting the monitored run.
"""

from __future__ import annotations

import asyncio

from yanshi.contracts import AllowMode, PromptMode, RunResult, RunSpec
from yanshi.registry import AdapterRegistry, default_registry

# Module-level rebindable seam so tests can monkeypatch the runner primitive
# (yanshi.summary_client._run_blocking) without importing yanshi.dispatch and
# risking an import cycle.
from yanshi.runner import run_blocking as _run_blocking

__all__ = ["AgentCliSummaryClient"]

_SUMMARY_INSTRUCTION = (
    "You are a monitoring assistant. In 1-3 short sentences, summarize the "
    "sub-agent's current progress and health from the structured events below. "
    "Output ONLY the summary, no preamble.\n\n"
)


class AgentCliSummaryClient:
    """One-shot, non-monitored agent-CLI implementation of ``SummaryClient``."""

    def __init__(
        self,
        *,
        cli: str,
        model: str | None = None,
        registry: AdapterRegistry | None = None,
        timeout_s: int = 60,
    ) -> None:
        self.cli = cli
        self.model = model
        self._registry = registry or default_registry()
        self.timeout_s = timeout_s

    async def summarize(self, prompt: str) -> str:
        """Run one lightweight agent-CLI call and return its reply text.

        The structured ``prompt`` (already a compact event digest produced by
        ``RollingSummarizer``) is wrapped with a tight instruction and dispatched
        as a single READ_ONLY call. The blocking subprocess runs in a worker
        thread so the monitor event loop is never blocked. All call failures are
        re-raised as :class:`RuntimeError` (G2.7).
        """

        spec = RunSpec(
            cli=self.cli,
            prompt=_SUMMARY_INSTRUCTION + prompt,
            model=self.model,
            allow=AllowMode.READ_ONLY,
            prompt_mode=PromptMode.STDIN,
            timeout_s=self.timeout_s,
        )
        try:
            result = await asyncio.to_thread(self._run_once, spec)
        except Exception as exc:  # noqa: BLE001 - degrade to deterministic fallback (G2.7).
            raise RuntimeError(f"summary watcher call failed: {exc}") from exc
        if result.is_error or not result.reply:
            raise RuntimeError(
                f"summary watcher returned no usable reply: state={result.state}"
            )
        return result.reply.strip()

    def _run_once(self, spec: RunSpec) -> RunResult:
        """Build, run, and parse a single agent-CLI invocation (no monitoring)."""

        adapter = self._registry.get(spec.cli)
        command = adapter.build_command(spec)
        outcome = _run_blocking(command, timeout_s=spec.timeout_s)
        return adapter.parse_result(outcome)
