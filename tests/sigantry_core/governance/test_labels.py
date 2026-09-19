"""Unit tests for sigantry_core.governance.labels (GOV-01, GOV-02).

Covers:
- LabelInventoryEntry DTO + inventory_labels iteration
- Dispatcher routing (PowerBI artifacts vs Fabric-native vs unknown)
- SP-only path: Fabric-native items emit SP_NotSupported (Pitfall 4)
- Bulk chunking at 2000 items per request (Pitfall 9)
- PowerBI response parsing
- Pitfall 10: explicit per-item iteration, never the cascade endpoint
- SemanticModel routes via PowerBI admin (deterministic resolution)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core.client import FabricRestClient, HttpResponse, PowerBIRestClient
from sigantry_core.governance.labels import (
    LabelInventoryEntry,
    LabelSyncOutcome,
    apply_label_to_workspace_items,
    inventory_labels,
)


def _resp(status: int = 200, body: dict | None = None) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        json_body=body or {},
        headers={},
        request_id="req-test",
        operation_id=None,
        elapsed_ms=1.0,
    )


@pytest.fixture
def fabric_mock() -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.send.return_value = _resp()
    c.list_paginated.return_value = iter([])
    return c


@pytest.fixture
def powerbi_mock() -> MagicMock:
    c = MagicMock(spec=PowerBIRestClient)
    c.send.return_value = _resp()
    return c


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class TestInventory:
    def test_label_inventory_entry_dto(self) -> None:
        e = LabelInventoryEntry(item_id="it1", item_type="Lakehouse", label_id="lbl-1")
        assert e.item_id == "it1"
        assert e.item_type == "Lakehouse"
        assert e.label_id == "lbl-1"

    def test_inventory_labels_iterates(self, fabric_mock: MagicMock) -> None:
        fabric_mock.list_paginated.return_value = iter(
            [
                {"id": "it1", "type": "Lakehouse", "sensitivityLabel": {"id": "lbl-1"}},
                {"id": "it2", "type": "Notebook"},
            ]
        )
        entries = list(inventory_labels(fabric_mock, "ws-1"))
        assert entries == [
            LabelInventoryEntry(item_id="it1", item_type="Lakehouse", label_id="lbl-1"),
            LabelInventoryEntry(item_id="it2", item_type="Notebook", label_id=None),
        ]
        fabric_mock.list_paginated.assert_called_once_with("/v1/workspaces/ws-1/items")

    def test_inventory_labels_missing_sensitivity_label_yields_none(
        self, fabric_mock: MagicMock
    ) -> None:
        fabric_mock.list_paginated.return_value = iter(
            [
                # sensitivityLabel present but not a dict — should still yield None
                {"id": "it3", "type": "Warehouse", "sensitivityLabel": None},
            ]
        )
        entries = list(inventory_labels(fabric_mock, "ws-1"))
        assert entries == [LabelInventoryEntry(item_id="it3", item_type="Warehouse", label_id=None)]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_powerbi_artifacts_route_through_powerbi_admin(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        fabric_mock.list_paginated.return_value = iter(
            [
                {"id": "r1", "type": "Report"},
                {"id": "d1", "type": "Dashboard"},
                {"id": "ds1", "type": "SemanticModel"},
                {"id": "df1", "type": "Dataflow"},
            ]
        )
        powerbi_mock.send.return_value = _resp(
            200,
            {
                "reports": [{"id": "r1", "status": "Succeeded"}],
                "dashboards": [{"id": "d1", "status": "Succeeded"}],
                "datasets": [{"id": "ds1", "status": "Succeeded"}],
                "dataflows": [{"id": "df1", "status": "Succeeded"}],
            },
        )
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=True
            )
        )
        # All four artifacts succeeded; Fabric admin was NOT called
        assert len(outcomes) == 4
        assert all(o.status == "Succeeded" for o in outcomes)
        powerbi_mock.send.assert_called_once()
        fabric_mock.send.assert_not_called()

        # Verify the request body shape
        call = powerbi_mock.send.call_args
        assert call.args == (
            "POST",
            "/v1.0/myorg/admin/informationprotection/setLabels",
        )
        body = call.kwargs["json"]
        assert body["labelId"] == "lbl-1"
        assert body["assignmentMethod"] == "Standard"
        assert body["artifacts"]["reports"] == [{"id": "r1"}]
        assert body["artifacts"]["dashboards"] == [{"id": "d1"}]
        assert body["artifacts"]["datasets"] == [{"id": "ds1"}]
        assert body["artifacts"]["dataflows"] == [{"id": "df1"}]

    def test_fabric_native_under_user_builds_bulk_batch(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        fabric_mock.list_paginated.return_value = iter(
            [
                {"id": "lh1", "type": "Lakehouse"},
                {"id": "nb1", "type": "Notebook"},
            ]
        )
        fabric_mock.send.return_value = _resp(
            200,
            {
                "itemsChangeLabelStatus": [
                    {"id": "lh1", "type": "Lakehouse", "status": "Succeeded"},
                    {"id": "nb1", "type": "Notebook", "status": "Succeeded"},
                ]
            },
        )
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=False
            )
        )
        assert len(outcomes) == 2
        call = fabric_mock.send.call_args
        assert call.args == ("POST", "/v1/admin/items/bulkSetLabels")
        body = call.kwargs["json"]
        assert body["labelId"] == "lbl-1"
        assert body["assignmentMethod"] == "Standard"
        assert len(body["items"]) == 2
        assert {it["id"] for it in body["items"]} == {"lh1", "nb1"}
        # PowerBI not called when no PowerBI artifacts present
        powerbi_mock.send.assert_not_called()

    def test_sp_only_emits_sp_not_supported_for_fabric_native(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        # Pitfall 4: SP cannot call Fabric admin bulkSetLabels
        fabric_mock.list_paginated.return_value = iter(
            [
                {"id": "lh1", "type": "Lakehouse"},
                {"id": "nb1", "type": "Notebook"},
            ]
        )
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=True
            )
        )
        assert outcomes == [
            LabelSyncOutcome(item_id="lh1", item_type="Lakehouse", status="SP_NotSupported"),
            LabelSyncOutcome(item_id="nb1", item_type="Notebook", status="SP_NotSupported"),
        ]
        fabric_mock.send.assert_not_called()
        powerbi_mock.send.assert_not_called()

    def test_bulk_chunking_at_2000(self, fabric_mock: MagicMock, powerbi_mock: MagicMock) -> None:
        # Pitfall 9: bulkSetLabels caps at 2000 items per request.
        # 5000 Lakehouse items → 3 calls (2000, 2000, 1000).
        fabric_mock.list_paginated.return_value = iter(
            [{"id": f"lh{i}", "type": "Lakehouse"} for i in range(5000)]
        )
        fabric_mock.send.return_value = _resp(200, {"itemsChangeLabelStatus": []})
        list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=False
            )
        )
        calls = fabric_mock.send.call_args_list
        assert len(calls) == 3
        assert len(calls[0].kwargs["json"]["items"]) == 2000
        assert len(calls[1].kwargs["json"]["items"]) == 2000
        assert len(calls[2].kwargs["json"]["items"]) == 1000

    def test_powerbi_response_parsing(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        fabric_mock.list_paginated.return_value = iter(
            [
                {"id": "r1", "type": "Report"},
                {"id": "ds1", "type": "SemanticModel"},
            ]
        )
        powerbi_mock.send.return_value = _resp(
            200,
            {
                "reports": [{"id": "r1", "status": "Failed"}],
                "datasets": [{"id": "ds1", "status": "InsufficientUsageRights"}],
                # Informational extra container — should be ignored by the parser
                "extra-container": "ignored",
            },
        )
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=False
            )
        )
        assert len(outcomes) == 2
        by_id = {o.item_id: o for o in outcomes}
        assert by_id["r1"].status == "Failed"
        assert by_id["r1"].item_type == "Report"
        assert by_id["ds1"].status == "InsufficientUsageRights"
        assert by_id["ds1"].item_type == "SemanticModel"

    def test_unknown_type_yields_failed_not_dropped(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        fabric_mock.list_paginated.return_value = iter([{"id": "x1", "type": "ThingNotInAnyList"}])
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=False
            )
        )
        assert outcomes == [
            LabelSyncOutcome(item_id="x1", item_type="ThingNotInAnyList", status="Failed")
        ]
        fabric_mock.send.assert_not_called()
        powerbi_mock.send.assert_not_called()

    def test_explicit_iteration_no_call_when_workspace_empty(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        # Pitfall 10 regression: zero items → zero calls; we never invoke
        # the cascade endpoint expecting 80-item downstream propagation.
        fabric_mock.list_paginated.return_value = iter([])
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=False
            )
        )
        assert outcomes == []
        fabric_mock.send.assert_not_called()
        powerbi_mock.send.assert_not_called()
        # The workspace items endpoint was hit exactly once for enumeration;
        # no other endpoint touched.
        fabric_mock.list_paginated.assert_called_once_with("/v1/workspaces/ws-1/items")

    def test_semantic_model_routes_via_powerbi_admin(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        # Deterministic resolution: SemanticModel → PowerBI admin path
        # (cheaper + SP-supported), even though it appears in both lists in
        # the research snippet. Documented as Rule 1 auto-fix in SUMMARY.
        fabric_mock.list_paginated.return_value = iter([{"id": "ds1", "type": "SemanticModel"}])
        powerbi_mock.send.return_value = _resp(
            200, {"datasets": [{"id": "ds1", "status": "Succeeded"}]}
        )
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=True
            )
        )
        assert len(outcomes) == 1
        assert outcomes[0].item_id == "ds1"
        assert outcomes[0].status == "Succeeded"
        assert outcomes[0].item_type == "SemanticModel"
        fabric_mock.send.assert_not_called()
        powerbi_mock.send.assert_called_once()

    def test_mixed_workload_user_path(
        self, fabric_mock: MagicMock, powerbi_mock: MagicMock
    ) -> None:
        # Mixed workload + user path: Fabric bulk + PowerBI admin both fire.
        fabric_mock.list_paginated.return_value = iter(
            [
                {"id": "lh1", "type": "Lakehouse"},
                {"id": "r1", "type": "Report"},
            ]
        )
        fabric_mock.send.return_value = _resp(
            200,
            {"itemsChangeLabelStatus": [{"id": "lh1", "type": "Lakehouse", "status": "Succeeded"}]},
        )
        powerbi_mock.send.return_value = _resp(
            200, {"reports": [{"id": "r1", "status": "Succeeded"}]}
        )
        outcomes = list(
            apply_label_to_workspace_items(
                fabric_mock, powerbi_mock, "ws-1", "lbl-1", running_as_sp=False
            )
        )
        assert {o.item_id for o in outcomes} == {"lh1", "r1"}
        fabric_mock.send.assert_called_once()
        powerbi_mock.send.assert_called_once()
