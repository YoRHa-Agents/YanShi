"""Token and cost accounting."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from yanshi.contracts import PricingStatus, Usage

_BUILTIN_PRICING_USD_PER_M: dict[str, tuple[float, float]] = {
    "claude": (3.0, 15.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.8, 4.0),
    "gpt-5": (1.25, 10.0),
    "gemini": (1.25, 5.0),
}


class UsageEstimate(BaseModel):
    """Usage plus cost and pricing provenance."""

    model_config = ConfigDict(extra="forbid")

    usage: Usage
    cost_usd: float | None
    pricing_status: PricingStatus


class UsageMeter:
    """Derive cost from native values or a cached pricing table."""

    def __init__(self, pricing_cache_path: str | Path | None = None) -> None:
        self.pricing_cache_path = Path(pricing_cache_path) if pricing_cache_path else None
        self._pricing = dict(_BUILTIN_PRICING_USD_PER_M)
        self._pricing.update(self._load_cache())

    def estimate(
        self,
        *,
        model: str | None,
        usage: Usage,
        native_cost_usd: float | None = None,
    ) -> UsageEstimate:
        """Return normalized cost with provenance."""

        if native_cost_usd is not None:
            return UsageEstimate(
                usage=usage,
                cost_usd=native_cost_usd,
                pricing_status=PricingStatus.NATIVE,
            )
        pricing = self._lookup_pricing(model)
        if pricing is None:
            return UsageEstimate(usage=usage, cost_usd=None, pricing_status=PricingStatus.MISSING)
        input_per_m, output_per_m = pricing
        cost = (
            usage.input_tokens * input_per_m / 1_000_000.0
            + usage.cached_input_tokens * input_per_m / 1_000_000.0
            + usage.output_tokens * output_per_m / 1_000_000.0
            + usage.reasoning_tokens * output_per_m / 1_000_000.0
        )
        return UsageEstimate(usage=usage, cost_usd=cost, pricing_status=PricingStatus.PRICED)

    def _lookup_pricing(self, model: str | None) -> tuple[float, float] | None:
        if not model:
            return None
        lowered = model.lower()
        for prefix, pricing in self._pricing.items():
            if lowered.startswith(prefix):
                return pricing
        return None

    def _load_cache(self) -> dict[str, tuple[float, float]]:
        if self.pricing_cache_path is None or not self.pricing_cache_path.is_file():
            return {}
        raw = json.loads(self.pricing_cache_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        parsed: dict[str, tuple[float, float]] = {}
        for model, value in raw.items():
            if (
                isinstance(model, str)
                and isinstance(value, list)
                and len(value) == 2
                and all(isinstance(item, int | float) for item in value)
            ):
                parsed[model] = (float(value[0]), float(value[1]))
        return parsed
