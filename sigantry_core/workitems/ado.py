"""Azure DevOps Work Items WorkItemProvider -- REST atop BaseRestClient.

Implements the ``WorkItemProvider`` Protocol from
``sigantry_core.protocols`` (Plan 11-01). Composes a private
``BaseRestClient`` rather than subclassing -- the Protocol shape is not
the "rest client" shape.

Auth:
    DefaultAzureCredential chain via ``sigantry_core.auth.TokenProvider``
    against ``AZURE_DEVOPS_SCOPE``
    (``499b84ac-1321-427f-aa17-267ca6975798/.default``). PAT auth is NOT
    supported in v3.0 -- Microsoft fully deprecated ADO PAT-equivalent
    OAuth apps in 2026. See:
    https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/service-principal-managed-identity

Endpoints:
    - ping: GET /{project}/_apis/projects/{project}?api-version=7.1
    - batch fetch: POST /{project}/_apis/wit/workitemsbatch?api-version=7.1
    - add comment: POST /{project}/_apis/wit/workItems/{id}/comments
                         ?api-version=7.0-preview.3   (PREVIEW -- see Pitfall 1
                         in 11-RESEARCH.md; the GA contract still uses the
                         preview namespace as of 7.1 docs)

Single-HTTP-client policy: this module MUST NOT ``import httpx`` at all
(ruff ``TID251`` ban applies even inside ``TYPE_CHECKING`` blocks because
the per-file-ignore in ``pyproject.toml`` only covers
``sigantry_core/client/**``, ``sigantry_core/auth/diagnose.py``, and
``sigantry_core/auth/github_app.py``). The ``http_client`` constructor
kwarg is therefore typed as ``Any | None`` (with ``from __future__ import
annotations`` rendering it a string at runtime) so the constructor can
still accept an ``httpx.Client`` from a caller without naming the symbol
in this file.

Source patterns:
    - sigantry_core/client/fabric.py:40-92 (subclass + from_defaults factory).
    - 11-RESEARCH.md Pattern 1 (composition, not subclassing).
    - 11-PATTERNS.md lines 96-167 (workitems/ado.py mapping).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from sigantry_core.auth import TokenProvider, get_token_provider
from sigantry_core.auth.audiences import AZURE_DEVOPS_SCOPE
from sigantry_core.client.base import BaseRestClient
from sigantry_core.protocols import WorkItem
from sigantry_core.workitems._payload import format_structured_comment

if TYPE_CHECKING:
    from sigantry_core.release.record import DeployRecord

_ADO_FIELDS: Final[list[str]] = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.AssignedTo",
]
"""Universal System.* field set across CMMI / Agile / Scrum / Basic templates.

