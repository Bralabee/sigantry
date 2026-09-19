"""GitHub Pull Requests Provider -- REST atop BaseRestClient (PAT or App auth).

Implements the :class:`sigantry_core.pr_bot.providers.base.Provider` Protocol
(Plan 14-05 Task 1). Composes a private :class:`BaseRestClient` rather than
subclassing -- the Provider Protocol shape is not the "rest client" shape.
Auth differs from ADO: GitHub uses a per-request Bearer token (PAT or
1-hour installation token from :mod:`sigantry_core.auth.github_app`), so
``default_scope=""`` and the ``Authorization`` header is injected via
``extra_headers`` on every request, overriding the Bearer-of-empty-string
that BaseRestClient synthesises from the no-op TokenProvider.

This module is the structural twin of
:mod:`sigantry_core.workitems.github` (Phase 11). Only the endpoint set
differs: PR endpoints replace work-item endpoints. Auth + caching +
banned-API discipline are reused verbatim.

Auth modes (mutually exclusive):

- PAT: ``GithubProvider(owner=..., repo=..., pat="ghp_...")``
       -- simplest; default for trial use.
- App: ``GithubProvider(owner=..., repo=..., app_id=...,
         private_key_pem=..., installation_id=...)``
       -- production-recommended; installation token rotates via
       :func:`sigantry_core.auth.github_app.mint_installation_token`.

Endpoints (verified -- see 14-RESEARCH.md §Pattern 3 + §Code Examples §3):

- ping:              GET  /repos/{owner}/{repo}
- get_pr:            GET  /repos/{owner}/{repo}/pulls/{n}
- get_changed_files: GET  /repos/{owner}/{repo}/pulls/{n}/files (paginates)
- post_comment:      POST /repos/{owner}/{repo}/issues/{n}/comments
                          (issue-comments endpoint -- every PR is an issue)

Single-HTTP-client policy: this module routes all HTTP through
:class:`BaseRestClient`. The ``http_client`` constructor kwarg is typed
as ``Any | None`` so a caller can still inject an alternative transport
without naming the upstream symbol in this file (per the workitems/ado.py
precedent).

Source patterns:
    - sigantry_core/workitems/github.py (Phase 11 -- structurally identical
      with PR endpoints replacing work-item endpoints).
    - 14-RESEARCH.md §Pattern 3 (PAT-or-App auth) + §Code Examples §3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sigantry_core.auth import TokenProviderProtocol
from sigantry_core.auth.github_app import mint_installation_token
from sigantry_core.client.base import BaseRestClient
from sigantry_core.pr_bot.providers.base import (
    ChangedFile,
    PullRequest,
)

_GITHUB_BASE: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION: Final[str] = "2022-11-28"
_TOKEN_REFRESH_MARGIN: Final[timedelta] = timedelta(seconds=60)
_DEFAULT_PER_PAGE: Final[int] = 30
"""Default per_page for the changed-files endpoint (GitHub max 100)."""
_MAX_PAGES: Final[int] = 100
"""Hard pagination cap: 30 entries x 100 pages = 3000 changed files max."""


class _NoopTokenProvider:
    """Placeholder ``TokenProviderProtocol`` -- GitHub auth bypasses the chain.

    Identical in shape + intent to
    :class:`sigantry_core.workitems.github._NoopTokenProvider`.
    BaseRestClient unconditionally calls ``token_provider.get_token(scope)``
    inside ``_build_headers``; GitHub auth supplies the ``Authorization``
    header per-request via ``extra_headers``. This no-op satisfies
    BaseRestClient's contract without wiring DefaultAzureCredential.
    """

    tenant_id: str | None = None

    def get_token(self, scope: str) -> str:
        del scope  # GitHub bypasses the OAuth-scope chain.
        return ""

    def last_credential_class(self, scope: str) -> str | None:
        del scope
        return "GitHub-PAT-or-App"


# Import-time conformance check (mirrors workitems/github.py review-fix MD-02).
# If a future BaseRestClient change extends TokenProviderProtocol with a new
# method, this assertion fires at import time and surfaces in CI rather than
# at runtime on first send.
assert isinstance(_NoopTokenProvider(), TokenProviderProtocol), (
    "_NoopTokenProvider drifted from TokenProviderProtocol; "
    "BaseRestClient may now call methods that this no-op does not implement."
)


_CHANGE_TYPE_MAP: Final[dict[str, str]] = {
    "added": "added",
    "modified": "modified",
    "removed": "removed",
    "renamed": "renamed",
    "changed": "modified",  # GitHub occasionally returns "changed" for edits.
    "copied": "added",
}
"""GitHub ``status`` -> normalised :class:`ChangedFile.change_type` literal."""


class GithubProvider:
    """GitHub Pull Requests Provider implementation.

    Construction is inexpensive; the first :meth:`ping` /
    :meth:`get_pr` / :meth:`get_changed_files` / :meth:`post_comment`
    triggers the :class:`BaseRestClient` retry + rate-limit +
    correlated-logging pipeline. App-auth construction defers the
    installation-token mint until the first request that actually needs
    the token.
    """

    name: str = "github"

    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        pat: str | None = None,
        app_id: str | None = None,
        private_key_pem: str | None = None,
        installation_id: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        app_supplied = (
            app_id is not None or private_key_pem is not None or installation_id is not None
        )
        app_complete = (
            app_id is not None and private_key_pem is not None and installation_id is not None
        )
        if pat is not None and app_supplied:
            raise ValueError(
                "GithubProvider: pass EITHER pat=... OR "
                "(app_id=, private_key_pem=, installation_id=), not both."
            )
        if pat is None and not app_complete:
            raise ValueError(
                "GithubProvider: must supply pat=... OR all three of "
                "(app_id, private_key_pem, installation_id)."
            )
        self._owner = owner
        self._repo = repo
        self._pat = pat
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._installation_id = installation_id
        self._cached_token: str | None = None
        self._cached_expires_at: datetime | None = None
        self._client = BaseRestClient(
            token_provider=_NoopTokenProvider(),
            base_url=_GITHUB_BASE,
            default_scope="",
            http_client=http_client,
        )

    # ---- auth helpers --------------------------------------------------

    def _current_token(self) -> str:
        """Return the current Bearer token, refreshing the App token if near expiry."""
        if self._pat is not None:
            return self._pat
        if (
            self._cached_token is None
            or self._cached_expires_at is None
            or datetime.now(UTC) + _TOKEN_REFRESH_MARGIN >= self._cached_expires_at
        ):
            # Same survives-python-O guard as workitems/github.py (review-fix MD-03).
            if (
                self._app_id is None
                or self._private_key_pem is None
                or self._installation_id is None
            ):
                raise RuntimeError(
                    "GithubProvider App-auth invariant broken: "
                    "_app_id / _private_key_pem / _installation_id is None. "
                    "The constructor must reject this combination earlier."
                )
            token, expires_at = mint_installation_token(
                self._app_id, self._private_key_pem, self._installation_id
            )
            self._cached_token = token
            self._cached_expires_at = expires_at
        return self._cached_token

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._current_token()}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }

    # ---- Provider Protocol surface -------------------------------------

    def ping(self) -> None:
        """Preflight: ``GET /repos/{owner}/{repo}``."""
        self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}",
            extra_headers=self._auth_headers(),
        )

    def get_pr(self, pr_id: str) -> PullRequest:
        """Fetch the PR's metadata via ``GET /repos/{owner}/{repo}/pulls/{n}``."""
        resp = self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}/pulls/{pr_id}",
            extra_headers=self._auth_headers(),
        )
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        base = body.get("base", {}) if isinstance(body.get("base"), dict) else {}
        head = body.get("head", {}) if isinstance(body.get("head"), dict) else {}
        return PullRequest(
            id=pr_id,
            title=str(body.get("title", "")),
            base_ref=str(base.get("ref", "")),
            head_ref=str(head.get("ref", "")),
            base_sha=str(base.get("sha", "")),
            head_sha=str(head.get("sha", "")),
            provider_name="github",
        )

    def get_changed_files(self, pr_id: str) -> list[ChangedFile]:
        """List changed files via ``GET /repos/{owner}/{repo}/pulls/{n}/files``.

        Paginates at :data:`_DEFAULT_PER_PAGE` per page; hard cap at
        :data:`_MAX_PAGES` (3000 entries). When the server returns fewer
        than ``per_page`` entries on a page, pagination stops.
        """
        out: list[ChangedFile] = []
        for page in range(1, _MAX_PAGES + 1):
            resp = self._client.send(
                "GET",
                f"/repos/{self._owner}/{self._repo}/pulls/{pr_id}/files",
                params={"per_page": _DEFAULT_PER_PAGE, "page": page},
                extra_headers=self._auth_headers(),
            )
            body = resp.json_body
            if not isinstance(body, list):
                break
            for entry in body:
                if not isinstance(entry, dict):
                    continue
                status = str(entry.get("status", "modified"))
                normalised = _CHANGE_TYPE_MAP.get(status, "modified")
                # mypy: Literal narrowing -- the map values are exactly the four
                # accepted strings; cast via the dict above guarantees it.
                out.append(
                    ChangedFile(
                        path=str(entry.get("filename", "")),
                        change_type=normalised,  # type: ignore[arg-type]
                        previous_path=(
                            str(entry["previous_filename"])
                            if isinstance(entry.get("previous_filename"), str)
                            else None
                        ),
                    )
                )
            if len(body) < _DEFAULT_PER_PAGE:
                break
        return out

    def post_comment(self, pr_id: str, body: str) -> str:
        """POST ``body`` as an issue comment on the PR; return the comment id.

        Endpoint: ``POST /repos/{owner}/{repo}/issues/{n}/comments`` with
        body ``{"body": text}`` -- the issue-comments endpoint is the
        correct API for PR-level comments (every pull request is an issue
        per docs.github.com). Review comments (file-line level) live on a
        different endpoint.
        """
        resp = self._client.send(
            "POST",
            f"/repos/{self._owner}/{self._repo}/issues/{pr_id}/comments",
            json={"body": body},
            extra_headers=self._auth_headers(),
        )
        result = resp.json_body if isinstance(resp.json_body, dict) else {}
        return str(result.get("id", ""))
