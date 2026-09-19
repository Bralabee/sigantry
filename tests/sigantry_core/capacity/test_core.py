"""Unit tests for sigantry_core.capacity.core (Plan 03-02 Task 2, WKSP-04)."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from sigantry_core.capacity import Capacity, list_capacities


class TestCapacityDTO:
    def test_from_api_full(self) -> None:
        payload = {
            "id": "c1",
            "displayName": "cap-dev",
            "sku": {"name": "F2", "tier": "Fabric"},
            "region": "westeurope",
            "state": "Active",
        }
        cap = Capacity.from_api(payload)
        assert cap.id == "c1"
        assert cap.display_name == "cap-dev"
        assert cap.sku_name == "F2"
        assert cap.sku_tier == "Fabric"
        assert cap.region == "westeurope"
        assert cap.state == "Active"

    def test_from_api_missing_sku_tier(self) -> None:
        payload = {
            "id": "c1",
            "displayName": "cap",
            "sku": {"name": "F2"},
            "region": "eu",
            "state": "Active",
        }
        cap = Capacity.from_api(payload)
        assert cap.sku_name == "F2"
        assert cap.sku_tier is None

    def test_from_api_no_sku(self) -> None:
        payload = {
            "id": "c1",
            "displayName": "cap",
            "region": "eu",
            "state": "Active",
        }
        cap = Capacity.from_api(payload)
        assert cap.sku_name is None
        assert cap.sku_tier is None

    def test_from_api_sku_non_dict_tolerated(self) -> None:
        # Schema evolution safety - unexpected sku shape should not crash.
        payload = {
            "id": "c1",
            "displayName": "cap",
            "sku": "unexpected-string",
            "region": "eu",
            "state": "Active",
        }
        cap = Capacity.from_api(payload)
        assert cap.sku_name is None
        assert cap.sku_tier is None

    def test_from_api_optional_region_state(self) -> None:
        payload = {"id": "c1", "displayName": "cap"}
        cap = Capacity.from_api(payload)
        assert cap.region is None
        assert cap.state is None

    def test_capacity_is_frozen(self) -> None:
        cap = Capacity.from_api({"id": "c1", "displayName": "cap"})
        with pytest.raises(dataclasses.FrozenInstanceError):
            cap.id = "c2"  # type: ignore[misc]


class TestListCapacities:
    def test_list_capacities_uses_fabric_core_endpoint(self, mock_fabric_client: MagicMock) -> None:
        mock_fabric_client.list_paginated.return_value = iter(
            [
                {
                    "id": "c1",
                    "displayName": "cap-1",
                    "sku": {"name": "F2", "tier": "Fabric"},
                    "region": "eu",
                    "state": "Active",
                },
                {
                    "id": "c2",
                    "displayName": "cap-2",
                    "sku": {"name": "F4", "tier": "Fabric"},
                    "region": "eu",
                    "state": "Paused",
                },
            ]
        )
        caps = list(list_capacities(mock_fabric_client))
        assert len(caps) == 2
        assert caps[0].id == "c1"
        assert caps[0].state == "Active"
        assert caps[1].state == "Paused"
        mock_fabric_client.list_paginated.assert_called_once_with("/v1/capacities")

    def test_list_capacities_empty(self, mock_fabric_client: MagicMock) -> None:
        mock_fabric_client.list_paginated.return_value = iter([])
        caps = list(list_capacities(mock_fabric_client))
        assert caps == []
