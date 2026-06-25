"""Tests for the YanShi configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest

from yanshi.config import (
    DEFAULT_CONFIG_FILENAME,
    AdaptersConfig,
    DefaultsConfig,
    LimitsConfig,
    ProfileConfig,
    YanshiConfig,
    discover_local_config,
    enabled_adapter_names,
    global_config_path,
    load_config,
    parse_config_file,
    render_default_config_toml,
    resolve_dispatch,
)
from yanshi.contracts import AllowMode, RunSpec
from yanshi.errors import ErrorCategory, YanShiError


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discover_local_config_walks_up(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    config_file = root / DEFAULT_CONFIG_FILENAME
    _write(config_file, "[adapters]\n")

    assert discover_local_config(nested) == config_file


def test_discover_local_config_returns_none_when_absent(tmp_path: Path) -> None:
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)

    assert discover_local_config(nested) is None


# ---------------------------------------------------------------------------
# layered load / precedence
# ---------------------------------------------------------------------------


def test_load_config_precedence_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_home = tmp_path / "home"
    global_home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(global_home))

    global_path = global_config_path()
    assert global_path == global_home / "config.toml"
    _write(
        global_path,
        '[defaults]\ncli = "claude"\ntimeout_s = 1800\n[adapters]\nenabled = ["claude"]\n',
    )

    local_dir = tmp_path / "repo"
    local_dir.mkdir()
    local_path = local_dir / DEFAULT_CONFIG_FILENAME
    _write(local_path, '[defaults]\ncli = "codex"\n[limits]\nmax_cost_usd = 5.0\n')

    loaded = load_config(start=local_dir)
    config = loaded.config

    assert config.defaults.cli == "codex"  # local overrides global per-key
    assert config.defaults.timeout_s == 1800  # preserved from global via deep merge
    assert config.adapters.enabled == ["claude"]  # only set globally
    assert config.limits.max_cost_usd == 5.0  # only set locally

    assert loaded.sources == [global_path, local_path]
    assert loaded.provenance["defaults"] == str(local_path)
    assert loaded.provenance["adapters"] == str(global_path)
    assert loaded.provenance["limits"] == str(local_path)
    assert loaded.provenance["summarizer"] == "builtin"
    assert loaded.provenance["profiles"] == "builtin"


def test_load_config_explicit_path_can_skip_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_home = tmp_path / "home"
    global_home.mkdir()
    monkeypatch.setenv("YANSHI_HOME", str(global_home))
    _write(global_config_path(), '[defaults]\ncli = "claude"\n')

    explicit = tmp_path / "custom.toml"
    _write(explicit, '[defaults]\ncli = "gemini"\n')

    loaded = load_config(explicit_path=explicit, include_global=False)

    assert loaded.config.defaults.cli == "gemini"
    assert loaded.sources == [explicit]
    assert loaded.provenance["defaults"] == str(explicit)


# ---------------------------------------------------------------------------
# parsing / validation
# ---------------------------------------------------------------------------


def test_parse_config_file_malformed_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    _write(bad, 'broken = "unterminated\n')

    with pytest.raises(YanShiError) as exc_info:
        parse_config_file(bad)

    assert exc_info.value.category == ErrorCategory.INVALID_REQUEST
    assert exc_info.value.detail["path"] == str(bad)


def test_parse_config_file_unknown_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "unknown.toml"
    _write(bad, "[summarizer]\nbogus = 1\n")

    with pytest.raises(YanShiError) as exc_info:
        parse_config_file(bad)

    assert exc_info.value.category == ErrorCategory.INVALID_REQUEST
    assert exc_info.value.detail["path"] == str(bad)


def test_parse_config_file_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.toml"

    with pytest.raises(YanShiError) as exc_info:
        parse_config_file(missing)

    assert exc_info.value.category == ErrorCategory.INVALID_REQUEST
    assert exc_info.value.detail["path"] == str(missing)


def test_parse_config_file_valid(tmp_path: Path) -> None:
    good = tmp_path / "ok.toml"
    _write(good, '[defaults]\neffort = "high"\nallow = "yolo"\n')

    config = parse_config_file(good)

    assert config.defaults.reasoning_effort == "high"
    assert config.defaults.allow == AllowMode.YOLO
    # populate_by_name also accepts the python attribute name as an input key
    # (not just the TOML "effort" alias). model_validate takes a dict, so this
    # exercises that path without mypy's alias-only constructor signature.
    assert DefaultsConfig.model_validate({"reasoning_effort": "high"}).reasoning_effort == "high"
    assert ProfileConfig.model_validate({"reasoning_effort": "low"}).reasoning_effort == "low"


# ---------------------------------------------------------------------------
# default template
# ---------------------------------------------------------------------------


def test_render_default_config_round_trips(tmp_path: Path) -> None:
    template = render_default_config_toml()
    assert "[adapters]" in template
    assert "[profiles.cheap]" in template
    assert "parent agent remains the artisan" in template
    assert "enabled mechanisms" in template
    assert "summary control threads" in template
    assert "clamped and surfaced as" in template

    path = tmp_path / DEFAULT_CONFIG_FILENAME
    _write(path, template)
    config = parse_config_file(path)

    assert config.adapters.enabled == ["claude", "codex", "cursor", "gemini"]
    assert config.summarizer.enabled is False
    assert config.summarizer.cli == "claude"
    assert config.defaults.reasoning_effort == "medium"
    assert config.defaults.allow == AllowMode.READ_ONLY
    assert config.defaults.timeout_s == 1800
    assert config.defaults.stall_timeout_s == 300
    assert "cheap" in config.profiles
    assert config.profiles["thorough"].reasoning_effort == "high"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_enabled_adapter_names() -> None:
    assert enabled_adapter_names(YanshiConfig()) is None
    config = YanshiConfig(adapters=AdaptersConfig(enabled=["claude", "codex"]))
    assert enabled_adapter_names(config) == ["claude", "codex"]


# ---------------------------------------------------------------------------
# dispatch resolution
# ---------------------------------------------------------------------------


def test_resolve_dispatch_defaults_fill_missing() -> None:
    config = YanshiConfig(defaults=DefaultsConfig(effort="medium", timeout_s=1800))
    resolved = resolve_dispatch({}, config=config)

    assert resolved.kwargs["reasoning_effort"] == "medium"
    assert resolved.kwargs["timeout_s"] == 1800
    assert resolved.warnings == []


def test_resolve_dispatch_profile_overlay() -> None:
    config = YanshiConfig(
        defaults=DefaultsConfig(effort="medium"),
        profiles={"cheap": ProfileConfig(effort="low", cost_ceiling_usd=0.5)},
    )
    resolved = resolve_dispatch({}, config=config, profile="cheap")

    assert resolved.kwargs["reasoning_effort"] == "low"
    assert resolved.kwargs["cost_ceiling_usd"] == 0.5


def test_resolve_dispatch_override_beats_profile_and_defaults() -> None:
    config = YanshiConfig(
        defaults=DefaultsConfig(effort="medium"),
        profiles={"cheap": ProfileConfig(effort="low")},
    )
    resolved = resolve_dispatch({"reasoning_effort": "high"}, config=config, profile="cheap")

    assert resolved.kwargs["reasoning_effort"] == "high"


def test_resolve_dispatch_unknown_profile_warns_and_ignores() -> None:
    config = YanshiConfig(defaults=DefaultsConfig(effort="medium"))
    resolved = resolve_dispatch({}, config=config, profile="nope")

    assert resolved.kwargs["reasoning_effort"] == "medium"
    warning = next(w for w in resolved.warnings if w.code == "profile_unknown")
    assert warning.detail["profile"] == "nope"
    assert warning.detail["available"] == []


def test_resolve_dispatch_allow_clamp() -> None:
    config = YanshiConfig(
        defaults=DefaultsConfig(allow=AllowMode.YOLO),
        limits=LimitsConfig(max_allow=AllowMode.READ_ONLY),
    )
    resolved = resolve_dispatch({}, config=config)

    assert resolved.kwargs["allow"] == AllowMode.READ_ONLY
    assert isinstance(resolved.kwargs["allow"], AllowMode)
    clamp = next(w for w in resolved.warnings if w.code == "capability_clamped")
    assert clamp.detail["field"] == "allow"


def test_resolve_dispatch_cost_clamp() -> None:
    config = YanshiConfig(limits=LimitsConfig(max_cost_usd=10.0))
    resolved = resolve_dispatch({"cost_ceiling_usd": 100.0}, config=config)

    assert resolved.kwargs["cost_ceiling_usd"] == 10.0
    clamp = next(w for w in resolved.warnings if w.code == "capability_clamped")
    assert clamp.detail["field"] == "cost_ceiling_usd"


def test_resolve_dispatch_timeout_clamp() -> None:
    config = YanshiConfig(limits=LimitsConfig(max_timeout_s=300))
    resolved = resolve_dispatch({"timeout_s": 1000}, config=config)

    assert resolved.kwargs["timeout_s"] == 300
    clamp = next(w for w in resolved.warnings if w.code == "capability_clamped")
    assert clamp.detail["field"] == "timeout_s"


def test_resolve_dispatch_cost_imposed_on_unset_without_warning() -> None:
    config = YanshiConfig(limits=LimitsConfig(max_cost_usd=10.0))
    resolved = resolve_dispatch({}, config=config)

    assert resolved.kwargs["cost_ceiling_usd"] == 10.0
    assert resolved.warnings == []


def test_resolve_dispatch_effort_alias_from_toml(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_CONFIG_FILENAME
    _write(path, '[defaults]\neffort = "high"\n')
    config = parse_config_file(path)

    resolved = resolve_dispatch({}, config=config)

    assert resolved.kwargs["reasoning_effort"] == "high"


def test_resolve_dispatch_explicit_model_passes_through() -> None:
    config = YanshiConfig(defaults=DefaultsConfig(model="default-model"))
    resolved = resolve_dispatch({"model": "custom-model"}, config=config)

    assert resolved.kwargs["model"] == "custom-model"


def test_resolved_kwargs_construct_valid_runspec() -> None:
    config = YanshiConfig(defaults=DefaultsConfig(effort="high"))
    resolved = resolve_dispatch({"allow": "yolo"}, config=config)

    assert "cli" not in resolved.kwargs
    assert "prompt" not in resolved.kwargs

    spec = RunSpec(cli="claude", prompt="hi", **resolved.kwargs)
    assert spec.cli == "claude"
    assert spec.reasoning_effort == "high"
    assert spec.allow == AllowMode.YOLO


def test_resolved_kwargs_with_cli_construct_runspec() -> None:
    config = YanshiConfig(defaults=DefaultsConfig(cli="claude", effort="medium"))
    resolved = resolve_dispatch({}, config=config)

    assert resolved.kwargs["cli"] == "claude"
    spec = RunSpec(prompt="hi", **resolved.kwargs)
    assert spec.cli == "claude"
    assert spec.reasoning_effort == "medium"
