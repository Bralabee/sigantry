"""Unit tests for sigantry_core.workspace.items (WKSP-02)."""

from __future__ import annotations

from unittest.mock import MagicMock

from sigantry_core.workspace import Item, list_items


def test_item_from_api_with_sensitivity_label() -> None:
    it = Item.from_api(
        {
            "id": "it1",
            "displayName": "lh1",
            "type": "Lakehouse",
            "workspaceId": "w1",
            "description": "",
            "sensitivityLabel": {"id": "lbl-1"},
        }
    )
    assert it.id == "it1"
    assert it.display_name == "lh1"
    assert it.type == "Lakehouse"
    assert it.workspace_id == "w1"
    assert it.sensitivity_label_id == "lbl-1"


def test_item_from_api_without_sensitivity_label() -> None:
    it = Item.from_api(
        {
            "id": "it1",
            "displayName": "lh1",
            "type": "Lakehouse",
            "workspaceId": "w1",
        }
    )
    assert it.sensitivity_label_id is None
    assert it.description is None


def test_list_items_no_filter(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.list_paginated.return_value = iter(
        [
            {
                "id": "it1",
                "displayName": "lh1",
                "type": "Lakehouse",
                "workspaceId": "w1",
            }
        ]
    )
    results = list(list_items(mock_fabric_client, "w1"))
    mock_fabric_client.list_paginated.assert_called_once_with(
        "/v1/workspaces/w1/items", params=None
    )
    assert len(results) == 1
    assert isinstance(results[0], Item)
    assert results[0].id == "it1"


def test_list_items_type_filter(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.list_paginated.return_value = iter([])
    list(list_items(mock_fabric_client, "w1", item_type="Lakehouse"))
    mock_fabric_client.list_paginated.assert_called_once_with(
        "/v1/workspaces/w1/items", params={"type": "Lakehouse"}
    )
