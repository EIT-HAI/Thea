"""Errors and retry policy shared by LLM providers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class ModelCallError(RuntimeError):
    """A provider call failed and no model decision was produced."""

    def __init__(self, provider: str, attempts: int, cause: BaseException):
        self.provider = provider
        self.attempts = attempts
        self.cause = cause
        super().__init__(
            f"{provider} call failed after {attempts} attempt(s): "
            f"{type(cause).__name__}: {cause}",
        )


def is_retryable_exception(exc: BaseException) -> bool:
    """Return whether retrying a provider request may succeed."""
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(exc, "code", None)
    try:
        status_code = int(value) if value is not None else None
    except (TypeError, ValueError):
        status_code = None

    if status_code is not None:
        return status_code in {408, 409, 425, 429} or status_code >= 500
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    name = type(exc).__name__.lower()
    return any(
        marker in name
        for marker in (
            "connection",
            "timeout",
            "ratelimit",
            "serviceunavailable",
            "internalserver",
        )
    )


def run_with_retries(
    operation: Callable[[], Any],
    *,
    provider: str,
    max_attempts: int,
) -> Any:
    """Run one provider operation with bounded exponential backoff."""
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= attempts or not is_retryable_exception(exc):
                raise ModelCallError(provider, attempt, exc) from exc
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("retry loop terminated without a result")
