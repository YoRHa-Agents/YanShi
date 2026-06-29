"""End-to-end integration tests for ``install.sh``.

Unlike :mod:`tests.test_install_sh` (fast, offline, ``--dry-run`` only), these
tests actually *run* the installer against an isolated copy of the repository and
assert that the resulting ``yanshi`` CLI works. They build the package and create
virtualenvs / uv tools, so they are gated behind ``YANSHI_INSTALL_IT=1`` (the same
opt-in pattern as the ``live`` suite) and need ``uv`` plus a warm uv cache or
network access.

Run them explicitly with::

    YANSHI_INSTALL_IT=1 uv run pytest -m install_it -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The minimal set of paths needed to build + install yanshi from a clean copy.
_COPY_ITEMS = (
    "src",
    "skill",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "install.sh",
)

# Expected released version (kept in sync with pyproject / __init__).
EXPECTED_VERSION = "1.3.0"

pytestmark = [
    pytest.mark.install_it,
    pytest.mark.skipif(
        os.environ.get("YANSHI_INSTALL_IT") != "1",
        reason="real install integration tests; set YANSHI_INSTALL_IT=1 to run",
    ),
    pytest.mark.skipif(
        shutil.which("uv") is None,
        reason="uv is required for install integration tests",
    ),
]


def _copy_repo(dest: Path) -> Path:
    """Materialize a clean checkout-like copy of the repo at *dest*."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in _COPY_ITEMS:
        src = REPO_ROOT / item
        if not src.exists():
            continue
        target = dest / item
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
    return dest


def _run_install(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(repo / "install.sh"), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    return _copy_repo(tmp_path / "repo")


def test_local_install_produces_working_cli(repo_copy: Path) -> None:
    """`install.sh --local` yields an importable package + a runnable CLI."""
    # --no-skill keeps the test hermetic (skill registration is covered by its
    # own tests, which target an isolated --skill-dir).
    proc = _run_install(repo_copy, "--local", "--no-skill", "--lang", "en")
    assert proc.returncode == 0, f"install failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    venv_yanshi = repo_copy / ".venv" / "bin" / "yanshi"
    venv_python = repo_copy / ".venv" / "bin" / "python"
    assert venv_yanshi.is_file(), "yanshi entry point not created in .venv"

    version = subprocess.run(
        [str(venv_python), "-c", "import yanshi; print(yanshi.__version__)"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == EXPECTED_VERSION

    helptext = subprocess.run(
        [str(venv_yanshi), "--help"], capture_output=True, text=True, timeout=60
    )
    assert helptext.returncode == 0
    assert "dispatch" in helptext.stdout
    # The installer runs `yanshi doctor` post-install (informational only).
    assert "doctor" in proc.stdout.lower()


def test_local_install_with_docs_includes_mkdocs(repo_copy: Path) -> None:
    """`--docs` pulls the docs group so the site can be built locally."""
    proc = _run_install(repo_copy, "--local", "--docs", "--no-skill", "--lang", "en")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    mkdocs_bin = repo_copy / ".venv" / "bin" / "mkdocs"
    assert mkdocs_bin.is_file(), "docs group did not install mkdocs"


def test_local_install_is_idempotent(repo_copy: Path) -> None:
    """Re-running the local install must succeed (idempotent)."""
    first = _run_install(repo_copy, "--local", "--no-skill", "--lang", "en")
    assert first.returncode == 0, first.stderr
    second = _run_install(repo_copy, "--local", "--no-skill", "--lang", "en")
    assert second.returncode == 0, second.stderr
    assert (repo_copy / ".venv" / "bin" / "yanshi").is_file()


def test_local_install_registers_skill(repo_copy: Path, tmp_path: Path) -> None:
    """`install.sh` registers SKILL.md so a parent agent can discover YanShi."""
    skills = tmp_path / "skills"
    proc = _run_install(
        repo_copy, "--local", "--skill-dir", str(skills), "--lang", "en"
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    registered = skills / "yanshi" / "SKILL.md"
    assert registered.is_file(), "installer did not register SKILL.md into the skills dir"
    # The registered contract documents the /devola-flow dispatch path.
    assert "/devola-flow" in registered.read_text(encoding="utf-8")


def test_local_install_no_skill_skips_registration(repo_copy: Path, tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    proc = _run_install(
        repo_copy, "--local", "--no-skill", "--skill-dir", str(skills), "--lang", "en"
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert not (skills / "yanshi").exists()


def test_global_install_registers_skill_from_packaged_data(
    repo_copy: Path, tmp_path: Path
) -> None:
    """A global (non-editable) install registers from bundled wheel data."""
    tool_dir = tmp_path / "uv_tools"
    bin_dir = tmp_path / "uv_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    skills = tmp_path / "skills"
    proc = _run_install(
        repo_copy,
        "--global",
        "--skill-dir",
        str(skills),
        "--lang",
        "en",
        env={"UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(bin_dir)},
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    registered = skills / "yanshi" / "SKILL.md"
    assert registered.is_file(), "global install did not register packaged SKILL.md"


def test_local_install_with_mcp_verifies_imports(repo_copy: Path) -> None:
    """`--with-mcp` verifies the dispatch import and prints wiring guidance."""
    proc = _run_install(repo_copy, "--local", "--with-mcp", "--no-skill", "--lang", "en")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "yanshi.dispatch" in proc.stdout
    assert "skill" in proc.stdout and "mcp_server" in proc.stdout


def test_global_install_onto_isolated_path(repo_copy: Path, tmp_path: Path) -> None:
    """`install.sh --global` installs a usable `yanshi` executable (isolated)."""
    tool_dir = tmp_path / "uv_tools"
    bin_dir = tmp_path / "uv_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    proc = _run_install(
        repo_copy,
        "--global",
        "--no-skill",
        "--lang",
        "en",
        env={"UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(bin_dir)},
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    yanshi_bin = bin_dir / "yanshi"
    assert yanshi_bin.exists(), "global install did not place a yanshi executable on the bin dir"

    helptext = subprocess.run(
        [str(yanshi_bin), "--help"], capture_output=True, text=True, timeout=60
    )
    assert helptext.returncode == 0
    assert "dispatch" in helptext.stdout


def test_unknown_flag_still_fails_fast(repo_copy: Path) -> None:
    """A real invocation with a bad flag exits non-zero before doing work."""
    proc = _run_install(repo_copy, "--definitely-not-a-flag")
    assert proc.returncode != 0
    assert not (repo_copy / ".venv").exists()
