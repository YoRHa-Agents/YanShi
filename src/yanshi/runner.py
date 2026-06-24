"""Subprocess runner for YanShi adapters."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from yanshi.contracts import BuiltCommand, RawOutcome

_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "USER",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
}


def build_child_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build a filtered child environment plus explicit caller overrides."""

    child_env = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
    if extra_env:
        child_env.update(extra_env)
    return child_env


def run_blocking(command: BuiltCommand, *, timeout_s: int | None = None) -> RawOutcome:
    """Run a prepared command to completion with argv-only subprocess spawning."""

    stdin_text = command.stdin_text
    if command.stdin_file is not None:
        stdin_text = Path(command.stdin_file).read_text(encoding="utf-8")

    started = time.monotonic()
    try:
        completed = subprocess.run(
            [command.command, *command.args],
            shell=False,
            check=False,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=command.cwd,
            env=build_child_env(command.env),
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return RawOutcome(
            command=command.command,
            args=command.args,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr)
        return RawOutcome(
            command=command.command,
            args=command.args,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=True,
        )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
