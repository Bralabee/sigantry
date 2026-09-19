"""Unit tests for fabric-dataops capacity subcommand (Plan 03-02 Task 2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sigantry_core.cli import app


class TestHelp:
    def test_capacity_help_lists_subcommands(self) -> None:
        runner = CliRunner()
        r = runner.invoke(app, ["capacity", "--help"])
        assert r.exit_code == 0
        for cmd in ("list", "pause", "resume"):
            assert cmd in r.stdout

    def test_workspace_subapp_still_registered(self) -> None:
        # Regression: registering capacity must not break the Plan 03-01
        # workspace subapp.
        runner = CliRunner()
        r = runner.invoke(app, ["workspace", "--help"])
        assert r.exit_code == 0


class TestPause:
    def test_pause_without_force_fails(self, mock_arm_client: MagicMock) -> None:
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._arm_client_factory",
            return_value=mock_arm_client,
        ):
            r = runner.invoke(app, ["capacity", "pause", "sub-1", "rg-1", "cap-1"])
        assert r.exit_code != 0
        mock_arm_client.send_arm_lro.assert_not_called()

    def test_pause_without_runbook_fails(self, mock_arm_client: MagicMock) -> None:
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._arm_client_factory",
            return_value=mock_arm_client,
        ):
            r = runner.invoke(app, ["capacity", "pause", "sub-1", "rg-1", "cap-1", "--force"])
        assert r.exit_code != 0
        mock_arm_client.send_arm_lro.assert_not_called()

    def test_pause_with_force_and_runbook_succeeds(self, mock_arm_client: MagicMock) -> None:
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._arm_client_factory",
            return_value=mock_arm_client,
        ):
            r = runner.invoke(
                app,
                [
                    "capacity",
                    "pause",
                    "sub-1",
                    "rg-1",
                    "cap-1",
                    "--force",
                    "--runbook-id",
                    "INC-1234",
                ],
            )
        assert r.exit_code == 0, r.stdout
        mock_arm_client.send_arm_lro.assert_called_once_with(
            "POST",
            "/subscriptions/sub-1/resourceGroups/rg-1"
            "/providers/Microsoft.Fabric/capacities/cap-1/suspend",
        )


class TestResume:
    def test_resume_without_force_fails(self, mock_arm_client: MagicMock) -> None:
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._arm_client_factory",
            return_value=mock_arm_client,
        ):
            r = runner.invoke(app, ["capacity", "resume", "sub-1", "rg-1", "cap-1"])
        assert r.exit_code != 0
        mock_arm_client.send_arm_lro.assert_not_called()

    def test_resume_with_force_and_runbook_succeeds(self, mock_arm_client: MagicMock) -> None:
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._arm_client_factory",
            return_value=mock_arm_client,
        ):
            r = runner.invoke(
                app,
                [
                    "capacity",
                    "resume",
                    "sub-1",
                    "rg-1",
                    "cap-1",
                    "--force",
                    "--runbook-id",
                    "INC-2000",
                ],
            )
        assert r.exit_code == 0, r.stdout
        mock_arm_client.send_arm_lro.assert_called_once_with(
            "POST",
            "/subscriptions/sub-1/resourceGroups/rg-1"
            "/providers/Microsoft.Fabric/capacities/cap-1/resume",
        )


class TestList:
    def test_list_table_output(self, mock_fabric_client: MagicMock) -> None:
        mock_fabric_client.list_paginated.return_value = iter(
            [
                {
                    "id": "c1",
                    "displayName": "cap-1",
                    "sku": {"name": "F2", "tier": "Fabric"},
                    "region": "eu",
                    "state": "Active",
                },
            ]
        )
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._fabric_client_factory",
            return_value=mock_fabric_client,
        ):
            r = runner.invoke(app, ["capacity", "list"])
        assert r.exit_code == 0, r.stdout
        assert "cap-1" in r.stdout

    def test_list_json_output(self, mock_fabric_client: MagicMock) -> None:
        mock_fabric_client.list_paginated.return_value = iter(
            [
                {
                    "id": "c1",
                    "displayName": "cap-1",
                    "sku": {"name": "F2"},
                    "region": "eu",
                    "state": "Active",
                },
            ]
        )
        runner = CliRunner()
        with patch(
            "sigantry_core.capacity.cli._fabric_client_factory",
            return_value=mock_fabric_client,
        ):
            r = runner.invoke(app, ["capacity", "list", "--output", "json"])
        assert r.exit_code == 0, r.stdout
        assert '"id"' in r.stdout
        assert "cap-1" in r.stdout
