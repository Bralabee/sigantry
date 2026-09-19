"""Unit tests for AdoWorkItemProvider (TRACE-02, Plan 11-04 Task 2).

Replaces the Wave 0 ``pytest.importorskip`` + ``pytest.xfail`` stubs with
respx-driven assertions that exercise the production module shipped in
Plan 11-04 Task 1. The contract test for ADO in
``tests/contract/test_workitem_provider_contract.py`` auto-resolves
(its xfail flips to PASS) because ``sigantry_core.workitems.ado`` is now
importable.

Pitfall coverage (per 11-RESEARCH.md):
    - Pitfall 1: comments endpoint is api-version=7.0-preview.3
      (NOT 7.1 GA). Locked by
      ``test_link_release_uses_preview_api_version_for_comments``.
    - Pitfall 2: ADO process-template variance --
      ``System.AssignedTo`` may be missing on Agile/Scrum templates AND
      its raw shape is a dict not a string when present. Locked by
      ``test_fetch_work_items_uses_batch_endpoint_with_locked_field_set``
      (dict shape) and
      ``test_fetch_work_items_handles_missing_assigned_to_field``
      (missing-field shape).
    - Pitfall 5: ADO 200-TSTU/5min global cap surfaces as 429 +
      ``Retry-After``; the existing tenacity policy in
      :class:`BaseRestClient` honours it. Locked by
      ``test_429_with_retry_after_eventually_succeeds``.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import AZURE_DEVOPS_SCOPE
from sigantry_core.protocols import WorkItem, WorkItemProvider
from sigantry_core.release.record import DeployRecord
from sigantry_core.workitems._payload import format_structured_comment
from sigantry_core.workitems.ado import AdoWorkItemProvider

_ORG = "myorg"
_PROJ = "myproj"
_BASE = f"https://dev.azure.com/{_ORG}"


@pytest.fixture
def deploy_record() -> DeployRecord:
    """Deterministic DeployRecord with .with_hash() applied."""
    return DeployRecord(
        workspace="ws-prod",
        release_id="R-2026-04-26-1",
        work_items=["1234"],
        fabric_items_changed=["nb.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    ).with_hash()


class TestAdoWorkItemProvider:
    def test_construction_sets_name(self, mock_token_provider: MagicMock) -> None:
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        assert p.name == "ado"

    def test_satisfies_runtime_checkable_protocol(self, mock_token_provider: MagicMock) -> None:
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        assert isinstance(p, WorkItemProvider)

    def test_no_api_version_attribute(self, mock_token_provider: MagicMock) -> None:
        """Symmetry with the other six seams (planner-mapper resolution)."""
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        assert not hasattr(p, "api_version")

    def test_ping_issues_get_with_correct_path_and_scope(
        self, mock_token_provider: MagicMock, respx_router: respx.Router
    ) -> None:
        route = respx_router.get(
            f"{_BASE}/_apis/projects/{_PROJ}",
            params={"api-version": "7.1"},
        ).respond(status_code=200, json={"id": "abc", "name": _PROJ})
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        p.ping()
        assert route.called
        mock_token_provider.get_token.assert_called_with(AZURE_DEVOPS_SCOPE)

    def test_fetch_work_items_uses_batch_endpoint_with_locked_field_set(
        self, mock_token_provider: MagicMock, respx_router: respx.Router
    ) -> None:
        """Happy path: Pitfall 2 dict-shape System.AssignedTo + locked field set."""
        response_body = {
            "value": [
                {
                    "id": 1234,
                    "fields": {
                        "System.Id": 1234,
                        "System.Title": "Login bug",
                        "System.WorkItemType": "Bug",
                        "System.State": "Active",
                        "System.AssignedTo": {
                            "displayName": "Alice",
                            "uniqueName": "alice@example.invalid",
                        },
                    },
                }
            ]
        }
        route = respx_router.post(
            f"{_BASE}/{_PROJ}/_apis/wit/workitemsbatch",
            params={"api-version": "7.1"},
        ).respond(status_code=200, json=response_body)
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        items = p.fetch_work_items(["1234"])
        assert route.called
        sent = route.calls[0].request.read()
        body = _json.loads(sent)
        assert body["ids"] == [1234]
        assert body["fields"] == [
            "System.Id",
            "System.Title",
            "System.WorkItemType",
            "System.State",
            "System.AssignedTo",
        ]
        assert len(items) == 1
        wi = items[0]
        assert isinstance(wi, WorkItem)
        assert wi.id == "1234"
        assert wi.title == "Login bug"
        assert wi.work_item_type == "Bug"
        assert wi.state == "Active"
        assert wi.assigned_to == "Alice"
        assert wi.provider_name == "ado"
        assert "System.WorkItemType" in wi.raw_fields

    def test_fetch_work_items_handles_missing_assigned_to_field(
        self, mock_token_provider: MagicMock, respx_router: respx.Router
    ) -> None:
        """Pitfall 2: some ADO process templates omit System.AssignedTo."""
        response_body = {
            "value": [
                {
                    "id": 5678,
                    "fields": {
                        "System.Id": 5678,
                        "System.Title": "Refactor X",
                        "System.WorkItemType": "Task",
                        "System.State": "New",
                        # System.AssignedTo intentionally omitted
                    },
                }
            ]
        }
        respx_router.post(
            f"{_BASE}/{_PROJ}/_apis/wit/workitemsbatch",
            params={"api-version": "7.1"},
        ).respond(status_code=200, json=response_body)
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        items = p.fetch_work_items(["5678"])
        assert items[0].assigned_to is None
        assert items[0].id == "5678"
        assert items[0].work_item_type == "Task"

    def test_link_release_posts_comment_per_work_item(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
        deploy_record: DeployRecord,
    ) -> None:
        posted: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            posted.append(request.read())
            return httpx.Response(status_code=201, json={"id": 99, "text": ""})

        respx_router.post(url__regex=rf"{_BASE}/{_PROJ}/_apis/wit/workItems/\d+/comments").mock(
            side_effect=_capture
        )
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        p.link_release(
            release_id="R-2026-04-26-1",
            work_items=["1234", "5678"],
            deploy_record=deploy_record,
        )
        assert len(posted) == 2
        for raw in posted:
            body = _json.loads(raw)
            assert body == {"text": format_structured_comment(deploy_record)}

    def test_link_release_uses_preview_api_version_for_comments(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
        deploy_record: DeployRecord,
    ) -> None:
        """Pitfall 1: comments endpoint is api-version=7.0-preview.3 even on 7.1 docs."""
        captured_params: list[dict[str, Any]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_params.append(dict(request.url.params))
            return httpx.Response(status_code=201, json={})

        respx_router.post(url__regex=rf"{_BASE}/{_PROJ}/_apis/wit/workItems/\d+/comments").mock(
            side_effect=_capture
        )
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        p.link_release(release_id="r", work_items=["1234"], deploy_record=deploy_record)
        assert captured_params == [{"api-version": "7.0-preview.3"}]

    def test_401_surfaces_as_auth_error(
        self, mock_token_provider: MagicMock, respx_router: respx.Router
    ) -> None:
        """401 response surfaces as AuthError via existing client classification."""
        from sigantry_core.client.errors import AuthError

        respx_router.get(
            f"{_BASE}/_apis/projects/{_PROJ}",
            params={"api-version": "7.1"},
        ).respond(status_code=401, json={"message": "Unauthorized"})
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        with pytest.raises(AuthError):
            p.ping()

    def test_429_with_retry_after_eventually_succeeds(
        self, mock_token_provider: MagicMock, respx_router: respx.Router
    ) -> None:
        """Pitfall 5: ADO 429 + Retry-After must be honoured by existing tenacity policy."""
        responses = [
            httpx.Response(
                status_code=429,
                headers={"Retry-After": "0"},
                json={"message": "Rate limited"},
            ),
            httpx.Response(status_code=200, json={"id": "abc", "name": _PROJ}),
        ]
        respx_router.get(
            f"{_BASE}/_apis/projects/{_PROJ}",
            params={"api-version": "7.1"},
        ).mock(side_effect=responses)
        p = AdoWorkItemProvider(
            organization=_ORG, project=_PROJ, token_provider=mock_token_provider
        )
        p.ping()  # MUST NOT raise -- retry policy succeeds on second attempt

    def test_from_defaults_uses_get_token_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_defaults wires DefaultAzureCredential via get_token_provider."""
        captured: dict[str, Any] = {}

        def _fake_factory(*, tenant_id: str | None = None) -> TokenProvider:
            captured["tenant_id"] = tenant_id
            m = MagicMock(spec=TokenProvider)
            m.get_token.return_value = "test-token"
            return m

        from sigantry_core.workitems import ado as ado_module

        monkeypatch.setattr(ado_module, "get_token_provider", _fake_factory)
        p = AdoWorkItemProvider.from_defaults(
            organization=_ORG, project=_PROJ, tenant_id="tenant-xyz"
        )
        assert captured["tenant_id"] == "tenant-xyz"
        assert p.name == "ado"
