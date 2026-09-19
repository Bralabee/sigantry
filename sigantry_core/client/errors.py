"""Typed exception hierarchy for sigantry_core.client.

RESEARCH.md §14.1. Two top-level branches:

- `HttpError` and its subclasses wrap non-retryable or retry-exhausted HTTP
  responses (4xx, and 5xx/429 after tenacity's max_attempts).
- `LROTimeoutError`, `OperationFailedError`, and `PaginationError` are
  protocol-level errors that are not tied to a single HTTP response.

All errors share `ClientError` as base so callers can catch the whole surface
with `except ClientError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ClientError(Exception):
    """Base class for all sigantry_core.client errors."""


@dataclass
class HttpError(ClientError):
    """Non-retryable HTTP response (4xx, or retried-then-exhausted 5xx/429)."""

    status_code: int
    body: dict[str, Any] | list[Any] | str | None = None
    request_id: str | None = None
    operation_id: str | None = None

    def __str__(self) -> str:
        return f"HTTP {self.status_code} request_id={self.request_id!r}"


class RateLimitError(HttpError):
    """Received 429 after client-side throttling already engaged."""


class AuthError(HttpError):
    """401/403 - stale token or tenant-setting gap. Not retried."""


class NotFoundError(HttpError):
    """404 - resource does not exist. Not retried."""


class ServerError(HttpError):
    """5xx after tenacity's max_attempts exhausted."""


@dataclass
class LROTimeoutError(ClientError):
    """Long-running operation did not reach a terminal state within timeout."""

    operation_id: str
    elapsed_seconds: float
    last_status: str


@dataclass
class OperationFailedError(ClientError):
    """Terminal Failed status on a long-running operation."""

    operation_id: str
    error_code: str
    message: str
    details: list[dict[str, Any]] | None = None


class PaginationError(ClientError):
    """Generator invariant violated (e.g. missing `value` key, runaway cursor)."""
