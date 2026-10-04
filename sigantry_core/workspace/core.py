"""Workspace CRUD against Fabric Core REST /v1/workspaces.

Per RESEARCH §5 + Pitfall 1:
- POST /v1/workspaces -> 201 synchronous (NOT LRO). Use client.send().
- GET /v1/workspaces/{id} -> 200 sync.
- PATCH /v1/workspaces/{id} -> 200 sync.
- DELETE /v1/workspaces/{id} -> 200 sync. (Gated by @destructive_op.)
- GET /v1/workspaces -> 200 paginated.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.client.errors import HttpError
from sigantry_core.governance.audit import destructive_op


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    display_name: str
    description: str | None
    type: str  # "Workspace" | "Personal" | "AdminWorkspace"
    capacity_id: str | None
    domain_id: str | None

    @classmethod
    def from_api(cls, payload: dict) -> Workspace:
        return cls(
            id=payload["id"],
            display_name=payload["displayName"],
            description=payload.get("description"),
            type=payload["type"],
            capacity_id=payload.get("capacityId"),
            domain_id=payload.get("domainId"),
        )


def create_workspace(
    client: FabricRestClient,
    *,
    display_name: str,
    capacity_id: str | None = None,
    description: str | None = None,
    domain_id: str | None = None,
) -> Workspace:
    """POST /v1/workspaces - 201 synchronous (NOT LRO)."""
    body: dict = {"displayName": display_name}
    if capacity_id is not None:
        body["capacityId"] = capacity_id
    if description is not None:
        body["description"] = description
    if domain_id is not None:
        body["domainId"] = domain_id
    resp = client.send("POST", "/v1/workspaces", json=body)
    payload = resp.json_body if isinstance(resp.json_body, dict) else {}
    return Workspace.from_api(payload)


def get_workspace(client: FabricRestClient, workspace_id: str) -> Workspace:
    """GET /v1/workspaces/{id} - 200 sync."""
    resp = client.send("GET", f"/v1/workspaces/{workspace_id}")
    payload = resp.json_body if isinstance(resp.json_body, dict) else {}
    return Workspace.from_api(payload)


def update_workspace(
    client: FabricRestClient,
    workspace_id: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
) -> Workspace:
    """PATCH /v1/workspaces/{id} - 200 sync. Empty body is valid."""
    body: dict = {}
    if display_name is not None:
        body["displayName"] = display_name
    if description is not None:
        body["description"] = description
    resp = client.send("PATCH", f"/v1/workspaces/{workspace_id}", json=body)
    payload = resp.json_body if isinstance(resp.json_body, dict) else {}
    return Workspace.from_api(payload)


@destructive_op("workspace", "delete", resource_arg="workspace_id")
def delete_workspace(
    client: FabricRestClient,
    workspace_id: str,
    *,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
    pbi_fallback: bool = False,
    tenant_id: str | None = None,
) -> None:
    """DELETE /v1/workspaces/{id} - 200 synchronous (NOT LRO).

    Decorator-enforced: ``force=True`` MUST be passed at call site.
    ``resource_id`` is read by the decorator for the audit record; when absent
    the workspace_id argument itself identifies the resource.

    With ``pbi_fallback=True``, when the Fabric DELETE raises an ``HttpError``
    whose body carries ``UnknownError`` (the status code is not checked), the
    delete is tried again once through
    ``DELETE https://api.powerbi.com/v1.0/myorg/groups/{id}``. That retry
    authenticates with the process default credential for ``tenant_id``, not
    with ``token_provider`` or the Fabric client's credential, and the audit
    record does not show that it ran. Default ``False`` preserves the strict
    single-API behaviour.
    """
    try:
        client.send("DELETE", f"/v1/workspaces/{workspace_id}")
    except HttpError as exc:
        if not pbi_fallback or not _is_unknown_error(exc):
            raise
        # Lazy import: PowerBIRestClient drags httpx + token-provider singleton
        # construction; only pay that on the rare fallback path.
        from sigantry_core.client.powerbi import PowerBIRestClient

        with PowerBIRestClient.from_defaults(tenant_id=tenant_id) as pbi:
            pbi.send("DELETE", f"/v1.0/myorg/groups/{workspace_id}")


def _is_unknown_error(exc: HttpError) -> bool:
    """Detect Fabric's ``UnknownError`` in an error body, whatever the status.

    Fabric returns ``{"errorCode": "UnknownError", "message": "..."}`` on the
    delete failure this fallback is for. Some error paths flatten the body to
    a plain string before it reaches us, so we accept either shape. The status
    code is not checked.
    """
    body = exc.body
    if isinstance(body, dict):
        # Fabric REST envelope: top-level "errorCode" or nested "error.code".
        if body.get("errorCode") == "UnknownError":
            return True
        nested = body.get("error")
        return isinstance(nested, dict) and nested.get("code") == "UnknownError"
    if isinstance(body, str):
        return "UnknownError" in body
    return False


def list_workspaces(client: FabricRestClient, *, roles: str | None = None) -> Iterator[Workspace]:
    """GET /v1/workspaces - paginated. ``roles`` filters by caller's role."""
    params: dict | None = {"roles": roles} if roles else None
    for payload in client.list_paginated("/v1/workspaces", params=params):
        yield Workspace.from_api(payload)
