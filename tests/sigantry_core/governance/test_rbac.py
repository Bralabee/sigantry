"""Unit tests for sigantry_core.governance.rbac (GOV-03 + GOV-04).

Covers:
- RbacRow DTO + CSV header order
- Workspace role CRUD validation (Pitfall 10 noted in docs)
- Three-layer audit generator (Pitfall 12 — never flatten)
- Workspace 401/403 → forbidden-admin-only (Pitfall 12 — never drop a layer)
- Capacity 401/403 → forbidden-admin-only (Pitfall 5)
- Item layer placeholder per workspace (A5)
- Group principal transitive expansion via _GraphClient
- T-3-08 regression: no member UPN/displayName/id leaks into log records
"""

from __future__ import annotations

import dataclasses
import io
import logging
from unittest.mock import MagicMock

import pytest

from sigantry_core.client import (
    FabricRestClient,
    HttpError,
    HttpResponse,
    PowerBIRestClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    c.list_paginated.return_value = iter([])
    return c


@pytest.fixture
def graph_mock() -> MagicMock:
    from sigantry_core.governance.principal_expansion import _GraphClient

    c = MagicMock(spec=_GraphClient)
    c.list_paginated.return_value = iter([])
    return c


# ---------------------------------------------------------------------------
# DTO + CSV
# ---------------------------------------------------------------------------


class TestRbacRow:
    def test_rbac_row_field_order(self) -> None:
        from dataclasses import fields

        from sigantry_core.governance.rbac import RbacRow

        names = tuple(f.name for f in fields(RbacRow))
        assert names == (
            "layer",
            "resource_id",
            "resource_name",
            "principal_id",
            "principal_type",
            "principal_name",
            "role",
            "access_status",
        )

    def test_rbac_row_is_frozen(self) -> None:
        from sigantry_core.governance.rbac import RbacRow

        row = RbacRow(
            layer="workspace",
            resource_id="ws-1",
            resource_name="ws",
            principal_id="p-1",
            principal_type="User",
            principal_name="Alice",
            role="Admin",
            access_status="ok",
        )
        # Frozen dataclasses raise FrozenInstanceError on attribute set.
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.layer = "capacity"  # type: ignore[misc]

    def test_csv_header_order(self) -> None:
        from sigantry_core.governance.rbac import write_csv

        out = io.StringIO()
        write_csv([], out)
        first_line = out.getvalue().splitlines()[0]
        assert first_line == (
            "layer,resource_id,resource_name,"
            "principal_id,principal_type,principal_name,"
            "role,access_status"
        )

    def test_csv_writes_one_row_per_rbacrow(self) -> None:
        from sigantry_core.governance.rbac import RbacRow, write_csv

        rows = [
            RbacRow("workspace", "ws-1", "ws-dev", "p-1", "User", "Alice", "Admin", "ok"),
            RbacRow("capacity", "cap-1", "cap-dev", "", "", "", "", "forbidden-admin-only"),
        ]
        out = io.StringIO()
        write_csv(rows, out)
        lines = out.getvalue().splitlines()
        assert len(lines) == 3  # header + 2 rows
        assert "Alice" in lines[1]
        assert "forbidden-admin-only" in lines[2]


# ---------------------------------------------------------------------------
# Workspace role CRUD
# ---------------------------------------------------------------------------


class TestRoleCRUD:
    def test_add_role_assignment_happy_path(self, fabric_mock: MagicMock) -> None:
        from sigantry_core.governance.rbac import add_role_assignment

        fabric_mock.send.return_value = _resp(201, {"id": "p-1"})
        out = add_role_assignment(
            fabric_mock, "ws-1", principal_id="p-1", principal_type="User", role="Admin"
        )
        fabric_mock.send.assert_called_once_with(
            "POST",
            "/v1/workspaces/ws-1/roleAssignments",
            json={"principal": {"id": "p-1", "type": "User"}, "role": "Admin"},
        )
        assert out == {"id": "p-1"}

    def test_add_role_assignment_invalid_role(self, fabric_mock: MagicMock) -> None:
        from sigantry_core.governance.rbac import add_role_assignment

        with pytest.raises(ValueError, match="role must be one of"):
            add_role_assignment(
                fabric_mock,
                "ws-1",
                principal_id="p-1",
                principal_type="User",
                role="GodMode",
            )
        fabric_mock.send.assert_not_called()

    def test_add_role_assignment_invalid_principal_type(self, fabric_mock: MagicMock) -> None:
        from sigantry_core.governance.rbac import add_role_assignment

        with pytest.raises(ValueError, match="principal_type must be one of"):
            add_role_assignment(
                fabric_mock,
                "ws-1",
                principal_id="p-1",
                principal_type="Robot",
                role="Admin",
            )
        fabric_mock.send.assert_not_called()

    def test_delete_role_assignment(self, fabric_mock: MagicMock) -> None:
        from sigantry_core.governance.rbac import delete_role_assignment

        delete_role_assignment(
            fabric_mock,
            "ws-1",
            "p-1",
            force=True,
            runbook_id="RBAC-DELETE-ROLE-TEST",
            principal="test-runner",
        )
        fabric_mock.send.assert_called_once_with(
            "DELETE", "/v1/workspaces/ws-1/roleAssignments/p-1"
        )

    def test_delete_role_assignment_requires_force(self, fabric_mock: MagicMock) -> None:
        # Audit-2026-05-07 W1.7 falsifiability contract: pre-fix this
        # call succeeded silently. The decorator now enforces force=True.
        from sigantry_core.governance import DestructiveOpError
        from sigantry_core.governance.rbac import delete_role_assignment

        with pytest.raises(DestructiveOpError):
            delete_role_assignment(fabric_mock, "ws-1", "p-1", force=False)
        fabric_mock.send.assert_not_called()

    def test_delete_role_assignment_force_missing_kwarg_raises(
        self, fabric_mock: MagicMock
    ) -> None:
        from sigantry_core.governance import DestructiveOpError
        from sigantry_core.governance.rbac import delete_role_assignment

        # ``force`` is keyword-only; the @destructive_op decorator
        # checks ``kwargs.get("force", False)`` BEFORE binding the
        # wrapped function, so missing-force raises DestructiveOpError
        # rather than the bare-Python TypeError. Either failure mode is
        # acceptable contractually; the gate locks DestructiveOpError as
        # the operator-facing surface.
        with pytest.raises(DestructiveOpError):
            delete_role_assignment(fabric_mock, "ws-1", "p-1")  # type: ignore[call-arg]
        fabric_mock.send.assert_not_called()

    def test_list_role_assignments_paginated(self, fabric_mock: MagicMock) -> None:
        from sigantry_core.governance.rbac import list_role_assignments

        fabric_mock.list_paginated.return_value = iter(
            [
                {"principal": {"id": "p-1", "type": "User"}, "role": "Admin"},
                {"principal": {"id": "p-2", "type": "Group"}, "role": "Member"},
            ]
        )
        out = list(list_role_assignments(fabric_mock, "ws-1"))
        fabric_mock.list_paginated.assert_called_once_with("/v1/workspaces/ws-1/roleAssignments")
        assert len(out) == 2
        assert out[0]["role"] == "Admin"


# ---------------------------------------------------------------------------
# Audit generator (workspace + item + capacity layers)
# ---------------------------------------------------------------------------


def _list_paginated_router(routes: dict[str, list]):
    """Return a side_effect that dispatches each call by path."""

    def _route(path: str, *args, **kwargs):
        if path in routes:
            return iter(routes[path])
        # Default: empty
        return iter([])

    return _route


class TestAudit:
    def test_audit_workspace_layer_basic(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [
                    {"id": "ws-1", "displayName": "ws-dev"},
                ],
                "/v1/workspaces/ws-1/roleAssignments": [
                    {
                        "principal": {
                            "id": "p-1",
                            "type": "User",
                            "displayName": "Alice",
                        },
                        "role": "Admin",
                    }
                ],
                "/v1/capacities": [],
            }
        )

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        # Expect: 1 workspace user row + 1 item placeholder = 2 rows
        assert len(rows) == 2
        ws_row = rows[0]
        assert ws_row.layer == "workspace"
        assert ws_row.resource_id == "ws-1"
        assert ws_row.resource_name == "ws-dev"
        assert ws_row.principal_id == "p-1"
        assert ws_row.principal_type == "User"
        assert ws_row.principal_name == "Alice"
        assert ws_row.role == "Admin"
        assert ws_row.access_status == "ok"

    def test_audit_item_layer_placeholder_per_workspace(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [
                    {"id": "ws-1", "displayName": "ws-1-name"},
                    {"id": "ws-2", "displayName": "ws-2-name"},
                ],
                "/v1/capacities": [],
            }
        )

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        item_rows = [r for r in rows if r.layer == "item"]
        assert len(item_rows) == 2
        for r in item_rows:
            assert r.access_status == "not-accessible-via-rest"
            assert r.principal_id == ""
            assert r.role == ""
        # Workspace order preserved + item placeholder positioned at end of each ws block
        # (no role assignments in this fixture, so item rows are immediately yielded)
        assert [r.resource_id for r in item_rows] == ["ws-1", "ws-2"]

    def test_audit_workspace_403_emits_forbidden_row_not_dropped(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        def _raising_iter():
            raise HttpError(
                status_code=403,
                body={"error": "Forbidden"},
                request_id="req-x",
                operation_id=None,
            )
            yield  # pragma: no cover

        def _route(path: str, *args, **kwargs):
            if path == "/v1/workspaces":
                return iter(
                    [
                        {"id": "ws-allowed", "displayName": "ws-allowed-name"},
                        {"id": "ws-forbidden", "displayName": "ws-forbidden-name"},
                    ]
                )
            if path == "/v1/workspaces/ws-allowed/roleAssignments":
                return iter(
                    [
                        {
                            "principal": {"id": "p-1", "type": "User", "displayName": "U1"},
                            "role": "Admin",
                        }
                    ]
                )
            if path == "/v1/workspaces/ws-forbidden/roleAssignments":
                return _raising_iter()
            return iter([])

        fabric_mock.list_paginated.side_effect = _route

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        ws_rows = [r for r in rows if r.layer == "workspace"]
        # ws-allowed still emitted normally
        assert any(r.resource_id == "ws-allowed" and r.access_status == "ok" for r in ws_rows)
        # ws-forbidden -> single placeholder, audit not aborted
        forbidden = [r for r in ws_rows if r.resource_id == "ws-forbidden"]
        assert len(forbidden) == 1
        assert forbidden[0].access_status == "forbidden-admin-only"
        assert forbidden[0].principal_id == ""
        # item placeholder still emitted for the forbidden workspace (Pitfall 12 — never drop a layer)
        assert any(r.layer == "item" and r.resource_id == "ws-forbidden" for r in rows)

    def test_audit_workspace_401_emits_forbidden_row_not_dropped(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        def _raising_iter():
            raise HttpError(
                status_code=401,
                body={"error": "Unauthorized"},
                request_id="req-x",
                operation_id=None,
            )
            yield  # pragma: no cover

        def _route(path: str, *args, **kwargs):
            if path == "/v1/workspaces":
                return iter([{"id": "ws-forbidden", "displayName": "ws-forbidden-name"}])
            if path == "/v1/workspaces/ws-forbidden/roleAssignments":
                return _raising_iter()
            return iter([])

        fabric_mock.list_paginated.side_effect = _route

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        ws_rows = [r for r in rows if r.layer == "workspace"]
        assert len(ws_rows) == 1
        assert ws_rows[0].access_status == "forbidden-admin-only"
        assert ws_rows[0].resource_id == "ws-forbidden"

    def test_audit_expands_group_principal(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [{"id": "ws-1", "displayName": "ws-1"}],
                "/v1/workspaces/ws-1/roleAssignments": [
                    {
                        "principal": {
                            "id": "g-1",
                            "type": "Group",
                            "displayName": "Eng-Admins",
                        },
                        "role": "Admin",
                    }
                ],
                "/v1/capacities": [],
            }
        )
        graph_mock.list_paginated.return_value = iter(
            [
                {
                    "id": "u-1",
                    "displayName": "Alice",
                    "@odata.type": "#microsoft.graph.user",
                },
                {
                    "id": "u-2",
                    "displayName": "Bob",
                    "@odata.type": "#microsoft.graph.user",
                },
            ]
        )

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        ws_rows = [r for r in rows if r.layer == "workspace"]
        # 1 group row + 2 expanded member rows
        assert len(ws_rows) == 3
        assert ws_rows[0].principal_type == "Group"
        assert ws_rows[0].access_status == "ok"
        # Members carry via-group:<group-name>
        member_rows = ws_rows[1:]
        assert {r.principal_id for r in member_rows} == {"u-1", "u-2"}
        for r in member_rows:
            assert r.access_status == "via-group:Eng-Admins"
            assert r.role == "Admin"
            assert r.principal_type == "User"

    def test_audit_capacity_layer_happy_path(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [],
                "/v1/capacities": [{"id": "cap-1", "displayName": "cap-prod"}],
            }
        )
        powerbi_mock.send.return_value = _resp(
            200,
            {
                "value": [
                    {
                        "graphId": "u-9",
                        "identifier": "u9@corp",
                        "displayName": "Capacity Admin",
                        "principalType": "User",
                        "capacityUserAccessRight": "Admin",
                    }
                ]
            },
        )

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        cap_rows = [r for r in rows if r.layer == "capacity"]
        assert len(cap_rows) == 1
        assert cap_rows[0].resource_id == "cap-1"
        assert cap_rows[0].principal_id == "u-9"
        assert cap_rows[0].principal_name == "Capacity Admin"
        assert cap_rows[0].role == "Admin"
        assert cap_rows[0].access_status == "ok"
        powerbi_mock.send.assert_called_once_with("GET", "/v1.0/myorg/admin/capacities/cap-1/users")

    def test_audit_capacity_403_emits_forbidden_row_not_dropped(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [],
                "/v1/capacities": [{"id": "cap-1", "displayName": "cap-prod"}],
            }
        )
        powerbi_mock.send.side_effect = HttpError(
            status_code=403,
            body={"error": "Forbidden"},
            request_id="req-x",
            operation_id=None,
        )

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        cap_rows = [r for r in rows if r.layer == "capacity"]
        assert len(cap_rows) == 1
        assert cap_rows[0].access_status == "forbidden-admin-only"
        assert cap_rows[0].resource_id == "cap-1"
        assert cap_rows[0].principal_id == ""

    def test_audit_capacity_401_emits_forbidden_row_not_dropped(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [],
                "/v1/capacities": [{"id": "cap-1", "displayName": "cap-prod"}],
            }
        )
        powerbi_mock.send.side_effect = HttpError(
            status_code=401,
            body={"error": "Unauthorized"},
            request_id="req-x",
            operation_id=None,
        )

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        cap_rows = [r for r in rows if r.layer == "capacity"]
        assert len(cap_rows) == 1
        assert cap_rows[0].access_status == "forbidden-admin-only"
        assert cap_rows[0].resource_id == "cap-1"
        assert cap_rows[0].principal_id == ""

    def test_audit_capacity_other_error_raises(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [],
                "/v1/capacities": [{"id": "cap-1", "displayName": "cap-prod"}],
            }
        )
        powerbi_mock.send.side_effect = HttpError(
            status_code=500, body={"error": "Boom"}, request_id="r", operation_id=None
        )

        with pytest.raises(HttpError) as exc_info:
            list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# T-3-08 regression: no member UPN / displayName / id in log records
