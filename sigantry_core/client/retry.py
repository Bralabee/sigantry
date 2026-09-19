"""Retry policy for sigantry_core.client.

CLIENT-02:

- Retries on 408/429/500/502/503/504 and ``httpx.ConnectError`` /
  ``httpx.ReadTimeout`` for *idempotent* verbs (GET / HEAD / OPTIONS /
  PUT).
- For *non-idempotent* verbs (POST / PATCH / DELETE) retries only on
  408/429/503 **and only when a ``Retry-After`` header is present** —
  the server-supplied retry signal is the only safe way to resume a
  mutation that may have already taken effect server-side. Other 5xx
  statuses propagate to the caller for explicit handling.
- Does not retry on 400/401/403/404/409/422 (they are routed to the
  appropriate typed subclass via ``classify_response``).
- Honours ``Retry-After`` (integer seconds or HTTP-date) capped at
  ``MAX_WAIT_SECONDS``.
- Exhausts at ``MAX_ATTEMPTS`` attempts; tenacity's ``reraise=True`` makes
  the final response / exception the caller's responsibility.
- ``ConnectError`` is retried for every method (the connection never
  reached the server, so the request was not applied).
- ``ReadTimeout`` is retried only for idempotent verbs — for
  non-idempotent verbs the server may have applied the change before the
  read timed out, so blind retry would risk duplication.
"""

from __future__ import annotations

import email.utils
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar, cast

import httpx
import tenacity

from sigantry_core.client.errors import (
    AuthError,
    HttpError,
    NotFoundError,
    RateLimitError,
    ServerError,
)

logger = logging.getLogger("sigantry_core.client.retry")

RETRY_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
NON_IDEMPOTENT_RETRY_STATUSES: frozenset[int] = frozenset({408, 429, 503})
NO_RETRY_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 409, 422})
IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "PUT"})
MAX_WAIT_SECONDS: int = 60
MAX_ATTEMPTS: int = 5

T = TypeVar("T")


def _is_idempotent(method: str | None) -> bool:
    """Method-tier classifier.

    ``None`` is treated as idempotent for backwards-compat with callers
    that have not yet been migrated to the method-aware API. Internal
    callers (BaseRestClient.send / send_multipart) always pass the verb.
    """
    if method is None:
        return True
    return method.upper() in IDEMPOTENT_METHODS


def _parse_retry_after(header: str | None) -> float | None:
    """Parse a ``Retry-After`` header value.

    Accepts either a decimal number of seconds or an RFC 7231 HTTP-date.
    Returns ``None`` for ``None``/garbage input so callers can fall back to
    exponential backoff. Negative or past values are clamped to 0. Future
    values above ``MAX_WAIT_SECONDS`` are capped at ``MAX_WAIT_SECONDS``.
    """
    if header is None or header == "":
        return None
    try:
        seconds = float(header)
        return min(max(seconds, 0.0), float(MAX_WAIT_SECONDS))
    except (ValueError, TypeError):
        pass
    try:
        target = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(tz=UTC)).total_seconds()
    return min(max(delta, 0.0), float(MAX_WAIT_SECONDS))


def _is_retryable_status(code: int, method: str | None = None) -> bool:
    """Method-aware status-code retryability.

    Idempotent verbs (and the legacy ``method=None`` default) match the
    full ``RETRY_STATUSES`` set. Non-idempotent verbs match only the
    narrow ``NON_IDEMPOTENT_RETRY_STATUSES`` set; the additional
    ``Retry-After``-presence check lives in :func:`_should_retry_response`
    because it needs the response headers.
    """
    if _is_idempotent(method):
        return code in RETRY_STATUSES
    return code in NON_IDEMPOTENT_RETRY_STATUSES


def _wait_from_response(retry_state: tenacity.RetryCallState) -> float:
    """Respect ``Retry-After`` when present, else 2^(attempt-1) exp backoff."""
    outcome = retry_state.outcome
    if outcome is not None and not outcome.failed:
        result = outcome.result()
        if isinstance(result, httpx.Response):
            seconds = _parse_retry_after(result.headers.get("Retry-After"))
            if seconds is not None:
                return seconds
    return min(2.0 ** (retry_state.attempt_number - 1), float(MAX_WAIT_SECONDS))


