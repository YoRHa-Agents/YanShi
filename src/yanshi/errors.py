"""YanShi errors and failure classification."""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """Normative error categories from governance G6.2."""

    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    OVERLOADED = "overloaded"
    AUTH = "auth"
    BILLING = "billing"
    INVALID_REQUEST = "invalid_request"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        """Whether this category may be retried under the supervisor policy."""

        return self in {
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.SERVER_ERROR,
            ErrorCategory.OVERLOADED,
        }


class YanShiError(RuntimeError):
    """Base exception with explicit category and retryability."""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.detail = detail or {}

    @property
    def retryable(self) -> bool:
        """Whether this error category is retryable."""

        return self.category.retryable


class PreflightError(YanShiError):
    """Raised when dispatch preflight fails before spawn."""


class PathBoundaryError(YanShiError):
    """Raised when workdir/add_dirs path validation fails."""


class AdapterError(YanShiError):
    """Raised by adapters for malformed commands or parse failures."""


def classify_error_text(text: str) -> ErrorCategory:
    """Classify vendor error text into a governance category.

    The classifier is deliberately conservative: unknown text becomes
    `unknown`, and callers must surface the raw message alongside the category.
    """

    lower = text.lower()
    if any(marker in lower for marker in ("rate limit", "429", "too many requests")):
        return ErrorCategory.RATE_LIMIT
    if any(marker in lower for marker in ("overloaded", "capacity", "busy")):
        return ErrorCategory.OVERLOADED
    if any(marker in lower for marker in ("server error", "5xx", "500", "502", "503", "504")):
        return ErrorCategory.SERVER_ERROR
    if any(marker in lower for marker in ("auth", "unauthorized", "not logged in", "login")):
        return ErrorCategory.AUTH
    if any(marker in lower for marker in ("billing", "quota", "payment", "credit")):
        return ErrorCategory.BILLING
    if any(marker in lower for marker in ("invalid request", "bad request", "schema")):
        return ErrorCategory.INVALID_REQUEST
    if any(marker in lower for marker in ("max output", "maximum output", "output tokens")):
        return ErrorCategory.MAX_OUTPUT_TOKENS
    return ErrorCategory.UNKNOWN
