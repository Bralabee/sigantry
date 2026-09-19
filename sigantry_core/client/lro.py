"""Long-running operation polling per the Microsoft Learn Fabric LRO spec.

Handles the documented edge cases from the Learn article plus a handful of
operational observations captured in RESEARCH.md and PITFALLS.md:

1. ``200`` inline success (no LRO; initial body IS the result).
2. ``201`` inline success (no LRO).
3. ``202`` + ``x-ms-operation-id`` + ``Location`` (the standard path).
4. ``202`` + ``Location`` only - operation id inferred from the URL's last
   path segment.
5. Body-embedded ``operationId`` (e.g. Deployment Pipelines).
6. ``Succeeded`` WITH result - ``Location`` header flips to ``/result``
   between polls; we issue one final GET on the result URL and return the
   body.
7. ``Succeeded`` WITHOUT result - no ``/result`` Location; we return ``None``.
8. ``Failed`` terminal - body carries an ``error`` object; we raise
   :class:`sigantry_core.client.errors.OperationFailedError`.
9. ``404`` mid-poll (disappearing operation) - tolerate up to
   :data:`MAX_DISAPPEAR_POLLS` consecutive 404s, then raise
   :class:`sigantry_core.client.errors.LROTimeoutError`.
10. Timeout / max_polls hard caps - T-2-04 (infinite LRO loop) mitigation.
    Both caps enforced simultaneously so no single bug in either accounting
    path can leave the poller spinning forever.

Retry-After handling reuses :func:`sigantry_core.client.retry._parse_retry_after`
(seconds + HTTP-date, 60s cap) so there is exactly one parser in the package.
The poller honours Retry-After on BOTH the initial ``202`` response AND every
subsequent state-poll response (Pitfall 1 from RESEARCH.md - some servers omit
Retry-After from 202 but include it on the state endpoint, so we must read it
from every response, not only the first).

Spec references:
- learn.microsoft.com/en-us/rest/api/fabric/articles/long-running-operation
- learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-state
- .planning/phases/02-rest-api-client-layer/02-RESEARCH.md Pattern 3, section 14.3
- .planning/research/PITFALLS.md Pitfall 2 (LRO polling without Retry-After)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sigantry_core.client.errors import (
    LROTimeoutError,
    NotFoundError,
    OperationFailedError,
)
from sigantry_core.client.logging import (
    reset_operation_id,
    set_operation_id,
)
from sigantry_core.client.retry import _parse_retry_after

if TYPE_CHECKING:
    from sigantry_core.client.base import BaseRestClient
    from sigantry_core.client.models import HttpResponse

logger = logging.getLogger("sigantry_core.client.lro")

TERMINAL_STATUSES: frozenset[str] = frozenset({"Succeeded", "Failed"})
DEFAULT_TIMEOUT: float = 600.0
DEFAULT_MAX_POLLS: int = 100
MAX_DISAPPEAR_POLLS: int = 3
"""Consecutive 404s tolerated on the state URL before raising LROTimeoutError.

Fabric occasionally returns 404 briefly while an operation is still being
registered on the state service; tolerating a small number of them smooths
over that race without letting a perma-404 spin forever.
"""
DEFAULT_INITIAL_RETRY_AFTER: float = 3.0


def _get_header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup.

    httpx lowercases header names when iterating ``response.headers``, so our
    :class:`HttpResponse.headers` dict stores them as lowercase. Fabric/Power BI
    docs reference the canonical mixed-case names (``Location``, ``Retry-After``,
    ``x-ms-operation-id``) and those names flow through the LRO module. This
    helper normalises both sides so we can use the documented names while still
    reading lowercase keys out of :class:`HttpResponse`.
    """
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def extract_operation_id(initial: HttpResponse) -> str | None:
    """Locate the operation id across the three sources Fabric uses.

    Preference order:
      1. ``x-ms-operation-id`` response header (canonical path).
      2. Body field ``operationId`` (e.g. Deployment Pipelines endpoints).
      3. Last non-empty path segment of the ``Location`` header, ignoring a
         trailing ``/result`` (because ``.../operations/{id}/result`` should
         still resolve to ``{id}``, not ``result``).

    Returns ``None`` if none of the three yield a value.
    """
    header_val = _get_header(initial.headers, "x-ms-operation-id")
    if header_val:
        return header_val
    if isinstance(initial.json_body, dict):
        body_val = initial.json_body.get("operationId") or initial.json_body.get("operation_id")
        if isinstance(body_val, str) and body_val:
            return body_val
    loc = _get_header(initial.headers, "Location")
    if loc:
        path = urlparse(loc).path.rstrip("/")
        if path.endswith("/result"):
            path = path[: -len("/result")]
        _, _, tail = path.rpartition("/")
        if tail and tail != "result":
            return tail
    return None


