"""Raw stream log sink with bounded retention."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

Redactor = Callable[[str], str]


class RawLogSink:
    """Append raw stream lines to disk while keeping a bounded byte window."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        redactor: Redactor | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.redactor = redactor or (lambda value: value)
        self.truncated_count = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, raw_line: str) -> None:
        """Append one redacted raw line and enforce the ring byte window."""

        safe_line = self.redactor(raw_line).rstrip("\n") + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(safe_line)
        self._enforce_limit()

    def read_slice(self, *, offset: int = 0, limit: int | None = None) -> str:
        """Read a byte slice from the retained log."""

        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            return handle.read() if limit is None else handle.read(limit)

    def _enforce_limit(self) -> None:
        size = self.path.stat().st_size
        if size <= self.max_bytes:
            return
        with self.path.open("rb") as handle:
            handle.seek(max(0, size - self.max_bytes))
            retained = handle.read()
        self.path.write_bytes(retained)
        self.truncated_count += 1
