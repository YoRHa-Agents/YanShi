from __future__ import annotations

from yanshi.secrets import redact_mapping, redact_secrets


def test_redact_secrets_masks_key_token_password_and_bearer() -> None:
    text = "api_key=abc123 token:tok password = p Bearer abcdefghijklmnop sk-abcdefghijklmnop"
    redacted = redact_secrets(text)
    assert "abc123" not in redacted
    assert "token:tok" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "sk-abcdefghijklmnop" not in redacted


def test_redact_mapping_returns_copy() -> None:
    values = {"AUTH": "token=abc"}
    redacted = redact_mapping(values)
    assert redacted == {"AUTH": "token=[REDACTED]"}
    assert values == {"AUTH": "token=abc"}
