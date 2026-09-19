"""Unified pagination generator for Fabric and Power BI REST APIs.

Fabric REST emits a ``continuationToken`` plus an optional pre-formatted
``continuationUri`` on each page. The Microsoft Learn pagination article
(`learn.microsoft.com/en-us/rest/api/fabric/articles/pagination`) states that
when both are present callers SHOULD follow ``continuationUri`` verbatim,
because the server pre-applies any required query-string escaping.

Power BI REST uses the OData convention ``@odata.nextLink`` on the response
body. The link is an absolute URL that callers GET as-is.

This module unifies both shapes behind a single generator so Plan 02-03's
Fabric and Power BI subclasses share one implementation. Preference order
for the next page URL is:

1. ``continuationUri`` (Fabric, pre-formatted)
2. ``@odata.nextLink`` (Power BI)
3. ``continuationToken`` (Fabric, raw token - we append it as a query param
   on the ORIGINAL request URL)
4. terminal (no cursor keys at all)

Hard caps:

- ``MAX_PAGES`` (1000) - fail fast on runaway cursors so an upstream bug
  does not silently consume our retry/rate-limit budget. This is a
  correctness safeguard, not a hard user-facing limit; callers who expect
  more than 1000 pages should pass a higher ``max_pages`` with eyes open.

Spec references:
- learn.microsoft.com/en-us/rest/api/fabric/articles/pagination
- learn.microsoft.com/en-us/rest/api/power-bi/ (search "@odata.nextLink")
- .planning/phases/02-rest-api-client-layer/02-RESEARCH.md Pattern 4
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from sigantry_core.client.errors import PaginationError

if TYPE_CHECKING:
    from sigantry_core.client.base import BaseRestClient

logger = logging.getLogger("sigantry_core.client.pagination")

MAX_PAGES: int = 1000


def _extract_next(body: dict[str, Any]) -> str | None:
    """Return the next-page URL, preferring Fabric's continuationUri.

    Order of preference:
      1. ``continuationUri`` (Fabric, pre-formatted)
      2. ``@odata.nextLink`` (Power BI)
      3. ``None`` (terminal or continuationToken-only, handled by caller)
    """
    fabric_uri = body.get("continuationUri")
    if fabric_uri:
        return fabric_uri
    odata = body.get("@odata.nextLink")
    if odata:
        return odata
    return None


def paginate(
    client: BaseRestClient,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    scope: str | None = None,
    dedupe_by: str | None = None,
    max_pages: int = MAX_PAGES,
) -> Iterator[dict[str, Any]]:
    """Yield items from ``value[]`` of each page, auto-following cursors.

    Args:
        client: a ``BaseRestClient`` (retry + rate limit + logging already wired).
        method: HTTP verb for the initial call (usually ``"GET"``).
        url: path or absolute URL for page 1.
        params: query params for page 1. Subsequent pages reached via
            ``continuationUri`` or ``@odata.nextLink`` are called without
            re-applying these (the server pre-encoded the cursor URL).
            When falling back to ``continuationToken``, we copy ``params``
            and add the token.
        scope: OAuth scope override (default: ``client._default_scope``).
        dedupe_by: optional key in each yielded item; items whose key value
            was seen on a prior page are skipped.
        max_pages: hard cap (default 1000) - raise ``PaginationError`` beyond
            this. Protects against runaway cursors.

    Yields:
        One dict per item in the ``value`` array of each page.

    Raises:
        PaginationError: body is not a JSON object; ``value`` is present but
            not a list; cursor loop exceeds ``max_pages``.
    """
    seen: set[Any] = set()
    seen_tokens: set[str] = set()
    current_url = url
    current_params: dict[str, Any] | None = dict(params) if params else None
    current_method = method

    for page_idx in range(max_pages):
        resp = client.send(current_method, current_url, params=current_params, scope=scope)
        body = resp.json_body
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise PaginationError(
                f"Page {page_idx}: response body is not a JSON object (got {type(body).__name__})"
            )

        raw_items = body.get("value")
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise PaginationError(
                f"Page {page_idx}: 'value' is not a list (got {type(raw_items).__name__})"
            )

        for item in raw_items:
            if dedupe_by and isinstance(item, dict):
                key = item.get(dedupe_by)
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
            yield item

        next_url = _extract_next(body)
        fabric_token = body.get("continuationToken")

        if next_url:
            # Absolute cursor URL - follow as-is, drop original params.
            current_url = next_url
            current_params = None
            current_method = "GET"
            continue

        if fabric_token:
            # Defence in depth against a known Fabric API loop bug: if the
            # service hands us the same continuationToken twice in a row,
            # we'd otherwise spin until MAX_PAGES (~50K items) trips. Pattern
            # borrowed from usf_fabric_cli_cicd v1.8.4 (services/fabric_wrapper.py:1832).
            if fabric_token in seen_tokens:
                raise PaginationError(
                    f"Page {page_idx + 1}: same continuationToken returned "
                    f"twice for {url} (token prefix={fabric_token[:32]!r}) - "
                    "likely a Fabric API loop bug; refusing to spin."
                )
            seen_tokens.add(fabric_token)
            # Token without Uri - reissue original URL with the token as a
            # query param. Keep method and preserve the original params so
            # caller-specified filters survive pagination.
            current_params = dict(params or {})
            current_params["continuationToken"] = fabric_token
            current_method = method
            current_url = url
            continue

        # No cursor keys at all -> terminal page.
        return

    raise PaginationError(
        f"Exceeded MAX_PAGES={max_pages} while paginating {url}. "
        "Raise max_pages or add dedupe_by if duplicates are expected."
    )