# ---------------------------------------------------------------------------


class TestNoGraphLeak:
    def test_audit_no_member_upn_in_logs(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """T-3-08 regression: rbac.audit must not emit log records that
        contain any member UPN, displayName, or id from the
        transitive-members response. Member identities flow into RbacRow
        rows (CLI-only output), never into the correlated log stream."""
        from sigantry_core.governance.rbac import audit

        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces": [{"id": "ws-1", "displayName": "ws-1"}],
                "/v1/workspaces/ws-1/roleAssignments": [
                    {
                        "principal": {
                            "id": "g-1",
                            "type": "Group",
                            "displayName": "Eng-Admins",
                        },
                        "role": "Admin",
                    }
                ],
                "/v1/capacities": [],
            }
        )

        sentinel_upn = "alice.confidential@corp.example.com"
        sentinel_display = "Alice Confidential"
        sentinel_id = "11111111-2222-3333-4444-555555555555"
        graph_mock.list_paginated.return_value = iter(
            [
                {
                    "id": sentinel_id,
                    "displayName": sentinel_display,
                    "userPrincipalName": sentinel_upn,
                    "mail": sentinel_upn,
                    "@odata.type": "#microsoft.graph.user",
                }
            ]
        )

        # Capture EVERYTHING under the sigantry_core tree at DEBUG.
        caplog.set_level(logging.DEBUG, logger="sigantry_core")

        rows = list(audit(fabric_mock, powerbi_mock, graph_client=graph_mock))

        # Sanity: the row carries the sensitive data (CLI output is the
        # legitimate sink) — confirms the test exercised the leak surface.
        ws_rows = [r for r in rows if r.layer == "workspace"]
        member_rows = [r for r in ws_rows if r.principal_id == sentinel_id]
        assert len(member_rows) == 1, "expected one expanded member row"

        # Now: NO log record may carry the sentinel UPN/displayName/id.
        for record in caplog.records:
            text = record.getMessage() + " " + repr(record.__dict__)
            assert sentinel_upn not in text, f"member UPN leaked into log record: {record!r}"
            assert sentinel_display not in text, (
                f"member displayName leaked into log record: {record!r}"
            )
            assert sentinel_id not in text, f"member id leaked into log record: {record!r}"


