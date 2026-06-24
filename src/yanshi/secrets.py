"""Secret redaction for logs and summarizer inputs."""

from __future__ import annotations

import re
from collections.abc import Mapping

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)([^\s'\";,]+)"),
    re.compile(r"(?i)(bearer\s+)([a-z0-9._~+/=-]{12,})"),
    re.compile(r"(?i)(sk-[a-z0-9]{16,})"),
)

_REDACTION = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Redact common token/password/API-key shapes from text."""

    redacted = text
    redacted = _SECRET_PATTERNS[0].sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTION}",
        redacted,
    )
    redacted = _SECRET_PATTERNS[1].sub(lambda match: f"{match.group(1)}{_REDACTION}", redacted)
    redacted = _SECRET_PATTERNS[2].sub(_REDACTION, redacted)
    return redacted


def redact_mapping(values: Mapping[str, str]) -> dict[str, str]:
    """Return a redacted copy of a string mapping."""

    return {key: redact_secrets(value) for key, value in values.items()}
