from __future__ import annotations

from pathlib import Path

import pytest

from yanshi.errors import PathBoundaryError
from yanshi.paths import normalize_existing_dir, validate_workdir_and_add_dirs


def test_normalize_existing_dir_accepts_directory(tmp_path: Path) -> None:
    assert normalize_existing_dir(tmp_path) == tmp_path.resolve()


def test_normalize_existing_dir_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(PathBoundaryError):
        normalize_existing_dir(tmp_path / "missing")


def test_normalize_existing_dir_rejects_file(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(PathBoundaryError):
        normalize_existing_dir(file_path)


def test_normalize_existing_dir_rejects_root_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PathBoundaryError):
        normalize_existing_dir(outside, root=root)


def test_validate_workdir_and_add_dirs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    workdir = root / "work"
    extra = root / "extra"
    workdir.mkdir(parents=True)
    extra.mkdir()
    resolved_workdir, resolved_extra = validate_workdir_and_add_dirs(
        workdir,
        [str(extra)],
        root=root,
    )
    assert resolved_workdir == workdir.resolve()
    assert resolved_extra == (extra.resolve(),)