# ---------------------------------------------------------------------------
# Scoped audit (--workspace-id): GOV-04 enhancement, 2026-06-11 operator
# feedback -- tenant-wide console dump is not actionable; scoping + file
# output are. Engine half: workspace_ids resolution + capacity narrowing.
# ---------------------------------------------------------------------------


class TestScopedAudit:
    def test_scoped_audit_resolves_ids_and_narrows_capacity_layer(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.send.return_value = _resp(
            200, {"id": "ws-1", "displayName": "ws-dev", "capacityId": "cap-1"}
        )
        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {
                "/v1/workspaces/ws-1/roleAssignments": [
                    {
                        "principal": {"id": "p-1", "type": "User", "displayName": "Alice"},
                        "role": "Admin",
                    }
                ],
                # Listing returns the capacity GUID in a DIFFERENT case --
                # the name lookup must match case-insensitively.
                "/v1/capacities": [{"id": "CAP-1", "displayName": "F2-dev"}],
            }
        )
        powerbi_mock.send.return_value = _resp(
            200,
            {
                "value": [
                    {
                        "graphId": "p-9",
                        "principalType": "User",
                        "displayName": "Bob",
                        "capacityUserAccessRight": "Admin",
                    }
                ]
            },
        )

        rows = list(
            audit(fabric_mock, powerbi_mock, graph_client=graph_mock, workspace_ids=["ws-1"])
        )

        # workspace row + item placeholder + 1 capacity row
        assert [r.layer for r in rows] == ["workspace", "item", "capacity"]
        assert rows[0].resource_name == "ws-dev"
        cap_row = rows[2]
        assert cap_row.resource_id == "cap-1"
        assert cap_row.resource_name == "F2-dev"  # case-insensitive name match
        assert cap_row.principal_name == "Bob"
        # The tenant-wide workspace listing must NOT have been used.
        listed_paths = [c.args[0] for c in fabric_mock.list_paginated.call_args_list]
        assert "/v1/workspaces" not in listed_paths

    def test_scoped_audit_unknown_workspace_raises_value_error(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.send.side_effect = HttpError(
            status_code=404,
            body={"error": "WorkspaceNotFound"},
            request_id="req-x",
            operation_id=None,
        )
        with pytest.raises(ValueError, match="not found"):
            list(
                audit(
                    fabric_mock,
                    powerbi_mock,
                    graph_client=graph_mock,
                    workspace_ids=["ws-typo"],
                )
            )

    def test_scoped_audit_403_resolve_degrades_to_forbidden_placeholder(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.send.side_effect = HttpError(
            status_code=403,
            body={"error": "Forbidden"},
            request_id="req-x",
            operation_id=None,
        )

        def _route(path: str, *args, **kwargs):
            if path == "/v1/workspaces/ws-1/roleAssignments":
                raise HttpError(
                    status_code=403,
                    body={"error": "Forbidden"},
                    request_id="req-y",
                    operation_id=None,
                )
            return iter([])

        fabric_mock.list_paginated.side_effect = _route

        rows = list(
            audit(fabric_mock, powerbi_mock, graph_client=graph_mock, workspace_ids=["ws-1"])
        )
        # forbidden workspace placeholder + item placeholder; no capacity
        # rows (no capacityId was resolvable).
        assert [r.layer for r in rows] == ["workspace", "item"]
        assert rows[0].access_status == "forbidden-admin-only"

    def test_scoped_audit_duplicate_ids_resolved_once(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        from sigantry_core.governance.rbac import audit

        fabric_mock.send.return_value = _resp(200, {"id": "ws-1", "displayName": "ws-dev"})
        fabric_mock.list_paginated.side_effect = _list_paginated_router({})

        rows = list(
            audit(
                fabric_mock,
                powerbi_mock,
                graph_client=graph_mock,
                workspace_ids=["ws-1", "ws-1"],
            )
        )
        assert fabric_mock.send.call_count == 1
        # one item placeholder => the workspace was audited exactly once
        assert [r.layer for r in rows] == ["item"]

    def test_scoped_capacity_absent_from_listing_still_audited(
        self,
        fabric_mock: MagicMock,
        powerbi_mock: MagicMock,
        graph_mock: MagicMock,
    ) -> None:
        """A scoped capacityId invisible in the tenant listing must still be
        probed -- silence would read as 'no capacity layer' to an auditor."""
        from sigantry_core.governance.rbac import audit

        fabric_mock.send.return_value = _resp(
            200, {"id": "ws-1", "displayName": "ws-dev", "capacityId": "cap-hidden"}
        )
        fabric_mock.list_paginated.side_effect = _list_paginated_router(
            {"/v1/capacities": []}  # listing does not show the capacity
        )
        powerbi_mock.send.side_effect = HttpError(
            status_code=403,
            body={"error": "Forbidden"},
            request_id="req-z",
            operation_id=None,
        )

        rows = list(
            audit(fabric_mock, powerbi_mock, graph_client=graph_mock, workspace_ids=["ws-1"])
        )
        cap_rows = [r for r in rows if r.layer == "capacity"]
        assert len(cap_rows) == 1
        assert cap_rows[0].resource_id == "cap-hidden"
        assert cap_rows[0].access_status == "forbidden-admin-only"
