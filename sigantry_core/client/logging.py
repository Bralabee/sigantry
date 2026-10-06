"""Correlated structured logging for sigantry_core.client.

CLIENT-06:
- ``correlation_id`` and ``operation_id`` are kept in ``contextvars.ContextVar``
  so concurrent callers do not share state.
- ``JsonFormatter`` serialises each log record to JSON with a fixed field set.
- Sensitive headers (``Authorization``, ``X-Api-Key``, ``Set-Cookie``,
  ``Cookie``) are replaced with the literal string ``"<redacted>"`` before
  serialisation - T-2-02 mitigation.
- A logged ``url`` keeps its scheme, host, path and query parameter NAMES;
  query values, userinfo and the fragment are replaced with ``"<redacted>"``
  (see :func:`redact_url`). A response can hand the client a URL whose query
  carries a signature or token, and the URL is logged on every request. The
  same applies to ``operation_id``: an ARM operation is identified by its
  polling URL.
- A ``credential`` extra is printed only when the client itself set it, as a
  :class:`CredentialClassName`; any other value is replaced with
  ``"<redacted>"``. Third-party code logging through this tree cannot put a
  secret in that field by accident.
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
from urllib.parse import urlsplit, urlunsplit

_CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_OPERATION_ID: ContextVar[str | None] = ContextVar("operation_id", default=None)

SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {"authorization", "x-api-key", "set-cookie", "cookie"}
)
BODY_LOG_MAX_BYTES: int = 2048
REDACTED: str = "<redacted>"

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
    return {k: (REDACTED if k.lower() in SENSITIVE_HEADERS else v) for k, v in headers.items()}


class CredentialClassName(str):
    """A credential class name the client logs on purpose.

    :class:`JsonFormatter` prints a ``credential`` extra only when it is an
    instance of this type; any other value is replaced with ``"<redacted>"``.
    """

    __slots__ = ()


def redact_url(url: Any) -> Any:
    """Mask the parts of ``url`` that can carry a credential.

    Query values become ``<redacted>`` (the parameter names stay, so a log
    still shows which parameters were sent), as do userinfo and the fragment.
    Scheme, host and path are unchanged. A URL with none of those parts is
    returned as it was. ``None`` is returned as is; any other non-string
    (an ``httpx.URL``, say) is read through ``str()``, which is how the JSON
    output would print it anyway.
    """
    if url is None:
        return None
    if not isinstance(url, str):
        url = str(url)
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{REDACTED}@{netloc.rpartition('@')[2]}"
    query = parts.query
    if query:
        pieces = []
        for piece in query.split("&"):
            name, sep, _value = piece.partition("=")
            if not piece:
                pieces.append(piece)
            elif sep:
                pieces.append(f"{name}={REDACTED}")
            else:
                pieces.append(REDACTED)
        query = "&".join(pieces)
    fragment = REDACTED if parts.fragment else ""
    if (netloc, query, fragment) == (parts.netloc, parts.query, parts.fragment):
        return url
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


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
    - ``url`` and ``operation_id`` are passed through :func:`redact_url`.
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
        payload["url"] = redact_url(payload["url"])
        # An ARM operation's identity is its polling URL (see client.arm).
        # A plain operation id has no query, so it prints unchanged.
        payload["operation_id"] = redact_url(payload["operation_id"])
        headers = getattr(record, "headers", None)
        if isinstance(headers, dict):
            payload["headers"] = _redact_headers(headers)
        body = getattr(record, "body", None)
        if body is not None:
            body_str = str(body)
            if len(body_str) > BODY_LOG_MAX_BYTES:
                body_str = body_str[:BODY_LOG_MAX_BYTES] + "...[truncated]"
            payload["body"] = body_str
        # Diagnostic extras. ``scope`` passes through; ``credential`` only
        # when the client marked it as a class name (see CredentialClassName).
        scope = getattr(record, "scope", None)
        if scope is not None:
            payload["scope"] = scope
        credential = getattr(record, "credential", None)
        if credential is not None:
            payload["credential"] = (
                str(credential) if isinstance(credential, CredentialClassName) else REDACTED
            )
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
