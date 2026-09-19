"""Sensitivity label inventory + dispatch sync (GOV-01 + GOV-02).

Pattern 4 — dispatch by item type:

- Fabric-native types → Fabric admin ``bulkSetLabels`` (user identity only).
- PowerBI artifact types → Power BI admin ``informationprotection/setLabels``
  (Service Principal compatible).

Pitfall 4: Service Principals cannot call Fabric admin ``bulkSetLabels``.
When ``running_as_sp=True``, Fabric-native items emit
``LabelSyncOutcome(status="SP_NotSupported")`` rather than silently
failing. The CLI surfaces this row so operators see the gap explicitly.

Pitfall 9: Fabric admin ``bulkSetLabels`` is rate-limited to 25 req/hr
AND 2000 items per request. We chunk batches at exactly 2000 to minimise
request count.

Pitfall 10: the 80-item downstream-inheritance cascade is a DIFFERENT
feature from ``bulkSetLabels``. ``apply_label_to_workspace_items``
iterates every item explicitly and never relies on the cascade —
verifiable via ``test_explicit_iteration_no_call_when_workspace_empty``.

Deviation from RESEARCH §14.6: the research snippet placed
``"SemanticModel"`` in BOTH ``_POWERBI_ARTIFACT_TYPES`` and
``_FABRIC_NATIVE_TYPES``. We resolve the ambiguity deterministically — the
PowerBI check runs first in the dispatcher, so SemanticModel routes to
the SP-compatible PowerBI admin path. Documented in SUMMARY as Rule 1
auto-fix.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sigantry_core.client import FabricRestClient, PowerBIRestClient

# PowerBI admin setLabels container keys → our item_type values.
# The Power BI admin endpoint accepts these four artifact families only.
_POWERBI_ARTIFACT_TYPES: dict[str, str] = {
    "Dashboard": "dashboards",
    "Report": "reports",
    "SemanticModel": "datasets",
    "Dataflow": "dataflows",
}

# Fabric-native item types (user-only path via Fabric admin bulkSetLabels).
# SemanticModel intentionally NOT in this set — see module docstring.
_FABRIC_NATIVE_TYPES: frozenset[str] = frozenset(
    {
        "Lakehouse",
        "Notebook",
        "Warehouse",
        "DataPipeline",
        "Environment",
        "KQLDatabase",
        "KQLQueryset",
        "KQLDashboard",
        "MLExperiment",
        "MLModel",
        "MirroredDatabase",
        "SparkJobDefinition",
        "Eventhouse",
        "Eventstream",
        "SQLEndpoint",
        "SQLDatabase",
        "VariableLibrary",
        "Reflex",
        "MirroredWarehouse",
        "GraphQLApi",
        "CopyJob",
    }
)

_BULK_CHUNK_SIZE: int = 2000


@dataclass(frozen=True, slots=True)
class LabelInventoryEntry:
    """One row of the workspace's label inventory.

    ``label_id`` is ``None`` when the item carries no sensitivity label or
    the API response omits the ``sensitivityLabel`` key.
    """

    item_id: str
    item_type: str
    label_id: str | None


@dataclass(frozen=True, slots=True)
class LabelSyncOutcome:
    """One row of the label-sync outcome.

    ``status`` mirrors the upstream Fabric / PowerBI admin status strings
    plus the local ``"SP_NotSupported"`` value emitted when a Service
    Principal would have hit the unsupported Fabric-admin path.
    """

    item_id: str
    item_type: str
    status: str
    # "Succeeded" | "Failed" | "NotFound" | "InsufficientUsageRights"
    # | "FailedToGetUsageRights" | "SP_NotSupported"


def inventory_labels(
    fabric: FabricRestClient,
    workspace_id: str,
) -> Iterator[LabelInventoryEntry]:
    """List every item in ``workspace_id`` plus its sensitivity label id.

    GOV-01. Iterates ``GET /v1/workspaces/{id}/items`` (Fabric Core, which
    INCLUDES the ``sensitivityLabel`` key — see RESEARCH §5.3 / Pitfall 3).
    """
    for item in fabric.list_paginated(f"/v1/workspaces/{workspace_id}/items"):
        lbl = item.get("sensitivityLabel")
        label_id = lbl.get("id") if isinstance(lbl, dict) else None
        yield LabelInventoryEntry(
            item_id=item["id"],
            item_type=item["type"],
            label_id=label_id,
        )


def apply_label_to_workspace_items(
    fabric: FabricRestClient,
    powerbi: PowerBIRestClient,
    workspace_id: str,
    label_id: str,
    *,
    running_as_sp: bool,
) -> Iterator[LabelSyncOutcome]:
    """Iterate every item and dispatch label application by type (GOV-02).

    Dispatch table:

    - PowerBI artifact type (Dashboard / Report / SemanticModel / Dataflow)
      → batched into a single Power BI admin
      ``/v1.0/myorg/admin/informationprotection/setLabels`` call.
    - Fabric-native type + ``running_as_sp=True`` → emit
      ``LabelSyncOutcome(status="SP_NotSupported")`` immediately;
      no Fabric admin call fires for those items.
    - Fabric-native type + ``running_as_sp=False`` → batched into
      ``/v1/admin/items/bulkSetLabels`` calls of up to 2000 items each.
    - Unknown type → emit ``LabelSyncOutcome(status="Failed")`` so the
      gap is visible (never silently dropped).

    Per-item iteration is mandatory: we do NOT rely on the 80-item
    downstream-inheritance cascade (Pitfall 10). Verifiable in
    ``tests/.../test_labels.py::test_explicit_iteration_no_call_when_workspace_empty``.
    """
    fabric_batch: list[dict[str, Any]] = []
    powerbi_batches: dict[str, list[dict[str, Any]]] = {
        container: [] for container in _POWERBI_ARTIFACT_TYPES.values()
    }
    unknown_items: list[LabelSyncOutcome] = []

    for item in fabric.list_paginated(f"/v1/workspaces/{workspace_id}/items"):
        iid = item["id"]
        t = item["type"]
        if t in _POWERBI_ARTIFACT_TYPES:
            powerbi_batches[_POWERBI_ARTIFACT_TYPES[t]].append({"id": iid})
        elif t in _FABRIC_NATIVE_TYPES:
            if running_as_sp:
                yield LabelSyncOutcome(item_id=iid, item_type=t, status="SP_NotSupported")
                continue
            fabric_batch.append({"id": iid, "type": t})
        else:
            unknown_items.append(LabelSyncOutcome(item_id=iid, item_type=t, status="Failed"))

    # ---- Fabric admin bulkSetLabels — chunked at 2000 ---------------
    for chunk in _chunks(fabric_batch, _BULK_CHUNK_SIZE):
        resp = fabric.send(
            "POST",
            "/v1/admin/items/bulkSetLabels",
            json={
                "items": chunk,
                "labelId": label_id,
                "assignmentMethod": "Standard",
            },
        )
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        for r in body.get("itemsChangeLabelStatus", []):
            yield LabelSyncOutcome(
                item_id=r["id"],
                item_type=r.get("type", "Unknown"),
                status=r["status"],
            )

    # ---- PowerBI admin setLabels — single combined call -------------
    if any(powerbi_batches.values()):
        resp = powerbi.send(
            "POST",
            "/v1.0/myorg/admin/informationprotection/setLabels",
            json={
                "artifacts": powerbi_batches,
                "labelId": label_id,
                "assignmentMethod": "Standard",
            },
        )
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        for container, rows in body.items():
            # Only the four documented containers map back to a known type;
            # any other key is informational and ignored.
            if container not in _POWERBI_ARTIFACT_TYPES.values():
                continue
            if not isinstance(rows, list):
                continue
            for r in rows:
                yield LabelSyncOutcome(
                    item_id=r["id"],
                    item_type=_container_to_type(container),
                    status=r["status"],
                )

    # Surface unknown-type items last so their presence is unmistakable in
    # both JSON arrays and CSV files.
    yield from unknown_items


def _chunks(xs: list[Any], n: int) -> Iterator[list[Any]]:
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def _container_to_type(container: str) -> str:
    """Reverse-map a PowerBI admin container key to our item_type vocab."""
    reverse = {v: k for k, v in _POWERBI_ARTIFACT_TYPES.items()}
    return reverse.get(container, "Unknown")
