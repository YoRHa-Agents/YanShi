"""Repo-level configuration for YanShi mechanisms and control threads.

This is the keystone config module consumed by the registry, dispatcher, and
CLI. It defines the on-disk `.yanshi.toml` schema (pydantic v2 models with
``extra="forbid"``), layered discovery/merge of global + local config, and the
deterministic resolution of per-call dispatch overrides into RunSpec kwargs.

Parsing uses the stdlib :mod:`tomllib`. Per the "No Silent Failures" rule, any
malformed or unreadable config raises :class:`YanShiError` with
``ErrorCategory.INVALID_REQUEST`` and the offending path in ``detail`` -- it is
never silently ignored.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yanshi.contracts import AllowMode, WarningRecord
from yanshi.errors import ErrorCategory, YanShiError

__all__ = [
    "DEFAULT_CONFIG_FILENAME",
    "AdaptersConfig",
    "SummarizerSettings",
    "DefaultsConfig",
    "ProfileConfig",
    "LimitsConfig",
    "YanshiConfig",
    "LoadedConfig",
    "ResolvedDispatch",
    "global_config_path",
    "discover_local_config",
    "parse_config_file",
    "load_config",
    "render_default_config_toml",
    "enabled_adapter_names",
    "resolve_dispatch",
]

DEFAULT_CONFIG_FILENAME = ".yanshi.toml"

# Strength ranking for AllowMode used when clamping against limits.
_ALLOW_RANK: dict[AllowMode, int] = {AllowMode.READ_ONLY: 0, AllowMode.YOLO: 1}

# Top-level config sections tracked for provenance/merge.
_SECTIONS: tuple[str, ...] = ("adapters", "summarizer", "defaults", "limits", "profiles")


class AdaptersConfig(BaseModel):
    """Which adapter mechanisms YanShi may dispatch to (``None`` means all)."""

    model_config = ConfigDict(extra="forbid")

    enabled: list[str] | None = None


class SummarizerSettings(BaseModel):
    """Optional summary control-thread settings (off by default)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    cli: str = "claude"
    model: str | None = None
    debounce_s: float = 5.0
    min_new_events: int = 2
    max_tokens: int = 150
    watcher_token_ceiling: int = 1000
    timeout_s: int = 60


class DefaultsConfig(BaseModel):
    """Default mechanism choices applied to every dispatch (lowest precedence).

    ``reasoning_effort`` is exposed under the TOML key ``effort`` via an alias;
    ``populate_by_name=True`` keeps the python attribute name usable too.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    cli: str | None = None
    model: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = Field(
        default=None, alias="effort"
    )
    allow: AllowMode | None = None
    timeout_s: int | None = None
    stall_timeout_s: int | None = None
    cost_ceiling_usd: float | None = None


class ProfileConfig(DefaultsConfig):
    """A named override bundle; identical shape to :class:`DefaultsConfig`."""


class LimitsConfig(BaseModel):
    """Hard caps enforced on every dispatch regardless of profile/overrides."""

    model_config = ConfigDict(extra="forbid")

    max_allow: AllowMode | None = None
    max_cost_usd: float | None = None
    max_timeout_s: int | None = None


class YanshiConfig(BaseModel):
    """The full parsed `.yanshi.toml` document."""

    model_config = ConfigDict(extra="forbid")

    adapters: AdaptersConfig = AdaptersConfig()
    summarizer: SummarizerSettings = SummarizerSettings()
    defaults: DefaultsConfig = DefaultsConfig()
    limits: LimitsConfig = LimitsConfig()
    profiles: dict[str, ProfileConfig] = {}


@dataclass(frozen=True)
class LoadedConfig:
    """A resolved config plus where each section came from."""

    config: YanshiConfig
    sources: list[Path]  # in precedence order, low -> high
    provenance: dict[str, str]  # section name -> source path string ("builtin" if default)


@dataclass(frozen=True)
class ResolvedDispatch:
    """Dispatch kwargs ready to splat into RunSpec, plus any warnings."""

    kwargs: dict[str, Any]
    warnings: list[WarningRecord]


def global_config_path() -> Path:
    """Return ``$YANSHI_HOME/config.toml`` (``YANSHI_HOME`` defaults to ``~/.yanshi``)."""

    home = Path(os.environ.get("YANSHI_HOME", "~/.yanshi")).expanduser()
    return home / "config.toml"


def discover_local_config(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default cwd) returning the nearest `.yanshi.toml`."""

    base = start if start is not None else Path.cwd()
    for directory in (base, *base.parents):
        candidate = directory / DEFAULT_CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def parse_config_file(path: Path) -> YanshiConfig:
    """Read, parse, and validate a single `.yanshi.toml` file.

    Raises :class:`YanShiError` (``INVALID_REQUEST``) on unreadable files,
    malformed TOML, or schema-validation failures -- never swallowing them.
    """

    return _validate_config(_read_toml(path), path)


