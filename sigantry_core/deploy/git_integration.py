"""Fabric Core Git REST wrappers (DEPLOY-05).

Seven endpoints per Microsoft Learn:
  POST /v1/workspaces/{id}/git/connect              - 200 sync
  POST /v1/workspaces/{id}/git/initializeConnection - 202 LRO
  POST /v1/workspaces/{id}/git/updateFromGit        - 202 LRO
  POST /v1/workspaces/{id}/git/commitToGit          - 202 LRO
  GET  /v1/workspaces/{id}/git/status               - 200 sync OR 202 LRO
  GET  /v1/workspaces/{id}/git/connection           - 200 sync
  POST /v1/workspaces/{id}/git/disconnect           - 200 sync (destructive)

Service principal is supported on every endpoint EXCEPT ``connect`` when
``myGitCredentials.source == "Automatic"`` (Pitfall 4C / T-4-06). The
toolkit always passes ``source: "ConfiguredConnection"`` + a pre-provisioned
``connectionId``. The ``git_connection_id`` kwarg on :func:`connect_azdo`
has no default — call sites MUST supply the id, enforced both here and
by the CLI layer (``--git-connection-id`` is required).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from sigantry_core.client import FabricRestClient
from sigantry_core.governance.audit import destructive_op


@dataclass(frozen=True, slots=True)
class GitConnection:
    """Frozen view of a workspace's Git connection state.

    ``state`` is one of ``NotConnected``/``Connected``/``ConnectedAndInitialized``;
    the other fields reflect ``gitProviderDetails`` when present, else ``None``.
    """

    state: str
    provider_type: str | None
    organization_name: str | None
    project_name: str | None
    repository_name: str | None
    branch_name: str | None
    directory_name: str | None

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> GitConnection:
        details = body.get("gitProviderDetails") or {}
        return cls(
            state=body.get("gitConnectionState", "NotConnected"),
            provider_type=details.get("gitProviderType"),
            organization_name=details.get("organizationName"),
            project_name=details.get("projectName"),
            repository_name=details.get("repositoryName"),
            branch_name=details.get("branchName"),
            directory_name=details.get("directoryName"),
        )


def connect_azdo(
    client: FabricRestClient,
    workspace_id: str,
    *,
    organization_name: str,
    project_name: str,
    repository_name: str,
    branch_name: str,
    directory_name: str,
    git_connection_id: str,  # REQUIRED - no default (Pitfall 4C / T-4-06)
) -> None:
    """Attach an ADO repo to a Fabric workspace.

    POST ``/v1/workspaces/{id}/git/connect`` - 200 sync.

    SP is blocked when ``myGitCredentials.source == "Automatic"``.
    The toolkit always uses ``ConfiguredConnection`` with a pre-provisioned id.
    """
    client.send(
        "POST",
        f"/v1/workspaces/{workspace_id}/git/connect",
        json={
            "gitProviderDetails": {
                "gitProviderType": "AzureDevOps",
                "organizationName": organization_name,
                "projectName": project_name,
                "repositoryName": repository_name,
                "branchName": branch_name,
                "directoryName": directory_name,
            },
            "myGitCredentials": {
                "source": "ConfiguredConnection",
                "connectionId": git_connection_id,
            },
        },
    )


def initialize_connection(
    client: FabricRestClient,
    workspace_id: str,
    *,
    strategy: Literal["None", "PreferRemote", "PreferWorkspace"] = "PreferRemote",
) -> dict[str, Any] | None:
    """POST .../git/initializeConnection - 202 LRO. Returns init outcome.

    Note for callers reading the response body: Fabric returns the result with
    *camelCase* keys (``requiredAction``, ``remoteCommitHash``, ``workspaceHead``).
    PascalCase access silently defaults to ``"None"`` on miss — usf_fabric_cli_cicd
    paid for this in v1.8.0 (CHANGELOG API-H3). Tests at
    ``tests/sigantry_core/deploy/test_git_integration.py:125-129,155`` assert
    the camelCase contract.
    """
    return cast(
        dict[str, Any] | None,
        client.send_lro(
            "POST",
            f"/v1/workspaces/{workspace_id}/git/initializeConnection",
            json={"initializationStrategy": strategy},
        ),
    )


def connect_or_reconnect(
    client: FabricRestClient,
    workspace_id: str,
    *,
    organization_name: str,
    project_name: str,
    repository_name: str,
    branch_name: str,
    directory_name: str,
    git_connection_id: str,
    force_reconnect: bool = False,
    runbook_id: str | None = None,
) -> Literal["connected", "already-connected", "reconnected"]:
    """Idempotent + safe connect: check current state before POSTing.

    The naive sequence ``connect_azdo(...) -> initialize_connection(...)`` has
    a known failure mode: ``connect_azdo`` returns "already connected" silently
    when the workspace is already bound, but if the existing binding points
    at a *different* repo/branch/dir, the next ``initialize_connection`` call
    fails with a 400 Bad Request because the workspace state still reflects
    the old config. This is the most common failure mode of `scaffold -> deploy`
    against existing workspaces. Pattern lifted from usf_fabric_cli_cicd v1.8.1
    (services/deployer.py:1044-1133).

    Returns one of:
      - ``"connected"`` — workspace was NotConnected; new connection POSTed.
      - ``"already-connected"`` — workspace is already at the desired target;
        no-op.
      - ``"reconnected"`` — workspace was bound to a different target; the
        old binding was disconnected and the new one POSTed.

    Args:
        force_reconnect: If the workspace is connected to a *different* target,
            ``connect_or_reconnect`` will refuse with ``ValueError`` unless
            this is ``True``. The disconnect step is destructive (decorated
            with ``@destructive_op``) so opt-in is required.
        runbook_id: Optional incident id threaded into the destructive-op audit
            record on the disconnect path.
    """
    desired = (
        organization_name,
        project_name,
        repository_name,
        branch_name,
        directory_name,
    )
    current = get_connection(client, workspace_id)
    actual = (
        current.organization_name,
        current.project_name,
        current.repository_name,
        current.branch_name,
        current.directory_name,
    )

    if current.state == "NotConnected":
        connect_azdo(
            client,
            workspace_id,
            organization_name=organization_name,
            project_name=project_name,
            repository_name=repository_name,
            branch_name=branch_name,
            directory_name=directory_name,
            git_connection_id=git_connection_id,
        )
        return "connected"

    if actual == desired:
        return "already-connected"

    if not force_reconnect:
        raise ValueError(
            f"workspace {workspace_id} is connected to "
            f"{actual} but desired target is {desired}; "
            "pass force_reconnect=True to disconnect-and-reconnect "
            "(this is destructive: the existing connection will be torn down)."
        )

    disconnect(
        client,
        workspace_id,
        force=True,
        runbook_id=runbook_id,
        resource_id=workspace_id,
    )
    connect_azdo(
        client,
        workspace_id,
        organization_name=organization_name,
        project_name=project_name,
        repository_name=repository_name,
        branch_name=branch_name,
        directory_name=directory_name,
        git_connection_id=git_connection_id,
    )
    return "reconnected"


def update_from_git(
    client: FabricRestClient,
    workspace_id: str,
    *,
    workspace_head: str,
    remote_commit_hash: str,
    conflict_policy: Literal["PreferRemote", "PreferWorkspace"] = "PreferRemote",
    allow_override: bool = True,
) -> dict[str, Any] | None:
    """POST .../git/updateFromGit - 202 LRO."""
    return cast(
        dict[str, Any] | None,
        client.send_lro(
            "POST",
            f"/v1/workspaces/{workspace_id}/git/updateFromGit",
            json={
                "workspaceHead": workspace_head,
                "remoteCommitHash": remote_commit_hash,
                "conflictResolution": {
                    "conflictResolutionType": "Workspace",
                    "conflictResolutionPolicy": conflict_policy,
                },
                "options": {"allowOverrideItems": allow_override},
            },
        ),
    )


def commit_to_git(
    client: FabricRestClient,
    workspace_id: str,
    *,
    workspace_head: str,
    comment: str,
    mode: Literal["All", "Selective"] = "All",
    selected_items: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """POST .../git/commitToGit - 202 LRO.

    ``mode="Selective"`` REQUIRES a non-empty ``selected_items`` list — each
    entry is ``{"objectId": ..., "logicalId": ...}``. Validated here (not at
    the REST layer) so the caller gets a precise ``ValueError`` instead of
    a 400 from the service.
    """
    if mode == "Selective" and not selected_items:
        raise ValueError("commit_to_git: mode='Selective' requires non-empty selected_items.")
    body: dict[str, Any] = {
        "mode": mode,
        "workspaceHead": workspace_head,
        "comment": comment,
    }
    if mode == "Selective":
        body["items"] = selected_items
    return cast(
        dict[str, Any] | None,
        client.send_lro(
            "POST",
            f"/v1/workspaces/{workspace_id}/git/commitToGit",
            json=body,
        ),
    )


def get_status(client: FabricRestClient, workspace_id: str) -> dict[str, Any]:
    """GET .../git/status - 200 sync OR 202 LRO (``send_lro`` handles both).

    ``send_lro`` returns the sync body directly on 200 and the LRO result on
    202; we normalise a non-dict / ``None`` result to an empty dict so call
    sites can key-access safely.
    """
    result = client.send_lro(
        "GET",
        f"/v1/workspaces/{workspace_id}/git/status",
    )
    return result if isinstance(result, dict) else {}


def get_connection(client: FabricRestClient, workspace_id: str) -> GitConnection:
    """GET .../git/connection - 200 sync. Returns a :class:`GitConnection` DTO."""
    resp = client.send("GET", f"/v1/workspaces/{workspace_id}/git/connection")
    body = resp.json_body if isinstance(resp.json_body, dict) else {}
    return GitConnection.from_api(body)


@destructive_op("workspace_git", "disconnect")
def disconnect(
    client: FabricRestClient,
    workspace_id: str,
    *,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
    resource_id: str | None = None,
    token_provider: Any | None = None,
) -> None:
    """POST .../git/disconnect - 200 sync. Destructive (decorated).

    ``force=True`` is REQUIRED (Phase 3 ``destructive_op`` contract). The
    wrapped call emits a single INFO-level audit record on success.
    """
    client.send("POST", f"/v1/workspaces/{workspace_id}/git/disconnect")
