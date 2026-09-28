"""Classify node exceptions to decide whether a retry is worthwhile.

Only transient failures are retryable; logic errors (ValueError from
sandboxed code, validation failures) must fail fast instead of burning
the retry budget. Classification is deliberately conservative — when in
doubt, do not retry.
"""
import asyncio
import re

RETRY_CATEGORIES = ("rate_limit", "timeout", "server_error")

# Status codes only count in an HTTP-ish context ("error code: 503"), never
# as bare numbers — prose like "row 503 failed validation" is not a 5xx.
_CODE_RE = {
    "rate_limit": re.compile(r"(http|status|error|code)\s*:?\s*429\b"),
    "server_error": re.compile(r"(http|status|error|code)\s*:?\s*50[0-4]\b"),
}


def classify_error(exc: BaseException) -> str | None:
    """Return the retry category for an exception, or None if not retryable."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"

    # Structured status codes win over message sniffing.
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        if status == 429:
            return "rate_limit"
        if 500 <= status < 600:
            return "server_error"

    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if "timeout" in name or "timed out" in msg or "timeout" in msg:
        return "timeout"
    for category, pattern in _CODE_RE.items():
        if pattern.search(msg):
            return category
    if "rate limit" in msg or "too many requests" in msg or "rate_limit" in name:
        return "rate_limit"
    if any(phrase in msg for phrase in (
        "server error", "internal error", "overloaded",
        "bad gateway", "service unavailable", "gateway timeout",
    )):
        return "server_error"
    return None