def load_config(
    *,
    start: Path | None = None,
    explicit_path: Path | None = None,
    include_global: bool = True,
) -> LoadedConfig:
    """Build a layered config: builtin defaults < global < local.

    The local layer is ``explicit_path`` when given, otherwise the result of
    :func:`discover_local_config`. Sections are deep-merged (later wins per key;
    ``profiles`` merge by dict key). ``sources`` and ``provenance`` record which
    files contributed and which source last set each top-level section.
    """

    provenance: dict[str, str] = {section: "builtin" for section in _SECTIONS}
    sources: list[Path] = []
    merged: dict[str, Any] = {}

    if include_global:
        global_path = global_config_path()
        if global_path.is_file():
            merged = _apply_layer(global_path, merged, provenance)
            sources.append(global_path)

    local_path = explicit_path if explicit_path is not None else discover_local_config(start)
    if local_path is not None:
        merged = _apply_layer(local_path, merged, provenance)
        sources.append(local_path)

    config = _validate_config(merged, None)
    return LoadedConfig(config=config, sources=sources, provenance=provenance)


def enabled_adapter_names(config: YanshiConfig) -> list[str] | None:
    """Return the enabled mechanism allow-list (``None`` means all)."""

    return config.adapters.enabled


def resolve_dispatch(
    overrides: Mapping[str, Any],
    *,
    config: YanshiConfig,
    profile: str | None = None,
) -> ResolvedDispatch:
    """Resolve per-call dispatch kwargs from defaults, an optional profile, and overrides.

    Precedence (low -> high): ``config.defaults`` < selected ``profile`` <
    ``overrides``. The result is then clamped by ``config.limits`` (each clamp
    emits a ``capability_clamped`` warning) and normalized so the dict can be
    splatted directly into a RunSpec.
    """

    warnings: list[WarningRecord] = []

    kwargs: dict[str, Any] = config.defaults.model_dump(exclude_none=True, by_alias=False)

    if profile is not None:
        selected = config.profiles.get(profile)
        if selected is None:
            warnings.append(
                WarningRecord(
                    code="profile_unknown",
                    message=f"unknown profile '{profile}'; ignoring",
                    detail={"profile": profile, "available": sorted(config.profiles)},
                )
            )
        else:
            kwargs.update(selected.model_dump(exclude_none=True, by_alias=False))

    for key, value in overrides.items():
        if value is not None:
            kwargs[key] = value

    _clamp_allow(kwargs, config.limits, warnings)
    _clamp_cost(kwargs, config.limits, warnings)
    _clamp_timeout(kwargs, config.limits, warnings)

    return ResolvedDispatch(kwargs=_normalize_kwargs(kwargs), warnings=warnings)


def render_default_config_toml() -> str:
    """Return a fully-commented `.yanshi.toml` template (used by ``yanshi init``).

    The template parses cleanly through :func:`parse_config_file`.
    """

    return _DEFAULT_CONFIG_TEMPLATE


def _apply_layer(
    path: Path,
    merged: dict[str, Any],
    provenance: dict[str, str],
) -> dict[str, Any]:
    """Validate ``path`` independently, then deep-merge it onto ``merged``."""

    raw = _read_toml(path)
    # Validate each file on its own so errors carry the precise source path.
    _validate_config(raw, path)
    result = _deep_merge(merged, raw)
    for section in raw:
        provenance[section] = str(path)
    return result


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise YanShiError(
            f"could not read config file: {path}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"path": str(path), "error": str(exc)},
        ) from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise YanShiError(
            f"malformed TOML in config file: {path}",
            category=ErrorCategory.INVALID_REQUEST,
            detail={"path": str(path), "error": str(exc)},
        ) from exc


