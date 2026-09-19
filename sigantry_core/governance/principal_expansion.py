"""MS Graph group transitive-member expansion (GOV-04 helper).

Uses the Phase 2 BaseRestClient pipeline pinned to
``https://graph.microsoft.com`` + GRAPH_SCOPE. Graph's
``/transitiveMembers`` endpoint flattens nested groups; we do not need
``$count`` / ``$filter`` / ``$search`` so no ``ConsistencyLevel: eventual``
header is required (RESEARCH Pitfall 6).

Graph permission required: ``GroupMember.Read.All`` (application or
delegated).

T-3-08 mitigation: this module yields raw member dicts back to
:mod:`sigantry_core.governance.rbac` for inclusion in the CLI CSV output.
It does NOT log the member payloads. The Phase 2 client pipeline only logs
the outbound request URL + status, never response bodies (see
``BaseRestClient._log_request``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sigantry_core.auth import GRAPH_AUDIENCE, GRAPH_SCOPE, get_token_provider
from sigantry_core.client.base import BaseRestClient


class _GraphClient(BaseRestClient):
    """Lightweight MS Graph client sharing the Phase 2 client pipeline.

    Pinned to ``GRAPH_AUDIENCE`` (``https://graph.microsoft.com``) and
    ``GRAPH_SCOPE`` so callers cannot accidentally point this client at a
    different audience.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", GRAPH_AUDIENCE)
        kwargs.setdefault("default_scope", GRAPH_SCOPE)
        super().__init__(**kwargs)

    @classmethod
    def from_defaults(
        cls,
        *,
        tenant_id: str | None = None,
        base_url: str = GRAPH_AUDIENCE,
        default_timeout: float = 30.0,
    ) -> _GraphClient:
        """Construct with the process-wide ``TokenProvider`` singleton."""
        return cls(
            token_provider=get_token_provider(tenant_id=tenant_id),
            base_url=base_url,
            default_scope=GRAPH_SCOPE,
            default_timeout=default_timeout,
        )


def expand_group_members(
    group_id: str,
    *,
    graph_client: _GraphClient | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield every transitive member of ``group_id`` as a raw dict.

    Backed by ``GET /v1.0/groups/{id}/transitiveMembers`` which already
    flattens nested groups. Member type filtering and annotation are the
    caller's responsibility (see :func:`sigantry_core.governance.rbac.audit`).

    Args:
        group_id: Entra group id (GUID).
        graph_client: Optional pre-built ``_GraphClient`` instance for DI in
            tests. Defaults to ``_GraphClient.from_defaults()``.

    Yields:
        Raw member dicts as returned by Graph (``id``, ``displayName``,
        ``userPrincipalName``, ``@odata.type``, ...).
    """
    client = graph_client or _GraphClient.from_defaults()
    yield from client.list_paginated(f"/v1.0/groups/{group_id}/transitiveMembers")
