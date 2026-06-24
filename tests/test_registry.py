from __future__ import annotations

import pytest

from yanshi.adapters.claude import ClaudeAdapter
from yanshi.errors import AdapterError
from yanshi.registry import AdapterRegistry, default_registry


def test_default_registry_contains_claude_capabilities() -> None:
    registry = default_registry()
    assert registry.list_names() == ["claude", "codex", "cursor", "gemini"]
    caps = registry.capabilities("claude")
    assert caps.stream_json is True
    assert caps.effort == "flag"


def test_registry_get_unknown_reports_available() -> None:
    registry = default_registry()
    with pytest.raises(AdapterError) as exc_info:
        registry.get("missing")
    assert "available" in exc_info.value.detail


def test_registry_rejects_duplicate_registration() -> None:
    registry = AdapterRegistry()
    registry.register(ClaudeAdapter())
    with pytest.raises(AdapterError):
        registry.register(ClaudeAdapter())
