"""Unit tests for sigantry_core.deploy.git_integration (DEPLOY-05).

Covers all seven Fabric Core Git REST wrappers, the GitConnection DTO,
the ConfiguredConnection-only invariant (Pitfall 4C / T-4-06), and the
@destructive_op gating on disconnect (T-3 carryover).
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from sigantry_core.client import FabricRestClient, HttpResponse
from sigantry_core.deploy.git_integration import (
    GitConnection,
    commit_to_git,
    connect_azdo,
    connect_or_reconnect,
    disconnect,
    get_connection,
    get_status,
    initialize_connection,
    update_from_git,
)
from sigantry_core.governance.audit import DestructiveOpError


def _mock_client(*, send_body: dict | None = None, lro_result: dict | None = None) -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.send.return_value = HttpResponse(
        status_code=200,
        json_body=send_body or {},
        headers={},
        request_id="req",
        operation_id=None,
        elapsed_ms=1.0,
    )
    c.send_lro.return_value = lro_result
    return c


class TestConnect:
    def test_sends_configured_connection(self) -> None:
        c = _mock_client()
        connect_azdo(
            c,
            "ws-1",
            organization_name="org",
            project_name="proj",
            repository_name="repo",
            branch_name="main",
            directory_name="fabric_items/",
            git_connection_id="conn-123",
        )
        c.send.assert_called_once()
        args, kwargs = c.send.call_args
        assert args[:2] == ("POST", "/v1/workspaces/ws-1/git/connect")
        body = kwargs["json"]
        assert body["myGitCredentials"]["source"] == "ConfiguredConnection"
        assert body["myGitCredentials"]["connectionId"] == "conn-123"
        # T-4-06 regression guard
        assert body["myGitCredentials"]["source"] != "Automatic"

    def test_connect_payload_schema(self) -> None:
        c = _mock_client()
        connect_azdo(
            c,
            "ws-1",
            organization_name="example-org",
            project_name="example-project",
            repository_name="example-repo",
            branch_name="master",
            directory_name="fabric_items/",
            git_connection_id="conn-1",
        )
        body = c.send.call_args.kwargs["json"]
        gp = body["gitProviderDetails"]
        assert gp["gitProviderType"] == "AzureDevOps"
        assert gp["organizationName"] == "example-org"
        assert gp["projectName"] == "example-project"
        assert gp["repositoryName"] == "example-repo"
        assert gp["branchName"] == "master"
        assert gp["directoryName"] == "fabric_items/"

    def test_connect_requires_git_connection_id(self) -> None:
        c = _mock_client()
        with pytest.raises(TypeError):
            connect_azdo(  # type: ignore[call-arg]
                c,
                "ws-1",
                organization_name="org",
                project_name="p",
                repository_name="r",
                branch_name="main",
                directory_name="d/",
            )

    def test_connect_never_uses_automatic(self) -> None:
        """Regression guard on Pitfall 4C / T-4-06.

        Under every call shape, the payload must NEVER contain
        ``source: "Automatic"``. SP is blocked on that path — we always
        use ConfiguredConnection.
        """
        c = _mock_client()
        for conn_id in ("c1", "c2", "c3"):
            connect_azdo(
                c,
                "ws-x",
                organization_name="org",
                project_name="proj",
                repository_name="repo",
                branch_name="main",
                directory_name="d/",
                git_connection_id=conn_id,
            )
        for call in c.send.call_args_list:
            body = call.kwargs["json"]
            assert body["myGitCredentials"]["source"] == "ConfiguredConnection"
            assert body["myGitCredentials"]["source"] != "Automatic"


class TestInit:
    def test_uses_send_lro(self) -> None:
        c = _mock_client(lro_result={"requiredAction": "UpdateFromGit"})
        out = initialize_connection(c, "ws-1")
        c.send_lro.assert_called_once()
        c.send.assert_not_called()
        assert out == {"requiredAction": "UpdateFromGit"}

    def test_strategy_kwarg(self) -> None:
        c = _mock_client(lro_result={})
        initialize_connection(c, "ws-1", strategy="PreferWorkspace")
        body = c.send_lro.call_args.kwargs["json"]
        assert body["initializationStrategy"] == "PreferWorkspace"

    def test_default_strategy_is_prefer_remote(self) -> None:
        c = _mock_client(lro_result={})
        initialize_connection(c, "ws-1")
        body = c.send_lro.call_args.kwargs["json"]
        assert body["initializationStrategy"] == "PreferRemote"


class TestUpdateFromGit:
    def test_body_shape(self) -> None:
        c = _mock_client(lro_result={})
        update_from_git(
            c,
            "ws-1",
            workspace_head="abc",
            remote_commit_hash="def",
        )
        body = c.send_lro.call_args.kwargs["json"]
        assert body["workspaceHead"] == "abc"
        assert body["remoteCommitHash"] == "def"
        assert body["conflictResolution"]["conflictResolutionPolicy"] == "PreferRemote"
        assert body["options"]["allowOverrideItems"] is True


class TestCommitToGit:
    def test_all_mode(self) -> None:
        c = _mock_client(lro_result={})
        commit_to_git(c, "ws-1", workspace_head="abc", comment="deploy dev")
        body = c.send_lro.call_args.kwargs["json"]
        assert body["mode"] == "All"
        assert "items" not in body

    def test_selective_without_items_raises(self) -> None:
        c = _mock_client(lro_result={})
        with pytest.raises(ValueError, match="selected_items"):
            commit_to_git(c, "ws-1", workspace_head="abc", comment="x", mode="Selective")

    def test_selective_with_items(self) -> None:
        c = _mock_client(lro_result={})
        items = [{"objectId": "xxx", "logicalId": "yyy"}]
        commit_to_git(
            c,
            "ws-1",
            workspace_head="abc",
            comment="partial",
            mode="Selective",
            selected_items=items,
        )
        body = c.send_lro.call_args.kwargs["json"]
        assert body["mode"] == "Selective"
        assert body["items"] == items


class TestStatusConnection:
    def test_get_status_calls_lro(self) -> None:
        c = _mock_client(lro_result={"changes": []})
        out = get_status(c, "ws-1")
        c.send_lro.assert_called_once()
        assert out == {"changes": []}

    def test_get_connection_parses_dto(self) -> None:
        c = _mock_client(
            send_body={
                "gitConnectionState": "Connected",
                "gitProviderDetails": {
                    "gitProviderType": "AzureDevOps",
                    "organizationName": "example-org",
                    "projectName": "example-project",
                    "repositoryName": "example-repo",
                    "branchName": "master",
                    "directoryName": "fabric_items/",
                },
            }
        )
        gc = get_connection(c, "ws-1")
        assert isinstance(gc, GitConnection)
        assert gc.state == "Connected"
        assert gc.organization_name == "example-org"
        assert gc.project_name == "example-project"
        assert gc.repository_name == "example-repo"
        assert gc.branch_name == "master"
        assert gc.directory_name == "fabric_items/"

    def test_get_connection_missing_state_defaults_not_connected(self) -> None:
        c = _mock_client(send_body={})
        gc = get_connection(c, "ws-1")
        assert gc.state == "NotConnected"
        assert gc.provider_type is None


class TestConnectOrReconnect:
    """Gotcha #12 — disconnect-before-reconnect when desired target differs from
    current binding. Pattern lifted from usf_fabric_cli_cicd v1.8.1
    (services/deployer.py:1044-1133). See ``docs/RELATED-WORK.md``.
    """

    _DESIRED: ClassVar[dict[str, str]] = {
        "organization_name": "org",
        "project_name": "proj",
        "repository_name": "repo",
        "branch_name": "main",
        "directory_name": "fabric_items/",
        "git_connection_id": "conn-1",
    }

    def _client_with_connection(self, **fields: object) -> MagicMock:
        body: dict = {
            "gitConnectionState": fields.pop("state", "Connected"),
        }
        if fields:
            body["gitProviderDetails"] = {
                "gitProviderType": "AzureDevOps",
                "organizationName": fields.get("organization_name"),
                "projectName": fields.get("project_name"),
                "repositoryName": fields.get("repository_name"),
                "branchName": fields.get("branch_name"),
                "directoryName": fields.get("directory_name"),
            }
        return _mock_client(send_body=body)

    def test_not_connected_takes_connect_path(self) -> None:
        c = self._client_with_connection(state="NotConnected")
        outcome = connect_or_reconnect(c, "ws-1", **self._DESIRED)
        assert outcome == "connected"
        # Only the get_connection + connect_azdo calls; no disconnect.
        urls = [call.args[1] for call in c.send.call_args_list]
        assert "/v1/workspaces/ws-1/git/connection" in urls
        assert "/v1/workspaces/ws-1/git/connect" in urls
        assert "/v1/workspaces/ws-1/git/disconnect" not in urls

    def test_already_connected_to_same_target_is_noop(self) -> None:
        c = self._client_with_connection(
            state="Connected",
            organization_name="org",
            project_name="proj",
            repository_name="repo",
            branch_name="main",
            directory_name="fabric_items/",
        )
        outcome = connect_or_reconnect(c, "ws-1", **self._DESIRED)
        assert outcome == "already-connected"
        urls = [call.args[1] for call in c.send.call_args_list]
        # Only the connection lookup. NEVER POST connect or disconnect.
        assert urls == ["/v1/workspaces/ws-1/git/connection"]

    def test_mismatched_without_force_raises_value_error(self) -> None:
        c = self._client_with_connection(
            state="Connected",
            organization_name="OTHER-org",
            project_name="proj",
            repository_name="repo",
            branch_name="main",
            directory_name="fabric_items/",
        )
        with pytest.raises(ValueError, match="force_reconnect=True"):
            connect_or_reconnect(c, "ws-1", **self._DESIRED)
        # No destructive calls leaked.
        urls = [call.args[1] for call in c.send.call_args_list]
        assert "/v1/workspaces/ws-1/git/disconnect" not in urls
        assert "/v1/workspaces/ws-1/git/connect" not in urls

    def test_mismatched_with_force_disconnects_then_connects(self) -> None:
        c = self._client_with_connection(
            state="Connected",
            organization_name="OTHER-org",
            project_name="proj",
            repository_name="repo",
            branch_name="main",
            directory_name="fabric_items/",
        )
        outcome = connect_or_reconnect(
            c,
            "ws-1",
            **self._DESIRED,
            force_reconnect=True,
            runbook_id="runbook-99",
        )
        assert outcome == "reconnected"
        urls = [call.args[1] for call in c.send.call_args_list]
        # Must have BOTH disconnect and connect, and disconnect must come first.
        assert "/v1/workspaces/ws-1/git/disconnect" in urls
        assert "/v1/workspaces/ws-1/git/connect" in urls
        assert urls.index("/v1/workspaces/ws-1/git/disconnect") < urls.index(
            "/v1/workspaces/ws-1/git/connect"
        )

    def test_branch_only_mismatch_still_triggers_reconnect_path(self) -> None:
        """A branch-only difference is enough to count as a mismatch; without
        force_reconnect the helper must refuse rather than silently no-op."""
        c = self._client_with_connection(
            state="Connected",
            organization_name="org",
            project_name="proj",
            repository_name="repo",
            branch_name="OTHER-branch",
            directory_name="fabric_items/",
        )
        with pytest.raises(ValueError, match="force_reconnect=True"):
            connect_or_reconnect(c, "ws-1", **self._DESIRED)


class TestDisconnect:
    def test_requires_force(self) -> None:
        c = _mock_client()
        with pytest.raises(DestructiveOpError, match="force=True"):
            disconnect(c, "ws-1")  # type: ignore[call-arg]
        c.send.assert_not_called()

    def test_with_force_hits_endpoint(self) -> None:
        c = _mock_client()
        disconnect(c, "ws-1", force=True, resource_id="ws-1")
        c.send.assert_called_once_with("POST", "/v1/workspaces/ws-1/git/disconnect")

    def test_force_false_raises_and_does_not_call(self) -> None:
        c = _mock_client()
        with pytest.raises(DestructiveOpError):
            disconnect(c, "ws-1", force=False)
        c.send.assert_not_called()
