from __future__ import annotations

import sys

import pytest

from yanshi.otel import emit_genai_event


def test_emit_genai_event_fails_open_without_otel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    assert emit_genai_event("test", {"system": "yanshi"}) is False
