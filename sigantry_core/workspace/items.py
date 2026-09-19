"""Workspace items - paginated list with optional type filter (WKSP-02).

Per RESEARCH §5.3 + Pitfall 3:
- /v1/workspaces/{id}/items (Core) - Item schema INCLUDES sensitivityLabel.
- /v1/admin/items (Admin) - different schema, no sensitivityLabel. We use Core.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.governance.audit import destructive_op


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    display_name: str
    type: str  # "Lakehouse" | "Notebook" | "Warehouse" | etc.
    workspace_id: str
    description: str | None
    sensitivity_label_id: str | None
    folder_id: str | None  # None = workspace root; set when item lives in a folder

    @classmethod
    def from_api(cls, payload: dict) -> Item:
        label = payload.get("sensitivityLabel")
        label_id = label.get("id") if isinstance(label, dict) else None
        return cls(
            id=payload["id"],
            display_name=payload["displayName"],
            type=payload["type"],
            workspace_id=payload["workspaceId"],
            description=payload.get("description"),
            sensitivity_label_id=label_id,
            folder_id=payload.get("folderId"),
        )


def list_items(
    client: FabricRestClient,
    workspace_id: str,
    *,
    item_type: str | None = None,
) -> Iterator[Item]:
    """GET /v1/workspaces/{id}/items?type=<item_type> - paginated."""
    params: dict | None = {"type": item_type} if item_type else None
    for payload in client.list_paginated(f"/v1/workspaces/{workspace_id}/items", params=params):
        yield Item.from_api(payload)


@destructive_op("item", "delete", resource_arg="item_id")
def delete_item(
    client: FabricRestClient,
    workspace_id: str,
    item_id: str,
    *,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
) -> None:
    """DELETE /v1/workspaces/{id}/items/{itemId} - 200 sync.

    Decorator-enforced: ``force=True`` MUST be passed at call site. Used by
    the reconciler's orphan-cleanup branch to unpublish workspace items that
    are absent from the repo tree.
    """
    client.send("DELETE", f"/v1/workspaces/{workspace_id}/items/{item_id}")
