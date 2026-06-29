"""Tests for skill registration (``yanshi.skill_install``).

These cover the gap reported as "installed but not registered": resolving the
canonical ``SKILL.md`` source, detecting agent skills homes, and copying the
skill into them. No real ``$HOME`` is touched — every test points at a tmp dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yanshi import skill_install
from yanshi.skill_install import (
    SKILL_COMPANIONS,
    SKILL_ENTRY,
    SKILL_NAME,
    SkillRegistration,
    SkillRegistrationError,
    default_agent_skill_homes,
    detect_agent_skill_homes,
    find_skill_source_dir,
    register_skill,
    resolve_targets,
)


def test_find_skill_source_dir_resolves_canonical_skill_md() -> None:
    """The source resolver finds a directory that actually ships SKILL.md."""
    source = find_skill_source_dir()
    assert source.is_dir()
    assert (source / SKILL_ENTRY).is_file()


def test_find_skill_source_dir_raises_when_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Silent Failures: a missing source is a hard error, not an empty copy."""
    monkeypatch.setattr(skill_install, "_packaged_skill_dir", lambda: None)
    monkeypatch.setattr(skill_install, "_repo_skill_dir", lambda: None)
    with pytest.raises(SkillRegistrationError):
        find_skill_source_dir()


def test_register_into_explicit_dir_copies_skill_and_companions(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    report = register_skill(skills_dir=skills)

    assert isinstance(report, SkillRegistration)
    assert report.dry_run is False
    skill_dir = skills / SKILL_NAME
    registered = skill_dir / SKILL_ENTRY
    assert registered.is_file()
    assert report.registered == [str(registered)]

    # Registered content matches the canonical source byte-for-byte.
    source = find_skill_source_dir()
    assert registered.read_text(encoding="utf-8") == (source / SKILL_ENTRY).read_text(
        encoding="utf-8"
    )
    # Companions that exist next to SKILL.md are copied too.
    for companion in SKILL_COMPANIONS:
        if (source / companion).is_file():
            assert (skill_dir / companion).is_file()


def test_register_is_idempotent(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    first = register_skill(skills_dir=skills)
    second = register_skill(skills_dir=skills)
    assert first.registered == second.registered
    registered = skills / SKILL_NAME / SKILL_ENTRY
    assert registered.is_file()


def test_register_dry_run_writes_nothing(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    report = register_skill(skills_dir=skills, dry_run=True)
    assert report.dry_run is True
    # The would-be path is reported, but nothing is written to disk.
    assert report.registered == [str(skills / SKILL_NAME / SKILL_ENTRY)]
    assert not (skills / SKILL_NAME).exists()


def test_detect_agent_skill_homes_only_returns_existing(tmp_path: Path) -> None:
    """A home is selected only when its agent config dir exists, in canon order."""
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".agents").mkdir()
    # .claude intentionally absent.
    homes = detect_agent_skill_homes(home=tmp_path)
    assert homes == [tmp_path / ".cursor" / "skills", tmp_path / ".agents" / "skills"]


def test_detect_agent_skill_homes_empty_when_no_agents(tmp_path: Path) -> None:
    assert detect_agent_skill_homes(home=tmp_path) == []


def test_default_agent_skill_homes_lists_all(tmp_path: Path) -> None:
    homes = default_agent_skill_homes(home=tmp_path)
    assert homes == [
        tmp_path / ".cursor" / "skills",
        tmp_path / ".claude" / "skills",
        tmp_path / ".agents" / "skills",
    ]


def test_resolve_targets_explicit_dir_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom"
    assert resolve_targets(explicit, home=tmp_path) == [explicit]


def test_register_auto_detects_and_writes_each_home(tmp_path: Path) -> None:
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".claude").mkdir()
    report = register_skill(home=tmp_path)
    expected = [
        str(tmp_path / ".cursor" / "skills" / SKILL_NAME / SKILL_ENTRY),
        str(tmp_path / ".claude" / "skills" / SKILL_NAME / SKILL_ENTRY),
    ]
    assert report.registered == expected
    for path in expected:
        assert Path(path).is_file()


def test_register_no_agent_home_is_reported_not_raised(tmp_path: Path) -> None:
    """With no agent homes and no explicit dir, registration is a no-op report."""
    report = register_skill(home=tmp_path)
    assert report.registered == []
    assert report.targets == []
    assert report.dry_run is False
