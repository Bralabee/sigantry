"""Workspace -> Capacity assignment (WKSP-03).

Per RESEARCH §5.4 + Pitfall 1:
- POST /v1/workspaces/{id}/assignToCapacity -> 202 LRO. Use send_lro.
"""

from __future__ import annotations

from typing import Any

from sigantry_core.client import FabricRestClient


def assign_to_capacity(client: FabricRestClient, workspace_id: str, capacity_id: str) -> Any:
    """POST /v1/workspaces/{id}/assignToCapacity - 202 LRO."""
    return client.send_lro(
        "POST",
        f"/v1/workspaces/{workspace_id}/assignToCapacity",
        json={"capacityId": capacity_id},
    )
