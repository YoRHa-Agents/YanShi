"""Adapter registry with TOML-backed capability metadata."""

from __future__ import annotations

import tomllib
from importlib import resources

from yanshi.adapters.base import Adapter
from yanshi.adapters.claude import ClaudeAdapter
from yanshi.adapters.codex import CodexAdapter
from yanshi.adapters.cursor import CursorAdapter
from yanshi.adapters.gemini import GeminiAdapter
from yanshi.contracts import AllowMode, Capabilities, CapabilityMode
from yanshi.errors import AdapterError, ErrorCategory


class AdapterRegistry:
    """Registry of available YanShi adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}
        self._capability_data: dict[str, dict[str, object]] = {}

    def register(self, adapter: Adapter) -> None:
        """Register an adapter instance."""

        if adapter.name in self._adapters:
            raise AdapterError(
                f"adapter already registered: {adapter.name}",
                category=ErrorCategory.INVALID_REQUEST,
            )
        self._adapters[adapter.name] = adapter
        self._capability_data[adapter.name] = _load_adapter_toml(adapter.name)

    def get(self, name: str) -> Adapter:
        """Return an adapter by name."""

        try:
            return self._adapters[name]
        except KeyError as exc:
            raise AdapterError(
                f"unknown adapter: {name}",
                category=ErrorCategory.INVALID_REQUEST,
                detail={"adapter": name, "available": sorted(self._adapters)},
            ) from exc

    def capabilities(self, name: str) -> Capabilities:
        """Return capabilities for an adapter, preferring TOML metadata."""

        adapter = self.get(name)
        raw = self._capability_data.get(name, {}).get("capabilities")
        if not isinstance(raw, dict):
            return adapter.capabilities
        return _capabilities_from_raw(raw)

    def list_names(self) -> list[str]:
        """List registered adapter names."""

        return sorted(self._adapters)


def default_registry() -> AdapterRegistry:
    """Build the default registry."""

    registry = AdapterRegistry()
    registry.register(ClaudeAdapter())
    registry.register(CodexAdapter())
    registry.register(CursorAdapter())
    registry.register(GeminiAdapter())
    return registry


def _load_adapter_toml(name: str) -> dict[str, object]:
    try:
        data_root = resources.files("yanshi.adapters.data")
        text = data_root.joinpath(f"{name}.toml").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return {}
    parsed = tomllib.loads(text)
    return dict(parsed)


def _capabilities_from_raw(raw: dict[str, object]) -> Capabilities:
    permission_modes_raw = raw.get("permission_modes")
    permission_modes = (
        [AllowMode(str(value)) for value in permission_modes_raw]
        if isinstance(permission_modes_raw, list)
        else [AllowMode.READ_ONLY]
    )
    return Capabilities(
        effort=CapabilityMode(str(raw.get("effort", CapabilityMode.NONE.value))),
        context_window_flag=bool(raw.get("context_window_flag", False)),
        session_resume=bool(raw.get("session_resume", False)),
        preassign_session_id=bool(raw.get("preassign_session_id", False)),
        output_schema=bool(raw.get("output_schema", False)),
        stream_json=bool(raw.get("stream_json", False)),
        permission_modes=permission_modes,
    )
