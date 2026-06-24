"""CLI installation/authentication preflight checks."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from yanshi.adapters.base import Adapter
from yanshi.errors import ErrorCategory, PreflightError
from yanshi.registry import AdapterRegistry, default_registry


@dataclass(frozen=True)
class PreflightResult:
    """Result of checking one adapter before dispatch."""

    cli: str
    ok: bool
    executable: str | None = None
    version: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def require_ok(self) -> None:
        """Raise a categorized preflight error if the check failed."""

        if self.ok:
            return
        auth_failed = any(
            "auth" in error.lower() or "credential" in error.lower() for error in self.errors
        )
        raise PreflightError(
            "; ".join(self.errors) or f"preflight failed for {self.cli}",
            category=ErrorCategory.AUTH if auth_failed else ErrorCategory.INVALID_REQUEST,
            detail={"cli": self.cli, "errors": self.errors},
        )


def preflight_adapter(adapter: Adapter, *, env: dict[str, str] | None = None) -> PreflightResult:
    """Check binary presence, version, and basic auth seed paths."""

    effective_env = dict(os.environ)
    if env:
        effective_env.update(env)
    executable = shutil.which(adapter.name, path=effective_env.get("PATH"))
    errors: list[str] = []
    warnings: list[str] = []
    if executable is None:
        errors.append(f"missing CLI executable: {adapter.name}")
        return PreflightResult(cli=adapter.name, ok=False, errors=errors)

    version = _detect_version(executable)
    if version is None:
        warnings.append(f"could not detect version for {adapter.name}")

    if adapter.name == "claude" and not _claude_auth_exists(effective_env):
        errors.append("claude authentication seed not found")

    return PreflightResult(
        cli=adapter.name,
        ok=not errors,
        executable=executable,
        version=version,
        errors=errors,
        warnings=warnings,
    )


def doctor(registry: AdapterRegistry | None = None) -> list[PreflightResult]:
    """Run preflight checks for every registered adapter."""

    effective_registry = registry or default_registry()
    return [
        preflight_adapter(effective_registry.get(name))
        for name in effective_registry.list_names()
    ]


def _detect_version(executable: str) -> str | None:
    for args in ([executable, "--version"], [executable, "-v"]):
        try:
            outcome = subprocess.run(
                args,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = (outcome.stdout + "\n" + outcome.stderr).strip()
        if output:
            return output.splitlines()[0]
    return None


def _claude_auth_exists(env: dict[str, str]) -> bool:
    if env.get("CLAUDE_CODE_OAUTH_TOKEN") or env.get("ANTHROPIC_API_KEY"):
        return True
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        root = Path(config_dir).expanduser()
        if (root / ".credentials.json").is_file() or (root / "auth.json").is_file():
            return True
    home_raw = env.get("HOME")
    if not home_raw:
        return False
    home = Path(home_raw).expanduser()
    return any(
        path.is_file()
        for path in (
            home / ".claude.json",
            home / ".claude" / ".credentials.json",
            home / ".claude" / "auth.json",
        )
    )
