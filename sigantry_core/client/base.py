"""BaseRestClient orchestrator.

Wires TokenProvider DI + retry policy + per-endpoint rate-limit bucket +
correlated JSON logging together for every outbound Fabric / Power BI /
Purview REST call. Subclasses (Plan 02-03) only override ``base_url`` and
``default_scope``.

CLIENT-01: the only legitimate place in the codebase to import ``httpx``
(plus the Phase 1 ``sigantry_core/auth/diagnose.py`` exception).
CLIENT-02: retry on 408/429/500/502/503/504 plus connection errors, honour
``Retry-After``.
CLIENT-05: ``pyrate_limiter`` per-endpoint buckets.
CLIENT-06: ``contextvars`` correlation id, JSON formatter, Authorization
header redaction.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Any, Self

import httpx

from sigantry_core import _version as _fd_version
from sigantry_core.auth import TokenProviderProtocol
from sigantry_core.client import rate_limit as _rate_limit
from sigantry_core.client.logging import (
    configure_client_logging,
    get_correlation_id,
    get_operation_id,
    reset_correlation_id,
    set_correlation_id,
)
from sigantry_core.client.models import HttpResponse
from sigantry_core.client.retry import (
    classify_response,
    execute_with_retry,
)

logger = logging.getLogger("sigantry_core.client.base")

_USER_AGENT: str = f"fabric-dataops/{_fd_version.__version__}"
_DEFAULT_TIMEOUT: float = 30.0


class BaseRestClient:
    """Synchronous HTTP client for Fabric / Power BI / Purview.

    Construction is inexpensive and does not contact the auth provider. The
    first :meth:`send` triggers :meth:`TokenProvider.get_token` exactly once
    per scope. Subsequent calls reuse the cached token inside the provider.

    Subclasses in Plan 02-03 set ``base_url`` and ``default_scope`` to the
    Fabric / Power BI / Purview defaults and optionally override pagination
    shape recognition.
    """

    def __init__(
        self,
        *,
        token_provider: TokenProviderProtocol,
        base_url: str,
        default_scope: str,
        default_timeout: float = _DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        # Review-fix MD-02: ``token_provider`` is typed as the structural
        # ``TokenProviderProtocol`` so the GitHub-auth carve-out's
        # ``_NoopTokenProvider`` (which routes Authorization through
        # extra_headers and bypasses the OAuth-scope chain) can satisfy
        # the contract without ``# type: ignore[arg-type]``. Concrete
        # ``TokenProvider`` instances satisfy the Protocol structurally.
        self._tp: TokenProviderProtocol = token_provider
        self._base_url = base_url.rstrip("/")
        self._default_scope = default_scope
        self._default_timeout = default_timeout
        self._http = http_client or httpx.Client(timeout=default_timeout)
        self._credential_logged_for: set[str] = set()
        # Idempotent - does not stack handlers on repeated instantiation.
        configure_client_logging()

    # ---- public API ---------------------------------------------------

    def send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        scope: str | None = None,
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Send an HTTP request with the full sigantry_core.client pipeline.

        Flow:
        1. Resolve the scope (caller override or default).
        2. Build headers (Authorization + User-Agent + correlation id).
        3. Acquire the per-endpoint rate-limit bucket (blocking).
        4. Drive ``execute_with_retry``; the helper unwraps
           ``tenacity.RetryError`` on result-based exhaustion so we always
           get a final response to classify.
        5. Emit the winning-credential log on first send per scope (T-1-01
           continuity from Phase 1).
        6. Emit the request-completed JSON log record.
        7. ``classify_response`` raises a typed error subclass on non-2xx.
        8. Return ``HttpResponse.from_httpx``.
        """
        scope = scope or self._default_scope
        url = self._url(path)
        headers = self._build_headers(scope, extra_headers)

        _rate_limit.try_acquire(method, self._relative_path(path))

        started = time.monotonic()
        retry_count = {"n": 0}

        def _do_call() -> httpx.Response:
            resp = self._http.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                timeout=timeout or self._default_timeout,
            )
            retry_count["n"] += 1
            return resp

        final = execute_with_retry(_do_call, method=method)
        elapsed_ms = (time.monotonic() - started) * 1000.0

        # Total attempts made; retry_count == 1 means no retry happened.
        attempts_made = retry_count["n"]

        self._log_first_credential(scope)
        self._log_request(method, url, final, elapsed_ms, attempts_made - 1)

        # Raises HttpError subclass on non-2xx; returns None on 2xx.
        classify_response(final)

        return HttpResponse.from_httpx(final, elapsed_ms=elapsed_ms)

    def send_multipart(
        self,
        method: str,
        path: str,
        *,
        files: dict[str, tuple[str, Any, str]],
        params: dict[str, Any] | None = None,
        scope: str | None = None,
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Multipart/form-data upload with the full pipeline.

        Mirrors :meth:`send` exactly — scope resolution, Authorization header,
        correlation id, per-endpoint rate-limit bucket, retry policy, typed
        error classification, and correlated JSON logging all inherit from
        the same helpers. The only differences are:

        1. The HTTP body is a multipart/form-data envelope built by
           ``httpx`` from the ``files`` kwarg. We MUST NOT set a
           ``Content-Type`` header manually — httpx generates one with the
           correct boundary.
        2. ``json`` is intentionally not accepted (use :meth:`send` for
           JSON bodies). Extra form fields can be added to ``files`` if
           needed (Fabric Environment staging/libraries only requires the
           file field).

        Args:
            method: HTTP verb (typically ``POST``).
            path: URL path relative to ``self._base_url`` or absolute URL.
            files: mapping of ``field_name -> (filename, content, content_type)``
                — httpx/requests convention.
            params: optional query params.
            scope: OAuth scope override (default: ``self._default_scope``).
            timeout: per-request timeout override.
            extra_headers: extra headers to merge AFTER the pipeline's
                default headers (Authorization is always from the pipeline).
        """
        scope = scope or self._default_scope
        url = self._url(path)
        headers = self._build_headers(scope, extra_headers)
        # httpx generates Content-Type: multipart/form-data; boundary=... itself.
        headers.pop("Content-Type", None)

        _rate_limit.try_acquire(method, self._relative_path(path))

        started = time.monotonic()
        retry_count = {"n": 0}

        def _do_call() -> httpx.Response:
            resp = self._http.request(
                method,
                url,
                files=files,
                params=params,
                headers=headers,
                timeout=timeout or self._default_timeout,
            )
            retry_count["n"] += 1
            return resp

        final = execute_with_retry(_do_call, method=method)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        attempts_made = retry_count["n"]

        self._log_first_credential(scope)
        self._log_request(method, url, final, elapsed_ms, attempts_made - 1)

        classify_response(final)

        return HttpResponse.from_httpx(final, elapsed_ms=elapsed_ms)

    def send_lro(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        scope: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_polls: int | None = None,
    ) -> Any:
        """Issue a request that may or may not be a long-running operation.

        Flow:
        - If the initial response is ``200``/``201`` - the body IS the result.
          Return it directly (no LRO polling).
        - If the initial response is ``202`` - locate the operation id (header
          > body > Location last-segment), build the state URL (Location
          header if present else reconstructed as ``{base_url}/v1/operations/
          {id}``), and delegate to
          :func:`sigantry_core.client.lro.poll_operation` which drives the
          poll loop until terminal.
        - Any other 2xx status code is returned as the body directly (unusual
          but not an error).

        Args:
            method: HTTP verb for the initial call.
            path: absolute URL or path relative to ``self._base_url``.
            params: initial request query parameters.
            json: initial request JSON body.
            scope: OAuth scope override (default: ``self._default_scope``).
            extra_headers: additional request headers (Authorization is
                always set by BaseRestClient; see
                :meth:`_build_headers`).
            timeout: LRO wall-clock cap in seconds. Default: 600.
            max_polls: hard cap on poll iterations. Default: 100.

        Returns:
            The result payload (a ``dict`` / ``list``) or ``None`` if the
            operation succeeded without a result URL.

        Raises:
            OperationFailedError: terminal Failed status.
            LROTimeoutError: ``timeout`` / ``max_polls`` / MAX_DISAPPEAR_POLLS
                exceeded.
            HttpError: the initial call returned a non-2xx status (raised by
                :func:`sigantry_core.client.retry.classify_response`).
        """
        # Lazy imports to avoid a circular dependency (lro imports
        # BaseRestClient only for type checking).
        from sigantry_core.client.lro import (
            DEFAULT_MAX_POLLS,
            DEFAULT_TIMEOUT,
            _get_header,
            extract_operation_id,
            poll_operation,
        )
        from sigantry_core.client.retry import _parse_retry_after

        initial = self.send(
            method,
            path,
            params=params,
            json=json,
            scope=scope,
            extra_headers=extra_headers,
        )

        if initial.status_code in (200, 201):
            return initial.json_body

        if initial.status_code != 202:
            # send() already raised on non-2xx via classify_response.
            # Any other 2xx is unusual but we return the body rather than
            # surprise the caller with an exception.
            return initial.json_body

        operation_id = extract_operation_id(initial)
        if not operation_id:
            from sigantry_core.client.errors import LROTimeoutError

            raise LROTimeoutError(
                operation_id="<unknown>",
                elapsed_seconds=0.0,
                last_status="missing_operation_id",
            )

        # State URL preference: Location header (canonical Fabric path) then
        # reconstructed ``{base_url}/v1/operations/{id}``.
        state_url = _get_header(initial.headers, "Location")
        if not state_url:
            state_url = f"{self._base_url}/v1/operations/{operation_id}"

        retry_after = _parse_retry_after(_get_header(initial.headers, "Retry-After")) or 3.0

        return poll_operation(
            self,
            operation_id=operation_id,
            state_url=state_url,
            scope=scope,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
            max_polls=max_polls if max_polls is not None else DEFAULT_MAX_POLLS,
            initial_retry_after=retry_after,
        )

    def list_paginated(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        scope: str | None = None,
        dedupe_by: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Auto-follow Fabric ``continuationUri``/``continuationToken`` or Power
        BI ``@odata.nextLink`` and yield items from each page's ``value`` array.

        Delegates to :func:`sigantry_core.client.pagination.paginate`. The
        underlying ``send`` call on each page carries the full retry + rate
        limit + correlated-logging pipeline, so pagination inherits all those
        guarantees automatically.

        Args:
            path: absolute URL or path relative to ``self._base_url``.
            params: initial-page query params (preserved across token-only
                pagination).
            scope: OAuth scope override (default: ``self._default_scope``).
            dedupe_by: item key used to drop duplicates across pages.
            max_pages: override the 1000-page safeguard (raises
                :class:`sigantry_core.client.errors.PaginationError` if
                exceeded).
        """
        # Lazy import to avoid a circular dependency between ``base`` and
        # ``pagination`` (pagination imports ``BaseRestClient`` only for type
        # checking).
        from sigantry_core.client.pagination import MAX_PAGES, paginate

        return paginate(
            self,
            "GET",
            path,
            params=params,
            scope=scope,
            dedupe_by=dedupe_by,
            max_pages=max_pages if max_pages is not None else MAX_PAGES,
        )

    @contextlib.contextmanager
    def correlation(self, correlation_id: str) -> Iterator[None]:
        """Scope a correlation id to a block of requests.

        The id flows into every ``send`` call's outbound
        ``x-client-correlation-id`` header and into the JSON log record's
        ``correlation_id`` field for the duration of the context.
        """
        token = set_correlation_id(correlation_id)
        try:
            yield
        finally:
            reset_correlation_id(token)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ---- internals ----------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self._base_url}{path if path.startswith('/') else '/' + path}"

    def _relative_path(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            from urllib.parse import urlparse

            return urlparse(path).path
        return path if path.startswith("/") else "/" + path

    def _build_headers(self, scope: str, extra: dict[str, str] | None) -> dict[str, str]:
        token = self._tp.get_token(scope)
        corr = get_correlation_id() or str(uuid.uuid4())
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "User-Agent": _USER_AGENT,
            "x-client-correlation-id": corr,
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _log_first_credential(self, scope: str) -> None:
        if scope in self._credential_logged_for:
            return
        cred = self._tp.last_credential_class(scope)
        if cred:
            logger.info(
                "client_credential_resolved",
                extra={"scope": scope, "credential": cred},
            )
            self._credential_logged_for.add(scope)

    def _log_request(
        self,
        method: str,
        url: str,
        resp: httpx.Response,
        elapsed_ms: float,
        retry_count: int,
    ) -> None:
        logger.info(
            "request_completed",
            extra={
                "method": method,
                "url": url,
                "status": resp.status_code,
                "elapsed_ms": round(elapsed_ms, 2),
                "retry_count": retry_count,
                "request_id": resp.headers.get("x-ms-request-id"),
                "operation_id": resp.headers.get("x-ms-operation-id") or get_operation_id(),
            },
        )
