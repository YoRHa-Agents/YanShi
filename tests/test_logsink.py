from __future__ import annotations

from pathlib import Path

import pytest

from yanshi.logsink import RawLogSink


def test_logsink_redacts_and_reads_slice(tmp_path: Path) -> None:
    sink = RawLogSink(
        tmp_path / "stream.ndjson",
        redactor=lambda value: value.replace("secret", "***"),
    )
    sink.append('{"token":"secret"}')
    assert "secret" not in sink.read_slice()
    assert "***" in sink.read_slice(offset=0, limit=100)


def test_logsink_enforces_byte_limit(tmp_path: Path) -> None:
    sink = RawLogSink(tmp_path / "stream.ndjson", max_bytes=10)
    sink.append("0123456789")
    sink.append("abcdef")
    assert sink.path.stat().st_size <= 10
    assert sink.truncated_count >= 1


def test_logsink_rejects_invalid_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RawLogSink(tmp_path / "stream.ndjson", max_bytes=0)