Source: 11-RESEARCH.md Pitfall 2 (process-template variance). Only the
System.* fields are guaranteed present across every ADO process template;
process-specific fields (Microsoft.VSTS.CMMI.*, etc.) flow through
``WorkItem.raw_fields`` and never become typed attributes.
"""


def _to_work_item(raw: dict[str, Any]) -> WorkItem:
    """Convert an ADO REST work-item record to the protocol value object.

    Pitfall 2 (process-template variance): ``System.AssignedTo`` may be
    absent on some Agile / Scrum templates, AND when present its raw
    shape is ``{"displayName": "...", "uniqueName": "...", ...}`` rather
    than a plain string. Both cases must produce a coherent WorkItem.
    """
    fields = raw.get("fields", {}) if isinstance(raw, dict) else {}
    if not isinstance(fields, dict):
        fields = {}
    assigned_to_raw = fields.get("System.AssignedTo")
    assigned_to: str | None = None
    if isinstance(assigned_to_raw, dict):
        # ADO returns {"displayName": "...", "uniqueName": "...", ...}; prefer
        # the human-readable display name and fall back to uniqueName so the
        # value object never carries a None when the raw record had identity.
        candidate = assigned_to_raw.get("displayName") or assigned_to_raw.get("uniqueName")
        if isinstance(candidate, str):
            assigned_to = candidate
    elif isinstance(assigned_to_raw, str):
        assigned_to = assigned_to_raw
    raw_id = fields.get("System.Id") if "System.Id" in fields else raw.get("id", "")
    return WorkItem(
        id=str(raw_id),
        title=str(fields.get("System.Title", "")),
        work_item_type=str(fields.get("System.WorkItemType", "")),
        state=str(fields.get("System.State", "")),
        assigned_to=assigned_to,
        provider_name="ado",
        raw_fields=dict(fields),
    )


class AdoWorkItemProvider:
    """ADO Work Items REST WorkItemProvider implementation.

    Construction is inexpensive; the first :meth:`ping` /
    :meth:`fetch_work_items` / :meth:`link_release` triggers the
    :class:`BaseRestClient` token-resolution + retry + rate-limit pipeline.

    The Protocol surface (per ``sigantry_core.protocols.WorkItemProvider``):

    - ``name: str = "ado"``
    - ``ping(self) -> None``
    - ``fetch_work_items(self, ids: list[str]) -> list[WorkItem]``
    - ``link_release(self, release_id, work_items, deploy_record) -> None``

    See also :class:`sigantry_core.client.fabric.FabricRestClient` -- the
    structural analog for the ``from_defaults`` factory ergonomic pattern.
    """

    name: str = "ado"

    def __init__(
        self,
        *,
        organization: str,
        project: str,
        token_provider: TokenProvider,
        http_client: Any | None = None,
    ) -> None:
        self._client = BaseRestClient(
            token_provider=token_provider,
            base_url=f"https://dev.azure.com/{organization}",
            default_scope=AZURE_DEVOPS_SCOPE,
            http_client=http_client,
        )
        self._organization = organization
        self._project = project

    @classmethod
    def from_defaults(
        cls,
        *,
        organization: str,
        project: str,
        tenant_id: str | None = None,
    ) -> AdoWorkItemProvider:
        """Construct with the process-wide ``TokenProvider`` singleton.

        Mirrors :meth:`FabricRestClient.from_defaults`. ``tenant_id`` is
        threaded through to :func:`sigantry_core.auth.get_token_provider`
        so multi-tenant callers can pin the credential chain (Phase 1
        Pitfall P1-6).
        """
        return cls(
            organization=organization,
            project=project,
            token_provider=get_token_provider(tenant_id=tenant_id),
        )

    def ping(self) -> None:
        """Preflight authentication + connectivity check.

        Issues ``GET /_apis/projects/{project}?api-version=7.1``. The
        Get-Project endpoint is org-scoped (the project name is the path
        parameter, not a path prefix) -- prefixing the URL with the
        project segment yields a 404 against live ADO even with valid
        auth. The work-item endpoints below DO use the ``/{project}``
        prefix because work items are project-scoped resources.

        Returns ``None`` on 2xx; raises ``AuthError`` on 401,
        ``NotFoundError`` on 404, and ``HttpError`` for any other
        non-2xx classification -- all delivered by the existing
        :class:`BaseRestClient` pipeline.
        """
        self._client.send(
            "GET",
            f"/_apis/projects/{self._project}",
            params={"api-version": "7.1"},
        )

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]:
        """Single round-trip batch fetch via ``/wit/workitemsbatch``.

        Source:
        https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/get-work-items-batch?view=azure-devops-rest-7.1

        Only the universal ``System.*`` field set is requested -- see
        :data:`_ADO_FIELDS` and Pitfall 2 in 11-RESEARCH.md. Process-template
        extras would arrive in ``raw_fields`` if requested but are
        intentionally NOT added to the field list.
        """
        body: dict[str, Any] = {
            "ids": [int(i) for i in ids],
            "fields": list(_ADO_FIELDS),
        }
        resp = self._client.send(
            "POST",
            f"/{self._project}/_apis/wit/workitemsbatch",
            params={"api-version": "7.1"},
            json=body,
        )
        body_json = resp.json_body if isinstance(resp.json_body, dict) else {}
        value = body_json.get("value")
        if not isinstance(value, list):
            return []
        return [_to_work_item(w) for w in value if isinstance(w, dict)]

    def link_release(
        self,
        release_id: str,
        work_items: list[str],
        deploy_record: DeployRecord,
    ) -> None:
        """Post the structured comment on every linked work-item.

        ``release_id`` is part of the Protocol surface for cross-provider
        consistency; the ADO impl uses it transitively via
        ``deploy_record.release_id`` (already serialised in the comment by
        :func:`format_structured_comment`).

        api-version pinning (Pitfall 1 in 11-RESEARCH.md): the comments
        endpoint is ``api-version=7.0-preview.3`` even on the latest 7.1
        docs page -- the GA contract still uses the preview namespace.
        Hard-coded here; ``test_link_release_uses_preview_api_version_for_comments``
        locks the invariant.
        """
        del release_id  # serialised inside deploy_record.release_id
        text = format_structured_comment(deploy_record)
        for wi_id in work_items:
            self._client.send(
                "POST",
                f"/{self._project}/_apis/wit/workItems/{wi_id}/comments",
                params={"api-version": "7.0-preview.3"},
                json={"text": text},
            )
