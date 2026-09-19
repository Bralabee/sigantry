"""Value types returned by ``sigantry_core.client``.

``HttpResponse`` is a frozen dataclass that captures the final response after
the retry loop completes. It intentionally carries only what callers need:
status code, parsed JSON body (if any), response headers, request id,
operation id (for LROs) and elapsed_ms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Frozen snapshot of the final HTTP response."""

    status_code: int
    json_body: dict[str, Any] | list[Any] | None
    headers: dict[str, str]
    request_id: str | None
    operation_id: str | None
    elapsed_ms: float

    @classmethod
    def from_httpx(cls, resp: Any, *, elapsed_ms: float) -> HttpResponse:
        """Build an ``HttpResponse`` from an ``httpx.Response`` object.

        JSON parsing is best-effort: a non-JSON body or malformed payload
        results in ``json_body=None`` rather than an exception.
        """
        body: dict[str, Any] | list[Any] | None = None
        if resp.content:
            ct = resp.headers.get("content-type", "")
            if "application/json" in ct:
                try:
                    body = resp.json()
                except Exception:
                    body = None
        return cls(
            status_code=resp.status_code,
            json_body=body,
            headers={k: v for k, v in resp.headers.items()},
            request_id=resp.headers.get("x-ms-request-id") or resp.headers.get("request-id"),
            operation_id=resp.headers.get("x-ms-operation-id"),
            elapsed_ms=elapsed_ms,
        )
