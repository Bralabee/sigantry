"""FabricRestClient - Fabric Core REST API wrapper over BaseRestClient.

Fabric Core paths live under ``/v1/...`` rooted at ``https://api.fabric.microsoft.com``.
This subclass pins the base URL and the Fabric OAuth scope so domain modules
(Phase 3+) can instantiate a client without restating either.

Usage - direct DI::

    from sigantry_core.auth import get_token_provider
    from sigantry_core.client import FabricRestClient

    client = FabricRestClient(token_provider=get_token_provider())
    workspaces = list(client.list_paginated("/v1/workspaces"))

Usage - process-wide singleton (preferred in domain code)::

    client = FabricRestClient.from_defaults()
    workspaces = list(client.list_paginated("/v1/workspaces"))

For multi-tenant callers, pass ``tenant_id`` to ``from_defaults()`` - it threads
through to ``get_token_provider(tenant_id=...)`` which pins the credential chain
so ``AzureCliCredential`` cannot silently succeed with the engineer's personal
subscription (Phase 1 Pitfall P1-6).
"""

from __future__ import annotations

from typing import Final

import httpx

from sigantry_core.auth import TokenProvider, get_token_provider
from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.client.base import BaseRestClient

FABRIC_DEFAULT_BASE_URL: Final[str] = FABRIC_AUDIENCE
"""``https://api.fabric.microsoft.com`` - the commercial Fabric Core root."""


class FabricRestClient(BaseRestClient):
    """Fabric Core REST client (paths under ``/v1/...``).

    Inherits the full retry + rate-limit + LRO + pagination + correlated-logging
    pipeline from :class:`BaseRestClient`. No Fabric-specific overrides are
    needed at the client layer; domain semantics live in Phase 3+ modules.
    """

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        base_url: str = FABRIC_DEFAULT_BASE_URL,
        default_scope: str = FABRIC_SCOPE,
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
        base_url: str = FABRIC_DEFAULT_BASE_URL,
        default_timeout: float = 30.0,
    ) -> FabricRestClient:
        """Construct with the process-wide ``TokenProvider`` singleton.

        Mirrors the Phase 1 ``from_defaults()`` ergonomic pattern used across
        ``sigantry_core.auth``. The scope is always :data:`FABRIC_SCOPE`.

        Args:
            tenant_id: Optional tenant id forwarded to
                :func:`sigantry_core.auth.get_token_provider` to pin the
                credential chain on that tenant (Pitfall P1-6).
            base_url: Override the Fabric Core root (defaults to
                ``https://api.fabric.microsoft.com``). Useful for sovereign
                clouds.
            default_timeout: Per-request timeout in seconds (default 30s).
        """
        return cls(
            token_provider=get_token_provider(tenant_id=tenant_id),
            base_url=base_url,
            default_scope=FABRIC_SCOPE,
            default_timeout=default_timeout,
        )