def poll_operation(
    client: BaseRestClient,
    *,
    operation_id: str,
    state_url: str,
    scope: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_polls: int = DEFAULT_MAX_POLLS,
    initial_retry_after: float = DEFAULT_INITIAL_RETRY_AFTER,
) -> dict[str, Any] | list[Any] | None:
    """Poll a Fabric LRO until terminal. Returns the result payload or ``None``.

    Args:
        client: a :class:`BaseRestClient` (retry + rate-limit + correlated
            logging are already wired via its ``.send`` method).
        operation_id: the canonical operation id; set on the ``operation_id``
            contextvar for the duration of polling so every log record this
            call emits can be joined to the original request.
        state_url: absolute URL of the state endpoint. The Fabric spec states
            this is always the ``Location`` header from the 202 response.
        scope: OAuth scope override (default: ``client._default_scope``).
        timeout: wall-clock limit in seconds. Default 600. Enforced BEFORE
            each poll so a long ``Retry-After`` sleep does not push us past
            the cap unexamined.
        max_polls: hard cap on poll iterations. Default 100.
        initial_retry_after: seconds to wait before the first poll. Caller
            passes the Retry-After parsed from the initial 202 response.

    Returns:
        - The parsed JSON body of the result endpoint (when ``Succeeded`` and
          the Location header flipped to ``.../result``).
        - ``None`` when the operation succeeded but has no result payload
          (no ``Location`` flip).

    Raises:
        OperationFailedError: terminal ``Failed`` status with an error body.
        LROTimeoutError: ``timeout`` exceeded, ``max_polls`` exceeded, or
            more than :data:`MAX_DISAPPEAR_POLLS` consecutive 404s on the
            state URL. The exception's ``last_status`` attribute carries the
            last observed status (or ``disappeared_after_N_404s`` for the
            404 path), so operators can triage without re-running the call.
    """
    started = time.monotonic()
    retry_after = initial_retry_after
    current_url = state_url
    last_status = "NotStarted"
    consecutive_404s = 0
    op_token = set_operation_id(operation_id)

    try:
        for poll_idx in range(max_polls):
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                raise LROTimeoutError(
                    operation_id=operation_id,
                    elapsed_seconds=elapsed,
                    last_status=last_status,
                )

            # Sleep BEFORE sending - the Retry-After semantics are "wait at
            # least this long before polling again". On the first iteration
            # that's the Retry-After from the initial 202 response.
            if retry_after > 0:
                time.sleep(retry_after)
            else:
                # Still call sleep(0) so tests that mock time.sleep can
                # observe the Retry-After sequence deterministically.
                time.sleep(0)

            try:
                resp = client.send("GET", current_url, scope=scope)
            except NotFoundError:
                consecutive_404s += 1
                logger.warning(
                    "lro_state_404",
                    extra={
                        "operation_id": operation_id,
                        "consecutive_404s": consecutive_404s,
                        "poll": poll_idx,
                    },
                )
                if consecutive_404s >= MAX_DISAPPEAR_POLLS:
                    raise LROTimeoutError(
                        operation_id=operation_id,
                        elapsed_seconds=time.monotonic() - started,
                        last_status=f"disappeared_after_{consecutive_404s}_404s",
                    ) from None
                continue

            consecutive_404s = 0
            body = resp.json_body if isinstance(resp.json_body, dict) else {}
            last_status = body.get("status", "Undefined")
            parsed_ra = _parse_retry_after(_get_header(resp.headers, "Retry-After"))
            if parsed_ra is not None:
                retry_after = parsed_ra

            logger.info(
                "lro_poll",
                extra={
                    "operation_id": operation_id,
                    "status": last_status,
                    "percent_complete": body.get("percentComplete"),
                    "poll": poll_idx,
                    "elapsed_s": elapsed,
                },
            )

            if last_status == "Failed":
                err = body.get("error") or {}
                raise OperationFailedError(
                    operation_id=operation_id,
                    error_code=err.get("errorCode", "Unknown"),
                    message=err.get("message", "operation failed"),
                    details=err.get("moreDetails"),
                )

            if last_status == "Succeeded":
                new_location = _get_header(resp.headers, "Location")
                if new_location and new_location.rstrip("/").endswith("/result"):
                    result_resp = client.send("GET", new_location, scope=scope)
                    return result_resp.json_body
                return None

            # Anything else is treated as Running. Surface unknown statuses
            # at warn so an operator can catch upstream contract drift.
            if last_status not in ("Running", "NotStarted"):
                logger.warning(
                    "lro_unknown_status",
                    extra={
                        "operation_id": operation_id,
                        "status": last_status,
                    },
                )

        # Fell off the end of the max_polls range without reaching terminal.
        raise LROTimeoutError(
            operation_id=operation_id,
            elapsed_seconds=time.monotonic() - started,
            last_status=last_status,
        )
    finally:
        reset_operation_id(op_token)
