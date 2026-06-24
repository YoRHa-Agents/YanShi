"""Path normalization and boundary validation."""

from __future__ import annotations

from pathlib import Path

from yanshi.errors import ErrorCategory, PathBoundaryError


def normalize_existing_dir(path: str | Path, *, root: str | Path | None = None) -> Path:
    """Resolve a directory and optionally require it to stay inside `root`.

    Args:
        path: Candidate directory.
        root: Optional trust boundary. If provided, the resolved `path` must
            be equal to or below the resolved `root`.

    Raises:
        PathBoundaryError: if the directory does not exist, is not a directory,
            or escapes the root boundary.
    """

    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PathBoundaryError(
            f"path does not exist: {candidate}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"path": str(candidate)},
        ) from exc
    if not resolved.is_dir():
        raise PathBoundaryError(
            f"path is not a directory: {resolved}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"path": str(resolved)},
        )

    if root is None:
        return resolved

    try:
        root_resolved = Path(root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise PathBoundaryError(
            f"root boundary does not exist: {root}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"root": str(root)},
        ) from exc
    if not root_resolved.is_dir():
        raise PathBoundaryError(
            f"root boundary is not a directory: {root_resolved}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"root": str(root_resolved)},
        )

    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PathBoundaryError(
            f"path escapes trusted root: {resolved} not under {root_resolved}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"path": str(resolved), "root": str(root_resolved)},
        ) from exc
    return resolved


def validate_workdir_and_add_dirs(
    workdir: str | Path,
    add_dirs: list[str] | tuple[str, ...] = (),
    *,
    root: str | Path | None = None,
) -> tuple[Path, tuple[Path, ...]]:
    """Validate the primary workdir and extra writable directories."""

    resolved_workdir = normalize_existing_dir(workdir, root=root)
    resolved_add_dirs = tuple(normalize_existing_dir(path, root=root) for path in add_dirs)
    return resolved_workdir, resolved_add_dirs
