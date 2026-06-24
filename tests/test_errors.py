from __future__ import annotations

from yanshi.errors import ErrorCategory, YanShiError, classify_error_text


def test_error_category_retryability() -> None:
    assert ErrorCategory.RATE_LIMIT.retryable is True
    assert ErrorCategory.SERVER_ERROR.retryable is True
    assert ErrorCategory.AUTH.retryable is False


def test_yanshi_error_exposes_category_and_retryability() -> None:
    err = YanShiError("rate limit", category=ErrorCategory.RATE_LIMIT)
    assert err.category == ErrorCategory.RATE_LIMIT
    assert err.retryable is True


def test_classify_error_text() -> None:
    assert classify_error_text("HTTP 429 rate limit") == ErrorCategory.RATE_LIMIT
    assert classify_error_text("billing quota exhausted") == ErrorCategory.BILLING
    assert classify_error_text("not logged in") == ErrorCategory.AUTH
    assert classify_error_text("totally novel failure") == ErrorCategory.UNKNOWN
