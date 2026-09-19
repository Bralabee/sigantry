"""Typer CLI tests for sigantry_core.workspace.cli."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sigantry_core.cli import app


def test_workspace_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["workspace", "--help"])
    assert result.exit_code == 0
    for cmd in ("list", "get", "create", "delete", "assign-capacity", "list-items"):
        assert cmd in result.stdout


def test_workspace_list_json(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.list_paginated.return_value = iter(
        [
            {
                "id": "w1",
                "displayName": "ws-dev",
                "type": "Workspace",
            }
        ]
    )
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "list", "--output", "json"])
    assert result.exit_code == 0, result.stdout
    assert '"id"' in result.stdout
    assert "w1" in result.stdout


def test_workspace_delete_without_force_exits_nonzero(
    mock_fabric_client: MagicMock,
) -> None:
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "delete", "w1"])
    assert result.exit_code != 0
    # the HTTP client.send must NOT have been called
    mock_fabric_client.send.assert_not_called()


def test_workspace_delete_with_force(mock_fabric_client: MagicMock) -> None:
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "delete", "w1", "--force"])
    assert result.exit_code == 0, result.stdout
    mock_fabric_client.send.assert_called_once_with("DELETE", "/v1/workspaces/w1")


def test_workspace_assign_capacity(mock_fabric_client: MagicMock) -> None:
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "assign-capacity", "w1", "cap-1"])
    assert result.exit_code == 0, result.stdout
    mock_fabric_client.send_lro.assert_called_once()


def test_workspace_list_table_default(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.list_paginated.return_value = iter(
        [
            {
                "id": "w1",
                "displayName": "ws-dev",
                "type": "Workspace",
                "capacityId": None,
                "domainId": None,
            }
        ]
    )
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "list"])
    assert result.exit_code == 0, result.stdout
    assert "Workspaces" in result.stdout
    assert "w1" in result.stdout


def test_workspace_get(mock_fabric_client: MagicMock) -> None:
    from sigantry_core.client import HttpResponse

    mock_fabric_client.send.return_value = HttpResponse(
        status_code=200,
        json_body={
            "id": "w1",
            "displayName": "ws-dev",
            "type": "Workspace",
            "capacityId": "cap-1",
            "domainId": None,
            "description": "desc",
        },
        headers={},
        request_id="req-test",
        operation_id=None,
        elapsed_ms=1.0,
    )
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "get", "w1"])
    assert result.exit_code == 0, result.stdout
    mock_fabric_client.send.assert_called_once_with("GET", "/v1/workspaces/w1")
    assert "w1" in result.stdout


def test_workspace_create(mock_fabric_client: MagicMock) -> None:
    from sigantry_core.client import HttpResponse

    mock_fabric_client.send.return_value = HttpResponse(
        status_code=201,
        json_body={"id": "w1", "displayName": "ws-dev", "type": "Workspace"},
        headers={},
        request_id="req-test",
        operation_id=None,
        elapsed_ms=1.0,
    )
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "create", "--name", "ws-dev"])
    assert result.exit_code == 0, result.stdout
    mock_fabric_client.send.assert_called_once_with(
        "POST", "/v1/workspaces", json={"displayName": "ws-dev"}
    )


def test_workspace_list_items_json(mock_fabric_client: MagicMock) -> None:
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
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(
            app,
            [
                "workspace",
                "list-items",
                "w1",
                "--type",
                "Lakehouse",
                "--output",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    mock_fabric_client.list_paginated.assert_called_once_with(
        "/v1/workspaces/w1/items", params={"type": "Lakehouse"}
    )
    assert "it1" in result.stdout


def test_workspace_list_items_table(mock_fabric_client: MagicMock) -> None:
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
    runner = CliRunner()
    with patch("sigantry_core.workspace.cli._client_factory", return_value=mock_fabric_client):
        result = runner.invoke(app, ["workspace", "list-items", "w1"])
    assert result.exit_code == 0, result.stdout
    assert "Items in w1" in result.stdout
