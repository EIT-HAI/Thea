"""Dependency-free redaction for logs and compaction input."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "app_secret",
    "authorization",
    "cookie",
    "encrypt_key",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "verification_token",
}
_SENSITIVE_KEY_SUFFIXES = ("_api_key", "_access_token", "_secret", "_password")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")
_DATA_URL_PATTERN = re.compile(
    r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+",
)
REDACTED = "<redacted>"


def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact credentials and large inline image payloads."""
    if isinstance(value, bytes):
        return f"<binary payload omitted: {len(value)} bytes>"
    if isinstance(value, tuple):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.strip().lower()
            if normalized in _SENSITIVE_KEYS or normalized.endswith(
                _SENSITIVE_KEY_SUFFIXES
            ):
                sanitized[key] = REDACTED
            elif (
                normalized in {"data", "base64"}
                and isinstance(item, str)
                and len(item) > 256
            ):
                sanitized[key] = f"<encoded payload omitted: {len(item)} chars>"
            else:
                sanitized[key] = redact_sensitive_data(item)
        return sanitized
    if isinstance(value, str):
        text = _DATA_URL_PATTERN.sub("<image payload omitted>", value)
        text = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)
        return _KEY_PATTERN.sub(REDACTED, text)
    return value


__all__ = ["REDACTED", "redact_sensitive_data"]
