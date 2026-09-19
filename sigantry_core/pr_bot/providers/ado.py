"""Azure DevOps Pull Requests Provider -- REST atop BaseRestClient.

Implements the :class:`sigantry_core.pr_bot.providers.base.Provider` Protocol
(Plan 14-05 Task 1). Composes :class:`BaseRestClient` rather than subclassing.

Auth: DefaultAzureCredential chain via :class:`sigantry_core.auth.TokenProvider`
against :data:`sigantry_core.auth.audiences.AZURE_DEVOPS_SCOPE`
(``499b84ac-1321-427f-aa17-267ca6975798/.default``). PAT auth is NOT supported
in v3.0; Microsoft deprecated ADO PAT-equivalent OAuth apps in 2026.

Endpoints (RESEARCH §Code Examples §4 + §Pitfalls 4-5):

- ping:   GET ``/{project}/_apis/projects/{project}?api-version=7.1``
- get_pr: GET ``.../pullRequests/{prId}?api-version=7.1``
- get_changed_files: 2-call sequence --
    1. GET ``.../pullRequests/{prId}/iterations?api-version=7.1`` (last id)
    2. GET ``.../pullRequests/{prId}/iterations/{iterId}/changes?api-version=7.1``
- post_comment: POST ``.../pullRequests/{prId}/threads?api-version=7.1`` with
    body ``{"comments":[{"parentCommentId":0,"content":<body>,"commentType":1}],
           "status":1}``

api-version pinning (RESEARCH §Pitfall 5): ``_API_VERSION`` is a module-level
:class:`Final[str]` constant; ``test_ado_post_comment_uses_api_version_71``
locks it as a regression catcher.

commentType discipline (RESEARCH §Pitfall 4): the input shape uses NUMERIC
``commentType: 1``; the response carries ``"text"`` (string). The test
``test_ado_post_comment_body_uses_numeric_commentType_1`` asserts the int 1.

Single-HTTP-client policy: routes all HTTP through :class:`BaseRestClient`.
``http_client`` kwarg is typed ``Any | None`` so callers may inject a
transport without naming the upstream symbol here.

Source: sigantry_core/workitems/ado.py (Phase 11) + 14-RESEARCH.md §4 + §5.
"""

from __future__ import annotations

from typing import Any, Final

from sigantry_core.auth import TokenProvider, get_token_provider
from sigantry_core.auth.audiences import AZURE_DEVOPS_SCOPE
from sigantry_core.client.base import BaseRestClient
from sigantry_core.pr_bot.providers.base import (
    ChangedFile,
    PullRequest,
)

_API_VERSION: Final[str] = "7.1"
"""Pinned ADO REST API version (RESEARCH §Pitfall 5).

A future Microsoft docs change to 7.2 doesn't break us, but a regression
test (``test_ado_post_comment_uses_api_version_71``) catches accidental
drift in the wire format.
"""

_CHANGE_TYPE_MAP: Final[dict[str, str]] = {
    "add": "added",
    "edit": "modified",
    "delete": "removed",
    "rename": "renamed",
    # Defensive: ADO occasionally emits combined types like "edit, rename".
    "added": "added",
    "modified": "modified",
    "removed": "removed",
    "renamed": "renamed",
}
"""ADO ``changeType`` -> normalised :class:`ChangedFile.change_type` literal.

ADO returns lower-cased verbs; the map is case-sensitive on the value
only (callers lower-case the raw ADO value before lookup).
"""


def _normalise_change_type(raw: str) -> str:
    """Resolve an ADO ``changeType`` string to a single canonical token.

    ADO can emit multi-flag values like ``"sourceRename, delete"`` or
    ``"edit, rename"``. The previous first-token-wins implementation
    misclassified deletions when ``delete`` appeared after another flag.
    Fix MD-01 (REVIEW 2026-04-28): apply semantic priority -- ``delete``
    is the most specific signal (a removed file outranks any preceding
    rename/edit annotation), then ``add``, then rename variants
    (``rename`` / ``sourcerename``), then ``edit`` as the fallback bucket.
    Also see RESEARCH §Pitfall 4 (enum-drift discipline).

    Returns the canonical lowercase token suitable for ``_CHANGE_TYPE_MAP``
    lookup. Falls back to the first non-empty token, then to ``"edit"``,
    when no recognised flag is present (preserves prior fallthrough
    behaviour for unknown values).
    """
    parts = [p.strip() for p in raw.lower().split(",") if p.strip()]
    if "delete" in parts:
        return "delete"
    if "add" in parts:
        return "add"
    if "rename" in parts or "sourcerename" in parts:
        return "rename"
    if "edit" in parts:
        return "edit"
    return parts[0] if parts else "edit"


def _strip_refs_heads(ref: str) -> str:
    """ADO returns ``refs/heads/main``; strip to ``main`` for parity with GitHub."""
    return ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref


class AdoProvider:
    """Azure DevOps Pull Requests Provider implementation.

    Construction is inexpensive; the first :meth:`ping` /
    :meth:`get_pr` / :meth:`get_changed_files` / :meth:`post_comment`
    triggers the :class:`BaseRestClient` token-resolution + retry +
    rate-limit pipeline.
    """

    name: str = "ado"

    def __init__(
        self,
        *,
        org: str,
        project: str,
        repository_id: str,
        token_provider: TokenProvider | None = None,
        http_client: Any | None = None,
    ) -> None:
        if token_provider is None:
            token_provider = get_token_provider()
        self._client = BaseRestClient(
            token_provider=token_provider,
            base_url=f"https://dev.azure.com/{org}",
            default_scope=AZURE_DEVOPS_SCOPE,
            http_client=http_client,
        )
        self._org = org
        self._project = project
        self._repository_id = repository_id

    @classmethod
    def from_defaults(
        cls,
        *,
        org: str,
        project: str,
        repository_id: str,
        tenant_id: str | None = None,
    ) -> AdoProvider:
        """Construct with the process-wide :class:`TokenProvider` singleton.

        Mirrors :meth:`sigantry_core.workitems.ado.AdoWorkItemProvider.from_defaults`.
        ``tenant_id`` is threaded through to
        :func:`sigantry_core.auth.get_token_provider` so multi-tenant
        callers can pin the credential chain.
        """
        return cls(
            org=org,
            project=project,
            repository_id=repository_id,
            token_provider=get_token_provider(tenant_id=tenant_id),
        )

    # ---- URL helpers ---------------------------------------------------

    def _pr_path(self, pr_id: str, *suffix: str) -> str:
        """Build a PR-scoped path; suffix segments are joined with ``/``."""
        base = f"/{self._project}/_apis/git/repositories/{self._repository_id}/pullRequests/{pr_id}"
        if not suffix:
            return base
        return base + "/" + "/".join(suffix)

    # ---- Provider Protocol surface -------------------------------------

    def ping(self) -> None:
        """Preflight: ``GET /{project}/_apis/projects/{project}?api-version=7.1``."""
        self._client.send(
            "GET",
            f"/{self._project}/_apis/projects/{self._project}",
            params={"api-version": _API_VERSION},
        )

    def get_pr(self, pr_id: str) -> PullRequest:
        """Fetch PR metadata via the ``pullRequests/{prId}`` endpoint."""
        resp = self._client.send(
            "GET",
            self._pr_path(pr_id),
            params={"api-version": _API_VERSION},
        )
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        target_commit = (
            body.get("lastMergeTargetCommit", {})
            if isinstance(body.get("lastMergeTargetCommit"), dict)
            else {}
        )
        source_commit = (
            body.get("lastMergeSourceCommit", {})
            if isinstance(body.get("lastMergeSourceCommit"), dict)
            else {}
        )
        return PullRequest(
            id=pr_id,
            title=str(body.get("title", "")),
            base_ref=_strip_refs_heads(str(body.get("targetRefName", ""))),
            head_ref=_strip_refs_heads(str(body.get("sourceRefName", ""))),
            base_sha=str(target_commit.get("commitId", "")),
            head_sha=str(source_commit.get("commitId", "")),
            provider_name="ado",
        )

    def get_changed_files(self, pr_id: str) -> list[ChangedFile]:
        """List changed files via the iterations + changes endpoint pair.

        ADO does not expose a direct "PR changed files" endpoint; instead
        the caller fetches the latest iteration id and then queries that
        iteration's changes. This 2-call sequence is documented in
        14-RESEARCH.md §Code Examples §4.
        """
        iter_resp = self._client.send(
            "GET",
            self._pr_path(pr_id, "iterations"),
            params={"api-version": _API_VERSION},
        )
        iter_body = iter_resp.json_body if isinstance(iter_resp.json_body, dict) else {}
        iterations = iter_body.get("value", [])
        if not isinstance(iterations, list) or not iterations:
            return []
        last = iterations[-1]
        if not isinstance(last, dict):
            return []
        iteration_id = last.get("id")
        if iteration_id is None:
            return []

        changes_resp = self._client.send(
            "GET",
            self._pr_path(pr_id, "iterations", str(iteration_id), "changes"),
            params={"api-version": _API_VERSION},
        )
        changes_body = changes_resp.json_body if isinstance(changes_resp.json_body, dict) else {}
        change_entries = changes_body.get("changeEntries", [])
        if not isinstance(change_entries, list):
            return []

        out: list[ChangedFile] = []
        for entry in change_entries:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item", {})
            if not isinstance(item, dict):
                continue
            raw_change_type = _normalise_change_type(str(entry.get("changeType", "edit")))
            normalised = _CHANGE_TYPE_MAP.get(raw_change_type, "modified")
            previous_path: str | None = None
            source_server_item = item.get("sourceServerItem")
            if isinstance(source_server_item, str):
                previous_path = source_server_item
            out.append(
                ChangedFile(
                    path=str(item.get("path", "")),
                    change_type=normalised,  # type: ignore[arg-type]
                    previous_path=previous_path,
                )
            )
        return out

    def post_comment(self, pr_id: str, body: str) -> str:
        """POST ``body`` as a thread comment on the PR; return the thread id.

        Endpoint: ``POST .../pullRequests/{prId}/threads?api-version=7.1``
        with body shape (RESEARCH §Code Examples §4 + §Pitfall 4)::

            {
              "comments": [
                {"parentCommentId": 0, "content": <body>, "commentType": 1}
              ],
              "status": 1
            }

        ``commentType: 1`` is numeric on input (Pitfall 4); the response
        carries ``"text"`` (string) but we never compare against it.
        """
        post_body = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": body,
                    "commentType": 1,
                }
            ],
            "status": 1,
        }
        resp = self._client.send(
            "POST",
            self._pr_path(pr_id, "threads"),
            params={"api-version": _API_VERSION},
            json=post_body,
        )
        result = resp.json_body if isinstance(resp.json_body, dict) else {}
        return str(result.get("id", ""))
