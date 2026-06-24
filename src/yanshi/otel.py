"""Optional fail-open OpenTelemetry hooks."""

from __future__ import annotations

import logging
from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)


def emit_genai_event(name: str, attributes: Mapping[str, object]) -> bool:
    """Emit a gen_ai.* event when OpenTelemetry is installed; otherwise fail open."""

    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
    except ImportError:
        _LOGGER.info("opentelemetry not installed; skipping event %s", name)
        return False
    try:
        tracer = trace.get_tracer("yanshi")
        with tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                span.set_attribute(f"gen_ai.{key}", value)
    except Exception as exc:  # noqa: BLE001 - optional telemetry must be fail-open with logging.
        _LOGGER.warning("failed to emit otel event %s: %s", name, exc)
        return False
    return True
