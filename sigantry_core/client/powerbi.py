"""PowerBIRestClient - Power BI REST API wrapper over BaseRestClient.

Power BI REST paths live under ``/v1.0/myorg/...`` rooted at
``https://api.powerbi.com``. Pagination uses ``@odata.nextLink``, which the
unified :func:`sigantry_core.client.pagination.paginate` generator handles
automatically.

Usage::

    from sigantry_core.client import PowerBIRestClient

    client = PowerBIRestClient.from_defaults()
    groups = list(client.list_paginated("/v1.0/myorg/groups"))

For multi-tenant callers, pass ``tenant_id`` to ``from_defaults()``; it threads
through to :func:`sigantry_core.auth.get_token_provider`.
"""

from __future__ import annotations

from typing import Final

import httpx

from sigantry_core.auth import TokenProvider, get_token_provider
from sigantry_core.auth.audiences import POWERBI_SCOPE
from sigantry_core.client.base import BaseRestClient

POWERBI_DEFAULT_BASE_URL: Final[str] = "https://api.powerbi.com"
"""``https://api.powerbi.com`` - the commercial Power BI REST root."""


class PowerBIRestClient(BaseRestClient):
    """Power BI REST client (paths under ``/v1.0/myorg/...``).

    Inherits the full retry + rate-limit + LRO + pagination + correlated-logging
    pipeline from :class:`BaseRestClient`. The unified :func:`paginate` generator
    recognises ``@odata.nextLink`` automatically, so the Power BI API's pagination
    contract needs no special-casing at this layer.
    """

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        base_url: str = POWERBI_DEFAULT_BASE_URL,
        default_scope: str = POWERBI_SCOPE,
        default_timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
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
        tenant_id: str | None = None,
        base_url: str = POWERBI_DEFAULT_BASE_URL,
        default_timeout: float = 30.0,
    ) -> PowerBIRestClient:
        """Construct with the process-wide ``TokenProvider`` singleton.

        Args:
            tenant_id: Optional tenant id forwarded to
                :func:`sigantry_core.auth.get_token_provider`.
            base_url: Override the Power BI root (defaults to
                ``https://api.powerbi.com``). Useful for sovereign clouds.
            default_timeout: Per-request timeout in seconds (default 30s).
        """
        return cls(
            token_provider=get_token_provider(tenant_id=tenant_id),
            base_url=base_url,
            default_scope=POWERBI_SCOPE,
            default_timeout=default_timeout,
        )