def _validate_config(data: Mapping[str, Any], path: Path | None) -> YanshiConfig:
    try:
        return YanshiConfig.model_validate(data)
    except ValidationError as exc:
        detail: dict[str, object] = {"error": str(exc)}
        message = "invalid YanShi configuration"
        if path is not None:
            detail["path"] = str(path)
            message = f"{message}: {path}"
        raise YanShiError(
            message,
            category=ErrorCategory.INVALID_REQUEST,
            detail=detail,
        ) from exc


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto ``base`` (overlay wins per leaf key)."""

    result = dict(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def _allow_rank(value: Any) -> int:
    return _ALLOW_RANK[AllowMode(value)]


def _clamp_allow(
    kwargs: dict[str, Any],
    limits: LimitsConfig,
    warnings: list[WarningRecord],
) -> None:
    max_allow = limits.max_allow
    current = kwargs.get("allow")
    if max_allow is None or current is None:
        return
    if _allow_rank(current) > _ALLOW_RANK[max_allow]:
        warnings.append(
            WarningRecord(
                code="capability_clamped",
                message=f"allow downgraded to limit '{max_allow.value}'",
                detail={
                    "field": "allow",
                    "limit": max_allow.value,
                    "requested": AllowMode(current).value,
                },
            )
        )
        kwargs["allow"] = max_allow


def _clamp_cost(
    kwargs: dict[str, Any],
    limits: LimitsConfig,
    warnings: list[WarningRecord],
) -> None:
    max_cost = limits.max_cost_usd
    if max_cost is None:
        return
    existing = kwargs.get("cost_ceiling_usd")
    if existing is None:
        kwargs["cost_ceiling_usd"] = max_cost
        return
    if existing > max_cost:
        warnings.append(
            WarningRecord(
                code="capability_clamped",
                message=f"cost_ceiling_usd clamped to limit {max_cost}",
                detail={"field": "cost_ceiling_usd", "limit": max_cost, "requested": existing},
            )
        )
        kwargs["cost_ceiling_usd"] = max_cost


def _clamp_timeout(
    kwargs: dict[str, Any],
    limits: LimitsConfig,
    warnings: list[WarningRecord],
) -> None:
    max_timeout = limits.max_timeout_s
    if max_timeout is None:
        return
    existing = kwargs.get("timeout_s")
    if existing is None:
        kwargs["timeout_s"] = max_timeout
        return
    if existing > max_timeout:
        warnings.append(
            WarningRecord(
                code="capability_clamped",
                message=f"timeout_s clamped to limit {max_timeout}",
                detail={"field": "timeout_s", "limit": max_timeout, "requested": existing},
            )
        )
        kwargs["timeout_s"] = max_timeout


def _normalize_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values and coerce ``allow`` to an :class:`AllowMode` instance."""

    normalized: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        normalized[key] = value
    if "allow" in normalized:
        normalized["allow"] = AllowMode(normalized["allow"])
    return normalized


_DEFAULT_CONFIG_TEMPLATE = """\
# YanShi (偃师) repository configuration (.yanshi.toml)
# The parent agent remains the artisan. This file chooses which CLI mechanisms
# may be used here, then sets shared defaults, hard limits, and optional summary
# control threads. Every value is optional; omitted keys fall back to builtins.

[adapters]
# Choose the enabled mechanisms for this repo. Remove this key (or the whole
# section) to leave every installed adapter mechanism available. Names are
# validated against the real adapter registry at dispatch time.
enabled = ["claude", "codex", "cursor", "gemini"]

[summarizer]
# Optional summary control threads are OFF by default. When enabled they are
# advisory only: deterministic status remains the source of truth.
enabled = false
# Mechanism used to produce summaries when enabled.
cli = "claude"
# Model for that summarizer mechanism; omit to use the CLI's own default.
model = "claude-3-5-haiku-latest"
# Minimum seconds between summary refreshes.
debounce_s = 5.0
# Minimum number of new significant events before re-summarizing.
min_new_events = 2
# Hard cap on summary length, in tokens.
max_tokens = 150
# Total watcher token budget before falling back to deterministic text.
watcher_token_ceiling = 1000
# Per-summary mechanism timeout, in seconds.
timeout_s = 60

[defaults]
# Defaults are applied to every dispatch before any named profile or CLI flag.
# Reasoning effort: low | medium | high | xhigh.
effort = "medium"
# Permission model: read-only | yolo. Keep read-only unless a task truly needs
# write access and your limits allow it.
allow = "read-only"
# Overall timeout per dispatch, in seconds.
timeout_s = 1800
# Stall (no-progress) timeout per dispatch, in seconds.
stall_timeout_s = 300
# Optionally pin a default mechanism, model, or cost ceiling for every dispatch.
# cli = "claude"
# model = "claude-3-7-sonnet-latest"
# cost_ceiling_usd = 5.0

[limits]
# Hard caps are enforced after defaults, profiles, and per-call overrides.
# Uncomment to activate; requests above a cap are clamped and surfaced as
# warnings instead of being ignored.
# max_allow = "read-only"
# max_cost_usd = 10.0
# max_timeout_s = 3600

[profiles.cheap]
# A fast, low-cost profile: minimal effort and tight budgets.
effort = "low"
cost_ceiling_usd = 0.5
timeout_s = 600

[profiles.thorough]
# A high-effort profile for hard, long-running tasks.
effort = "high"
timeout_s = 3600
stall_timeout_s = 600
"""
