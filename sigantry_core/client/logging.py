"""Correlated structured logging for sigantry_core.client.

CLIENT-06:
- ``correlation_id`` and ``operation_id`` are kept in ``contextvars.ContextVar``
  so concurrent callers do not share state.
- ``JsonFormatter`` serialises each log record to JSON with a fixed field set.
- Sensitive headers (``Authorization``, ``X-Api-Key``, ``Set-Cookie``,
  ``Cookie``) are replaced with the literal string ``"<redacted>"`` before
  serialisation - T-2-02 mitigation.
- Body logging is OFF at INFO and truncated to ``BODY_LOG_MAX_BYTES`` bytes at
  DEBUG. Callers supply the body via ``extra={"body": ...}``; nothing is
  scraped off the request object implicitly.

This module does not pull in any extra dependency - the JsonFormatter is a
hand-rolled subclass of ``logging.Formatter`` (~30 lines).
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar, Token
from typing import Any

_CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_OPERATION_ID: ContextVar[str | None] = ContextVar("operation_id", default=None)

SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {"authorization", "x-api-key", "set-cookie", "cookie"}
)
BODY_LOG_MAX_BYTES: int = 2048

_LOG_FIELDS: tuple[str, ...] = (
    "method",
    "url",
    "status",
    "elapsed_ms",
    "retry_count",
    "request_id",
)


def get_correlation_id() -> str | None:
    """Return the current correlation id or ``None`` if no caller set one."""
    return _CORRELATION_ID.get()


def set_correlation_id(value: str | None) -> Token:
    """Set the correlation id; return a reset token for ``reset_correlation_id``."""
    return _CORRELATION_ID.set(value)


def reset_correlation_id(token: Token) -> None:
    _CORRELATION_ID.reset(token)


def get_operation_id() -> str | None:
    return _OPERATION_ID.get()


def set_operation_id(value: str | None) -> Token:
    return _OPERATION_ID.set(value)


def reset_operation_id(token: Token) -> None:
    _OPERATION_ID.reset(token)


def _redact_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Replace values of sensitive headers with ``"<redacted>"``.

    Case-insensitive match on the header name. Non-sensitive headers are
    passed through untouched.
    """
    return {k: ("<redacted>" if k.lower() in SENSITIVE_HEADERS else v) for k, v in headers.items()}


class JsonFormatter(logging.Formatter):
    """Serialise a LogRecord to JSON with the sigantry_core.client field set.

    Contract:
    - Always emits ``ts``, ``level``, ``logger``, ``message``, ``correlation_id``,
      ``operation_id``, plus the six request fields (``method``, ``url``,
      ``status``, ``elapsed_ms``, ``retry_count``, ``request_id``).
    - Missing fields default to ``None`` so downstream log shippers can rely on
      schema stability.
    - If the record carries ``headers`` in extras the dict is redacted before
      serialisation.
    - If the record carries ``body`` it is stringified and truncated at
      ``BODY_LOG_MAX_BYTES`` bytes with a ``"...[truncated]"`` suffix.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": _CORRELATION_ID.get(),
            "operation_id": getattr(record, "operation_id", None) or _OPERATION_ID.get(),
        }
        for key in _LOG_FIELDS:
            payload[key] = getattr(record, key, None)
        headers = getattr(record, "headers", None)
        if isinstance(headers, dict):
            payload["headers"] = _redact_headers(headers)
        body = getattr(record, "body", None)
        if body is not None:
            body_str = str(body)
            if len(body_str) > BODY_LOG_MAX_BYTES:
                body_str = body_str[:BODY_LOG_MAX_BYTES] + "...[truncated]"
            payload["body"] = body_str
        # Pass-through extras commonly used for diagnostics (scope / credential).
        for extra_key in ("scope", "credential"):
            val = getattr(record, extra_key, None)
            if val is not None:
                payload[extra_key] = val
        return json.dumps(payload, default=str)


def configure_client_logging(level: int = logging.INFO) -> None:
    """Install the JsonFormatter on the ``sigantry_core.client`` logger tree.

    Idempotent - repeated calls do not stack multiple handlers.
    """
    logger = logging.getLogger("sigantry_core.client")
    for existing in logger.handlers:
        if isinstance(existing, logging.StreamHandler) and isinstance(
            existing.formatter, JsonFormatter
        ):
            return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
