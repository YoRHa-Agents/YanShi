from __future__ import annotations

from pathlib import Path

from yanshi.contracts import Usage
from yanshi.usage import UsageMeter


def test_usage_meter_prefers_native_cost() -> None:
    estimate = UsageMeter().estimate(
        model="unknown",
        usage=Usage(input_tokens=1),
        native_cost_usd=0.2,
    )
    assert estimate.cost_usd == 0.2
    assert estimate.pricing_status == "native"


def test_usage_meter_prices_known_model() -> None:
    estimate = UsageMeter().estimate(
        model="claude-sonnet-4",
        usage=Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )
    assert estimate.cost_usd == 18.0
    assert estimate.pricing_status == "priced"


def test_usage_meter_reports_missing_pricing() -> None:
    estimate = UsageMeter().estimate(model=None, usage=Usage(input_tokens=1))
    assert estimate.cost_usd is None
    assert estimate.pricing_status == "missing"


def test_usage_meter_loads_cache(tmp_path: Path) -> None:
    cache = tmp_path / "pricing-cache.json"
    cache.write_text('{"custom-model":[2,4]}', encoding="utf-8")
    estimate = UsageMeter(cache).estimate(
        model="custom-model-v1",
        usage=Usage(input_tokens=1_000_000, output_tokens=1_000_000),
    )
    assert estimate.cost_usd == 6.0
