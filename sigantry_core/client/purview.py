"""PurviewRestClient - stub for the Purview Unified Catalog REST API.

**v2 deferred:** Purview integration (``PURVIEW-01..03``) is NOT part of v1 scope
(see ``.planning/REQUIREMENTS.md`` - PURVIEW-01/02/03 live under the "v2 (deferred
to v1.1+)" section). This module ships the typed subclass and scope wiring so
Phase 3+ domain modules can import it without touching the client package when
v2 lands.

Purview URLs are **account-scoped** (e.g. ``https://<account>.purview.azure.net``);
there is no single canonical default, so callers MUST pass ``base_url`` explicitly.
Passing an empty or whitespace-only value raises :class:`ValueError` with remediation
pointing at the account-scoped URL shape.

Usage::

    from sigantry_core.client import PurviewRestClient

    client = PurviewRestClient.from_defaults(
        base_url="https://contoso.purview.azure.net",
    )
"""

from __future__ import annotations

import httpx

from sigantry_core.auth import TokenProvider, get_token_provider
from sigantry_core.auth.audiences import PURVIEW_SCOPE
from sigantry_core.client.base import BaseRestClient


class PurviewRestClient(BaseRestClient):
    """Purview Unified Catalog REST client - v1 stub.

    Inherits retry, rate-limit, LRO, pagination, and correlated logging from
    :class:`BaseRestClient`. No additional public API; v2 (PURVIEW-01..03)
    will flesh out scan registration, lineage publishing, and label automation.
    """

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        base_url: str,
        default_scope: str = PURVIEW_SCOPE,
        default_timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError(
                "PurviewRestClient requires a base_url "
                "(e.g. 'https://<account>.purview.azure.net'). Purview URLs are "
                "account-scoped; there is no canonical default."
            )
        super().__init__(
            token_provider=token_provider,
            base_url=base_url,
            default_scope=default_scope,
            default_timeout=default_timeout,
            http_client=http_client,
        )

    @classmethod
    def from_defaults(
        cls,
        *,
        base_url: str,
        tenant_id: str | None = None,
        default_timeout: float = 30.0,
    ) -> PurviewRestClient:
        """Construct with the process-wide ``TokenProvider`` singleton.

        ``base_url`` is mandatory - Purview has no canonical default.

        Args:
            base_url: The Purview account URL
                (e.g. ``https://contoso.purview.azure.net``). **Required.**
            tenant_id: Optional tenant id forwarded to
                :func:`sigantry_core.auth.get_token_provider`.
            default_timeout: Per-request timeout in seconds (default 30s).
        """
        return cls(
            token_provider=get_token_provider(tenant_id=tenant_id),
            base_url=base_url,
            default_scope=PURVIEW_SCOPE,
            default_timeout=default_timeout,
        )
