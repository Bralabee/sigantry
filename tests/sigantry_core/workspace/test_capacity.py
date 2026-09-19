"""Unit tests for sigantry_core.workspace.capacity (WKSP-03)."""

from __future__ import annotations

from unittest.mock import MagicMock

from sigantry_core.workspace import assign_to_capacity


def test_assign_to_capacity_uses_send_lro(mock_fabric_client: MagicMock) -> None:
    assign_to_capacity(mock_fabric_client, "w1", "cap-1")
    mock_fabric_client.send_lro.assert_called_once()
    # Not synchronous
    mock_fabric_client.send.assert_not_called()


def test_assign_to_capacity_body(mock_fabric_client: MagicMock) -> None:
    assign_to_capacity(mock_fabric_client, "w1", "cap-1")
    call = mock_fabric_client.send_lro.call_args
    assert call.args == ("POST", "/v1/workspaces/w1/assignToCapacity")
    assert call.kwargs == {"json": {"capacityId": "cap-1"}}


def test_assign_to_capacity_returns_lro_result(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.send_lro.return_value = {"state": "Succeeded"}
    result = assign_to_capacity(mock_fabric_client, "w1", "cap-1")
    assert result == {"state": "Succeeded"}