def _should_retry_response(resp: httpx.Response | None, method: str | None = None) -> bool:
    """Decide whether ``resp`` warrants another attempt for the given verb.

    For idempotent verbs (or the unspecified default) any code in
    :data:`RETRY_STATUSES` triggers retry. For non-idempotent verbs we
    additionally require a ``Retry-After`` header — the server is
    explicitly asking us to retry — and we only consider 408/429/503.
    Other 5xx propagate to the caller, who must decide whether the
    side effect was applied.
    """
    if resp is None:
        return False
    if not _is_retryable_status(resp.status_code, method):
        return False
    if _is_idempotent(method):
        return True
    return resp.headers.get("Retry-After") is not None


def _build_retry_exception_predicate(method: str | None) -> tenacity.retry_base:
    """Connect errors retry for all verbs; read timeouts only for idempotent.

    For idempotent verbs both ``ConnectError`` and ``ReadTimeout`` are
    treated as retry-safe (idempotent retry semantics). For
    non-idempotent verbs only ``ConnectError`` is safe — the request
    never reached the server. ``ReadTimeout`` on a non-idempotent verb
    means the server may have applied the change before the read timed
    out, so blind retry is unsafe.
    """
    if _is_idempotent(method):
        return tenacity.retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout))
    return tenacity.retry_if_exception_type(httpx.ConnectError)


def build_retry_policy(
    max_attempts: int = MAX_ATTEMPTS,
    *,
    method: str | None = None,
) -> tenacity.Retrying:
    """Build the tenacity ``Retrying`` engine used by the base client.

    ``method`` selects the retry tier:
        * idempotent (GET/HEAD/OPTIONS/PUT or ``None``) — full retry set
          on status, both ConnectError and ReadTimeout retried.
        * non-idempotent (POST/PATCH/DELETE) — narrow status set + only
          when Retry-After is present, ReadTimeout NOT retried.

    ``reraise=True`` re-raises the final exception from the wrapped callable
    (covers ``httpx.ConnectError`` / ``httpx.ReadTimeout`` exhaustion).
    Result-based exhaustion still raises ``tenacity.RetryError`` - callers
    should use :func:`execute_with_retry` which unwraps ``RetryError`` and
    returns the last response, so the caller then runs
    :func:`classify_response` to convert the status code into a typed
    subclass.
    """

    def _retry_predicate(resp: httpx.Response | None) -> bool:
        return _should_retry_response(resp, method)

    return tenacity.Retrying(
        stop=tenacity.stop_after_attempt(max_attempts),
        wait=_wait_from_response,
        retry=(
            tenacity.retry_if_result(_retry_predicate) | _build_retry_exception_predicate(method)
        ),
        reraise=True,
    )


def execute_with_retry(
    func: Callable[[], httpx.Response],
    *,
    max_attempts: int = MAX_ATTEMPTS,
    method: str | None = None,
) -> httpx.Response:
    """Invoke ``func`` through the retry policy and return the final response.

    - If ``func`` raises a retry-eligible exception and all attempts are
      exhausted, that exception is re-raised (``reraise=True``).
    - If ``func`` returns a retry-eligible status code and all attempts are
      exhausted, tenacity raises ``RetryError``; we catch it and return the
      last result so callers can hand it to :func:`classify_response`.
    - ``method`` is the HTTP verb of the wrapped request and selects the
      idempotent vs non-idempotent retry tier.
    """
    policy = build_retry_policy(max_attempts=max_attempts, method=method)
    try:
        return cast(httpx.Response, policy(func))
    except tenacity.RetryError as exc:
        last = exc.last_attempt
        if last is None or last.failed:
            raise
        return cast(httpx.Response, last.result())


def classify_response(resp: httpx.Response) -> None:
    """Raise the appropriate error subclass for a non-retryable non-2xx response.

    Called by ``BaseRestClient`` after the retry loop completes. A successful
    2xx response returns ``None``. A retryable code that exhausted retries
    maps to ``RateLimitError`` (429) or ``ServerError`` (5xx). Non-retryable
    4xx maps to the precise subclass where we have one (401/403 to
    ``AuthError``, 404 to ``NotFoundError``), otherwise to the generic
    ``HttpError``.
    """
    code = resp.status_code
    if 200 <= code < 300:
        return
    body: object
    try:
        body = resp.json()
    except Exception:
        body = resp.text or None
    request_id = resp.headers.get("x-ms-request-id")
    operation_id = resp.headers.get("x-ms-operation-id")

    kwargs = dict(status_code=code, body=body, request_id=request_id, operation_id=operation_id)
    if code == 429:
        raise RateLimitError(**kwargs)
    if code in (401, 403):
        raise AuthError(**kwargs)
    if code == 404:
        raise NotFoundError(**kwargs)
    if 500 <= code < 600:
        raise ServerError(**kwargs)
    raise HttpError(**kwargs)
