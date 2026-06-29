"""Register the YanShi skill (``SKILL.md`` + MCP shim) into agent skill homes.

The skill layer (design spec §1.2 / §7) is a first-class delivery surface:
a parent agent (Cursor / Claude / ...) discovers YanShi by reading a *registered*
``SKILL.md`` under its skills home. Installing the ``yanshi`` CLI is necessary
but **not sufficient** — without registration the agent never sees the skill,
which is exactly the "installed but not registered" gap this module closes.

Resolution is two-tier so registration works for every install scope:

1. **packaged wheel data** (``yanshi/_skill/SKILL.md``) — present in a built
   wheel via ``[tool.hatch.build.targets.wheel.force-include]``; used by global
   installs that have no checkout on disk.
2. **repo checkout** (``<repo>/skill/SKILL.md``) — used by editable/local
   installs where the package resolves back to ``src/yanshi``.

No Silent Failures: a missing source or a copy error raises
:class:`SkillRegistrationError`. A "no agent homes found" condition is *not* an
error — it is reported via an empty :attr:`SkillRegistration.registered` so the
caller (CLI / installer) decides whether that is fatal or a best-effort warning.
"""

from __future__ import annotations

import importlib.resources
import shutil
from pathlib import Path

from pydantic import BaseModel

from yanshi.errors import ErrorCategory, YanShiError

#: Directory name created under each agent skills home (``<home>/yanshi/``).
SKILL_NAME = "yanshi"
#: The progressive-disclosure contract file an agent reads.
SKILL_ENTRY = "SKILL.md"
#: Companion files copied alongside ``SKILL.md`` when they exist next to it.
SKILL_COMPANIONS: tuple[str, ...] = ("mcp_server.py",)

#: Canonical agent skill homes as ``(agent_config_dir, skills_subdir)`` pairs.
#: A home is auto-selected only when its ``agent_config_dir`` exists under $HOME,
#: so we register where an agent is actually installed instead of littering $HOME.
DEFAULT_AGENT_HOMES: tuple[tuple[str, str], ...] = (
    (".cursor", ".cursor/skills"),
    (".claude", ".claude/skills"),
    (".agents", ".agents/skills"),
)


class SkillRegistrationError(YanShiError):
    """Raised when the skill source cannot be located or a copy fails."""


class SkillRegistration(BaseModel):
    """JSON-ready report of a single ``register_skill`` run."""

    skill: str
    source: str
    files: list[str]
    targets: list[str]
    registered: list[str]
    dry_run: bool


def _packaged_skill_dir() -> Path | None:
    """Return the bundled ``yanshi/_skill`` dir if it ships SKILL.md, else None."""

    try:
        base = importlib.resources.files("yanshi")
    except (ModuleNotFoundError, ImportError, TypeError):
        return None
    candidate = Path(str(base)) / "_skill"
    return candidate if (candidate / SKILL_ENTRY).is_file() else None


def _repo_skill_dir() -> Path | None:
    """Return the repo-checkout ``skill`` dir for editable installs, else None."""

    # src/yanshi/skill_install.py -> parents[2] is the repo root for an editable
    # install (where ``import yanshi`` resolves back to ``src/yanshi``).
    candidate = Path(__file__).resolve().parents[2] / "skill"
    return candidate if (candidate / SKILL_ENTRY).is_file() else None


def find_skill_source_dir() -> Path:
    """Locate the directory holding the canonical ``SKILL.md``.

    Order: packaged wheel data first (so global installs work without a
    checkout), then a repo checkout (editable/local installs).

    Raises:
        SkillRegistrationError: if no source can be located.
    """

    for getter in (_packaged_skill_dir, _repo_skill_dir):
        found = getter()
        if found is not None:
            return found
    raise SkillRegistrationError(
        "could not locate the YanShi SKILL.md source (looked in packaged data "
        "'yanshi/_skill' and the repo 'skill/' directory)",
        category=ErrorCategory.INVALID_REQUEST,
    )


def _skill_source_files(source_dir: Path) -> list[Path]:
    """SKILL.md plus any companion files that exist next to it."""

    files = [source_dir / SKILL_ENTRY]
    files.extend(source_dir / name for name in SKILL_COMPANIONS if (source_dir / name).is_file())
    return files


def default_agent_skill_homes(home: Path | None = None) -> list[Path]:
    """Every canonical agent skills home (whether or not it exists yet)."""

    base = home if home is not None else Path.home()
    return [base / skills for _, skills in DEFAULT_AGENT_HOMES]


def detect_agent_skill_homes(home: Path | None = None) -> list[Path]:
    """Agent skills homes whose parent agent config dir already exists.

    ``~/.cursor/skills`` is returned only when ``~/.cursor`` exists, etc., so we
    register where an agent is actually present rather than creating stray dirs.
    """

    base = home if home is not None else Path.home()
    homes: list[Path] = []
    for agent_dir, skills in DEFAULT_AGENT_HOMES:
        if (base / agent_dir).is_dir():
            homes.append(base / skills)
    return homes


def resolve_targets(
    skills_dir: str | Path | None = None,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Resolve the target skills homes for registration.

    An explicit ``skills_dir`` always wins (and is created on write); otherwise
    auto-detect the installed agent homes.
    """

    if skills_dir is not None:
        return [Path(skills_dir).expanduser()]
    return detect_agent_skill_homes(home=home)


def register_skill(
    skills_dir: str | Path | None = None,
    *,
    home: Path | None = None,
    dry_run: bool = False,
) -> SkillRegistration:
    """Register the YanShi skill into the resolved agent skills homes.

    Copies ``SKILL.md`` (and any companion files) into ``<home>/yanshi/``. The
    operation is idempotent: an existing registration is overwritten in place.

    Args:
        skills_dir: Explicit skills home to register into. When ``None``,
            installed agent homes are auto-detected.
        home: Base directory to resolve ``~`` against (testing seam; defaults
            to the real ``$HOME``).
        dry_run: When true, plan the registration without touching disk.

    Returns:
        A :class:`SkillRegistration` report. An empty ``registered`` list means
        no agent skills home was found (not an error on its own).

    Raises:
        SkillRegistrationError: if the source is missing or a copy fails.
    """

    source_dir = find_skill_source_dir()
    source_files = _skill_source_files(source_dir)
    targets = resolve_targets(skills_dir, home=home)

    registered: list[str] = []
    for target_home in targets:
        skill_dir = target_home / SKILL_NAME
        primary = skill_dir / SKILL_ENTRY
        if not dry_run:
            try:
                skill_dir.mkdir(parents=True, exist_ok=True)
                for src in source_files:
                    shutil.copyfile(src, skill_dir / src.name)
            except OSError as exc:
                raise SkillRegistrationError(
                    f"failed to register skill into {skill_dir}: {exc}",
                    category=ErrorCategory.INVALID_REQUEST,
                    detail={"target": str(skill_dir)},
                ) from exc
        registered.append(str(primary))

    return SkillRegistration(
        skill=SKILL_NAME,
        source=str(source_dir),
        files=[src.name for src in source_files],
        targets=[str(target) for target in targets],
        registered=registered,
        dry_run=dry_run,
    )
