from __future__ import annotations

import pytest

from yanshi.adapters.claude import ClaudeAdapter
from yanshi.errors import AdapterError
from yanshi.registry import AdapterRegistry, build_registry, default_registry


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


def test_build_registry_none_registers_all_known_adapters() -> None:
    registry = build_registry(None)
    assert registry.list_names() == ["claude", "codex", "cursor", "gemini"]


def test_build_registry_registers_only_enabled_subset() -> None:
    registry = build_registry(["claude", "gemini"])
    assert registry.list_names() == ["claude", "gemini"]
    # An enabled adapter still resolves its capabilities from TOML metadata.
    caps = registry.capabilities("claude")
    assert caps.stream_json is True
    # A known-but-disabled adapter is simply absent from the registry.
    with pytest.raises(AdapterError):
        registry.get("codex")


def test_build_registry_empty_enabled_set_raises() -> None:
    with pytest.raises(AdapterError) as exc_info:
        build_registry([])
    assert exc_info.value.detail["known"] == ["claude", "codex", "cursor", "gemini"]


def test_build_registry_unknown_adapter_fails_fast() -> None:
    with pytest.raises(AdapterError) as exc_info:
        build_registry(["claude", "bogus"])
    assert "bogus" in str(exc_info.value)
    assert exc_info.value.detail["unknown"] == "bogus"
    assert exc_info.value.detail["known"] == ["claude", "codex", "cursor", "gemini"]


def test_build_registry_dedupes_repeated_adapter() -> None:
    registry = build_registry(["claude", "claude"])
    assert registry.list_names() == ["claude"]


def test_default_registry_delegates_to_build_registry() -> None:
    assert default_registry().list_names() == ["claude", "codex", "cursor", "gemini"]
