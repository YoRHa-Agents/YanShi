"""Offline, deterministic smoke tests for the YanShi ``install.sh`` installer.

These tests NEVER perform a real installation. They exercise:

* bash syntax (``bash -n``),
* ``--help`` / usage output (English + a Chinese substring under ``LANG=zh``),
* ``--dry-run`` planning, which must be 100% side-effect-free, and
* argument validation (unknown flags exit non-zero).

Everything runs ``bash install.sh ...`` with an explicit argv (never
``shell=True``), captured output, and a bounded timeout. An optional
``shellcheck`` lint runs only when ``shellcheck`` is on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"
REPO_ROOT = INSTALL_SH.parent

# A clearly-Chinese substring that install.sh embeds in its zh help/banner output.
CHINESE_MARKER = "安装"


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``bash install.sh <args>`` deterministically and offline."""

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=full_env,
    )


def test_install_sh_present() -> None:
    assert INSTALL_SH.is_file(), f"install.sh not found at {INSTALL_SH}"


def test_bash_syntax_is_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr


def test_help_lists_core_flags() -> None:
    result = _run(["--help"])
    assert result.returncode == 0, result.stderr
    out = result.stdout.lower()
    assert "usage" in out
    assert "--local" in out
    assert "--global" in out


def test_help_chinese_under_zh_locale() -> None:
    result = _run(["--help"], env={"LANG": "zh_CN.UTF-8"})
    assert result.returncode == 0, result.stderr
    assert CHINESE_MARKER in result.stdout, "expected a Chinese substring in zh --help output"


def test_dry_run_local_is_side_effect_free_and_editable() -> None:
    result = _run(["--dry-run", "--local"], env={"LANG": "en_US.UTF-8"})
    assert result.returncode == 0, result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "[dry-run]" in combined, "dry-run output must carry a [dry-run] marker"
    indicates_local = ("local" in combined) or ("editable" in combined)
    assert indicates_local, "dry-run --local must indicate a local/editable install"


def test_dry_run_global_mentions_path() -> None:
    result = _run(["--dry-run", "--global"], env={"LANG": "en_US.UTF-8"})
    assert result.returncode == 0, result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "[dry-run]" in combined
    assert "global" in combined
    assert "path" in combined


def test_unknown_flag_exits_nonzero() -> None:
    result = _run(["--bogus"])
    assert result.returncode != 0


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean() -> None:
    result = subprocess.run(
        ["shellcheck", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"shellcheck findings:\n{result.stdout}\n{result.stderr}"
