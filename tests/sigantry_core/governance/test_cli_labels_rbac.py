"""Typer CLI tests for label-sync + rbac-audit subcommands (Plan 03-03)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.client import (
    FabricRestClient,
    HttpResponse,
    PowerBIRestClient,
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
def fabric_client() -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.send.return_value = _resp()
    c.list_paginated.return_value = iter([])
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=None)
    return c


@pytest.fixture
def powerbi_client() -> MagicMock:
    c = MagicMock(spec=PowerBIRestClient)
    c.send.return_value = _resp()
    c.list_paginated.return_value = iter([])
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=None)
    return c


# ---------------------------------------------------------------------------
# Help / wiring
# ---------------------------------------------------------------------------


def test_label_sync_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["label-sync", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "label" in result.stdout.lower()


def test_rbac_audit_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["rbac-audit", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "rbac" in result.stdout.lower() or "audit" in result.stdout.lower()


def test_root_app_registers_all_four_subapps() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.stdout
    for name in ("workspace", "capacity", "label-sync", "rbac-audit"):
        assert name in result.stdout, f"missing subapp {name}: {result.stdout}"


# ---------------------------------------------------------------------------
# label-sync output modes + dispatch behaviour
# ---------------------------------------------------------------------------


def test_label_sync_json_output(fabric_client: MagicMock, powerbi_client: MagicMock) -> None:
    fabric_client.list_paginated.return_value = iter([{"id": "lh1", "type": "Lakehouse"}])
    fabric_client.send.return_value = _resp(
        200,
        {"itemsChangeLabelStatus": [{"id": "lh1", "type": "Lakehouse", "status": "Succeeded"}]},
    )
    runner = CliRunner()
    with (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch("sigantry_core.governance.cli._detect_sp", return_value=False),
    ):
        result = runner.invoke(
            app,
            ["label-sync", "--workspace-id", "ws-1", "--label-id", "lbl-1", "--output", "json"],
        )
    assert result.exit_code == 0, result.stdout
    assert "lh1" in result.stdout
    assert "Succeeded" in result.stdout


def test_label_sync_csv_output(fabric_client: MagicMock, powerbi_client: MagicMock) -> None:
    fabric_client.list_paginated.return_value = iter([{"id": "lh1", "type": "Lakehouse"}])
    fabric_client.send.return_value = _resp(
        200,
        {"itemsChangeLabelStatus": [{"id": "lh1", "type": "Lakehouse", "status": "Succeeded"}]},
    )
    runner = CliRunner()
    with (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch("sigantry_core.governance.cli._detect_sp", return_value=False),
    ):
        result = runner.invoke(
            app,
            ["label-sync", "--workspace-id", "ws-1", "--label-id", "lbl-1", "--output", "csv"],
        )
    assert result.exit_code == 0, result.stdout
    assert "itemId,itemType,status" in result.stdout
    assert "lh1,Lakehouse,Succeeded" in result.stdout


def test_label_sync_sp_flag_forces_sp_dispatch(
    fabric_client: MagicMock, powerbi_client: MagicMock
) -> None:
    """Explicit --sp must override detection and route Fabric-native to
    SP_NotSupported (no bulkSetLabels call)."""
    fabric_client.list_paginated.return_value = iter([{"id": "lh1", "type": "Lakehouse"}])
    runner = CliRunner()
    with (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch(
            # Detection would otherwise return False — confirm --sp wins
            "sigantry_core.governance.cli._detect_sp",
            return_value=False,
        ),
    ):
        result = runner.invoke(
            app,
            [
                "label-sync",
                "--workspace-id",
                "ws-1",
                "--label-id",
                "lbl-1",
                "--sp",
                "--output",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert "SP_NotSupported" in result.stdout
    fabric_client.send.assert_not_called()


def test_label_sync_user_flag_forces_user_dispatch(
    fabric_client: MagicMock, powerbi_client: MagicMock
) -> None:
    """Explicit --user overrides detection (would have flagged SP) and
    issues the Fabric admin bulkSetLabels call."""
    fabric_client.list_paginated.return_value = iter([{"id": "lh1", "type": "Lakehouse"}])
    fabric_client.send.return_value = _resp(
        200,
        {"itemsChangeLabelStatus": [{"id": "lh1", "type": "Lakehouse", "status": "Succeeded"}]},
    )
    runner = CliRunner()
    with (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch("sigantry_core.governance.cli._detect_sp", return_value=True),
    ):
        result = runner.invoke(
            app,
            [
                "label-sync",
                "--workspace-id",
                "ws-1",
                "--label-id",
                "lbl-1",
                "--user",
                "--output",
                "json",
            ],
        )
    assert result.exit_code == 0, result.stdout
    assert "Succeeded" in result.stdout
    fabric_client.send.assert_called_once()


# ---------------------------------------------------------------------------
# rbac-audit output modes
# ---------------------------------------------------------------------------


def test_rbac_audit_csv_output(fabric_client: MagicMock, powerbi_client: MagicMock) -> None:
    fabric_client.list_paginated.side_effect = [
        iter([{"id": "ws-1", "displayName": "ws-dev"}]),  # /v1/workspaces
        iter(
            [
                {
                    "principal": {"id": "p-1", "type": "User", "displayName": "Alice"},
                    "role": "Admin",
                }
            ]
        ),  # /v1/workspaces/ws-1/roleAssignments
        iter([]),  # /v1/capacities
    ]

    runner = CliRunner()
    # Ensure the audit doesn't actually try to call MS Graph
    with (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch(
            "sigantry_core.governance.rbac._GraphClient.from_defaults",
            return_value=MagicMock(),
        ),
    ):
        result = runner.invoke(app, ["rbac-audit", "--output", "csv"])
    assert result.exit_code == 0, result.stdout
    assert "layer,resource_id" in result.stdout
    assert "workspace,ws-1,ws-dev,p-1,User,Alice,Admin,ok" in result.stdout
    # item-layer placeholder per workspace
    assert "not-accessible-via-rest" in result.stdout


def test_rbac_audit_json_output(fabric_client: MagicMock, powerbi_client: MagicMock) -> None:
    fabric_client.list_paginated.side_effect = [
        iter([{"id": "ws-1", "displayName": "ws-dev"}]),
        iter([]),
        iter([]),
    ]
    runner = CliRunner()
    with (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch(
            "sigantry_core.governance.rbac._GraphClient.from_defaults",
            return_value=MagicMock(),
        ),
    ):
        result = runner.invoke(app, ["rbac-audit", "--output", "json"])
    assert result.exit_code == 0, result.stdout
    assert '"layer"' in result.stdout
    assert "not-accessible-via-rest" in result.stdout


# ---------------------------------------------------------------------------
# rbac-audit scoping + file output (GOV-04 enhancement, 2026-06-11)
# ---------------------------------------------------------------------------


def _patched_rbac_clients(fabric_client: MagicMock, powerbi_client: MagicMock):
    return (
        patch(
            "sigantry_core.governance.cli.FabricRestClient.from_defaults",
            return_value=fabric_client,
        ),
        patch(
            "sigantry_core.governance.cli.PowerBIRestClient.from_defaults",
            return_value=powerbi_client,
        ),
        patch(
            "sigantry_core.governance.rbac._GraphClient.from_defaults",
            return_value=MagicMock(),
        ),
    )


def test_rbac_audit_workspace_id_scopes_the_sweep(
    fabric_client: MagicMock, powerbi_client: MagicMock
) -> None:
    fabric_client.send.return_value = _resp(200, {"id": "ws-1", "displayName": "ws-dev"})

    def _route(path: str, *args, **kwargs):
        if path == "/v1/workspaces/ws-1/roleAssignments":
            return iter(
                [
                    {
                        "principal": {"id": "p-1", "type": "User", "displayName": "Alice"},
                        "role": "Admin",
                    }
                ]
            )
        return iter([])

    fabric_client.list_paginated.side_effect = _route

    runner = CliRunner()
    p1, p2, p3 = _patched_rbac_clients(fabric_client, powerbi_client)
    with p1, p2, p3:
        result = runner.invoke(app, ["rbac-audit", "--workspace-id", "ws-1"])
    assert result.exit_code == 0, result.output
    assert "workspace,ws-1,ws-dev,p-1,User,Alice,Admin,ok" in result.stdout
    listed = [c.args[0] for c in fabric_client.list_paginated.call_args_list]
    assert "/v1/workspaces" not in listed  # no tenant-wide sweep


def test_rbac_audit_out_writes_file_and_summarises(
    fabric_client: MagicMock, powerbi_client: MagicMock, tmp_path
) -> None:
    fabric_client.list_paginated.side_effect = [
        iter([{"id": "ws-1", "displayName": "ws-dev"}]),  # /v1/workspaces
        iter([]),  # roleAssignments
        iter([]),  # /v1/capacities
    ]
    target = tmp_path / "audits" / "rbac.csv"
    runner = CliRunner()
    p1, p2, p3 = _patched_rbac_clients(fabric_client, powerbi_client)
    with p1, p2, p3:
        result = runner.invoke(app, ["rbac-audit", "--out", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("layer,resource_id")
    assert "not-accessible-via-rest" in content
    # console carries the one-line summary, not the dump
    assert "wrote" in result.stdout
    assert "rows=1" in result.stdout
    assert "tenant-wide" in result.stdout
    assert "not-accessible-via-rest" not in result.stdout


def test_rbac_audit_out_dir_creates_dated_file(
    fabric_client: MagicMock, powerbi_client: MagicMock, tmp_path
) -> None:
    import re

    fabric_client.list_paginated.side_effect = [
        iter([{"id": "ws-1", "displayName": "ws-dev"}]),
        iter([]),
        iter([]),
    ]
    runner = CliRunner()
    p1, p2, p3 = _patched_rbac_clients(fabric_client, powerbi_client)
    with p1, p2, p3:
        result = runner.invoke(app, ["rbac-audit", "--out-dir", str(tmp_path / "filed")])
    assert result.exit_code == 0, result.output
    files = list((tmp_path / "filed").iterdir())
    assert len(files) == 1
    assert re.fullmatch(r"rbac-audit-\d{8}T\d{6}Z\.csv", files[0].name), files[0].name


def test_rbac_audit_out_and_out_dir_mutually_exclusive(
    fabric_client: MagicMock, powerbi_client: MagicMock, tmp_path
) -> None:
    runner = CliRunner()
    p1, p2, p3 = _patched_rbac_clients(fabric_client, powerbi_client)
    with p1, p2, p3:
        result = runner.invoke(
            app,
            ["rbac-audit", "--out", str(tmp_path / "a.csv"), "--out-dir", str(tmp_path)],
        )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_rbac_audit_unknown_workspace_fails_loudly_without_partial_file(
    fabric_client: MagicMock, powerbi_client: MagicMock, tmp_path
) -> None:
    from sigantry_core.client import HttpError

    fabric_client.send.side_effect = HttpError(
        status_code=404,
        body={"error": "WorkspaceNotFound"},
        request_id="req-x",
        operation_id=None,
    )
    target = tmp_path / "rbac.csv"
    runner = CliRunner()
    p1, p2, p3 = _patched_rbac_clients(fabric_client, powerbi_client)
    with p1, p2, p3:
        result = runner.invoke(
            app,
            ["rbac-audit", "--workspace-id", "ws-typo", "--out", str(target)],
        )
    assert result.exit_code != 0
    assert "not found" in result.output
    assert not target.exists()  # error surfaced before any file was created
