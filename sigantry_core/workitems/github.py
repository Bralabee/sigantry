"""GitHub Issues WorkItemProvider -- REST atop BaseRestClient (PAT or App auth).

Implements the ``WorkItemProvider`` Protocol from
``sigantry_core.protocols`` (Plan 11-01). Composes a private
``BaseRestClient`` rather than subclassing -- the Protocol shape is not
the "rest client" shape. Auth differs from ADO: GitHub uses a
per-request Bearer token (PAT or 1-hour installation token from
``sigantry_core.auth.github_app``), so ``default_scope=""`` and the
``Authorization`` header is injected via ``extra_headers`` on every
request, overriding the Bearer-of-empty-string that BaseRestClient
synthesises from the no-op TokenProvider.

Auth modes (mutually exclusive):

- PAT: ``GithubWorkItemProvider(owner=..., repo=..., pat="ghp_...")``
       -- simplest; the v3.0 default for trial use.
- App: ``GithubWorkItemProvider(owner=..., repo=..., app_id=...,
         private_key_pem=..., installation_id=...)``
       -- production-recommended; installation token rotates via
       ``sigantry_core.auth.github_app.mint_installation_token``.

Endpoints (verified -- see 11-RESEARCH.md Code Examples B + D):

- ping:        GET  /repos/{owner}/{repo}
- fetch:       GET  /repos/{owner}/{repo}/issues/{number}
- add comment: POST /repos/{owner}/{repo}/issues/{number}/comments

Single-HTTP-client policy: this module MUST NOT ``import httpx`` at
runtime -- only inside ``TYPE_CHECKING`` for the optional ``http_client``
kwarg type hint. The ``import httpx`` carve-out for the Phase 11 wedge
is restricted to ``sigantry_core/auth/github_app.py`` (per-file TID251
ignore in pyproject.toml). Even the TYPE_CHECKING import here renders
as a string at runtime via ``from __future__ import annotations``.

Source patterns:
    - sigantry_core/workitems/ado.py (Plan 11-04 -- structurally analogous).
    - 11-RESEARCH.md Pattern 1 (composition over subclassing) +
      Pattern 2 (App-installation token mint) +
      Code Examples B + D (issue + comment endpoints) +
      Pitfall 4 (secondary rate limit -- 403 + Retry-After).
    - 11-PATTERNS.md lines 159-167 (github.py differences from ado.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sigantry_core.auth import TokenProviderProtocol
from sigantry_core.auth.github_app import mint_installation_token
from sigantry_core.client.base import BaseRestClient
from sigantry_core.protocols import WorkItem
from sigantry_core.workitems._payload import format_structured_comment

if TYPE_CHECKING:
    from sigantry_core.release.record import DeployRecord


_GITHUB_BASE: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION: Final[str] = "2022-11-28"
_TOKEN_REFRESH_MARGIN: Final[timedelta] = timedelta(seconds=60)


class _NoopTokenProvider:
    """Placeholder ``TokenProviderProtocol`` -- GitHub auth bypasses the chain.

    ``BaseRestClient`` unconditionally calls ``token_provider.get_token(scope)``
    inside ``_build_headers``. GitHub's auth model does not use OAuth
    scopes -- the ``Authorization`` header is supplied per-request via
    ``extra_headers``. This no-op satisfies ``BaseRestClient``'s contract
    without wiring ``DefaultAzureCredential``.

    Conforms to :class:`sigantry_core.auth.TokenProviderProtocol`
    (review-fix MD-02). The conformance is asserted at module import time
    via ``isinstance(_NoopTokenProvider(), TokenProviderProtocol)`` -- if
    a future change to ``BaseRestClient`` extends the Protocol with a new
    method, the import-time check fails and the breakage surfaces in CI
    instead of at runtime on first send.
    """

    tenant_id: str | None = None

    def get_token(self, scope: str) -> str:
        # ``scope`` is unused: GitHub auth bypasses the OAuth-scope chain;
        # the Authorization header is supplied per-request via
        # ``GithubWorkItemProvider._auth_headers``.
        del scope
        return ""

    def last_credential_class(self, scope: str) -> str | None:
        del scope
        return "GitHub-PAT-or-App"


# Review-fix MD-02: import-time conformance check. If a future
# ``BaseRestClient`` change extends ``TokenProviderProtocol`` with another
# method, this assertion fires at import time and surfaces in CI rather
# than at runtime on first ``send()``.
assert isinstance(_NoopTokenProvider(), TokenProviderProtocol), (
    "_NoopTokenProvider drifted from TokenProviderProtocol; "
    "BaseRestClient may now call methods that this no-op does not implement."
)


def _to_work_item(raw: dict[str, Any]) -> WorkItem:
    """Convert a GitHub Issues REST record to the protocol value object.

    GitHub returns identical schema for issues and pull requests on the
    ``/issues/{n}`` endpoint; pull requests carry an extra ``pull_request``
    sub-object. Distinguish via that key (per RESEARCH.md Code Examples).
    ``assignee`` may be ``None`` (no assignee) or a dict with a ``login``
    field -- handle both shapes coherently.
    """
    assignee = raw.get("assignee")
    assigned_to: str | None = None
    if isinstance(assignee, dict):
        login = assignee.get("login")
        if isinstance(login, str):
            assigned_to = login
    wi_type = "pull_request" if "pull_request" in raw else "issue"
    return WorkItem(
        id=str(raw.get("number", "")),
        title=str(raw.get("title", "")),
        work_item_type=wi_type,
        state=str(raw.get("state", "")),
        assigned_to=assigned_to,
        provider_name="github",
        raw_fields=dict(raw),
    )


class GithubWorkItemProvider:
    """GitHub Issues REST WorkItemProvider implementation.

    Construction is inexpensive; the first :meth:`ping` /
    :meth:`fetch_work_items` / :meth:`link_release` triggers the
    :class:`BaseRestClient` retry + rate-limit + correlated-logging
    pipeline. App-auth construction defers the installation-token mint
    until the first request that actually needs the token.

    The Protocol surface (per
    ``sigantry_core.protocols.WorkItemProvider``):

    - ``name: str = "github"``
    - ``ping(self) -> None``
    - ``fetch_work_items(self, ids: list[str]) -> list[WorkItem]``
    - ``link_release(self, release_id, work_items, deploy_record) -> None``
    """

    name: str = "github"

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
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
                "GithubWorkItemProvider: pass EITHER pat=... OR "
                "(app_id=, private_key_pem=, installation_id=), not both."
            )
        if pat is None and not app_complete:
            raise ValueError(
                "GithubWorkItemProvider: must supply pat=... OR all three of "
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
        # Review-fix MD-02: no ``# type: ignore[arg-type]`` -- the
        # ``BaseRestClient`` ``token_provider`` parameter is now typed as
        # the structural ``TokenProviderProtocol``; ``_NoopTokenProvider``
        # satisfies it explicitly via the import-time assertion above.
        self._client = BaseRestClient(
            token_provider=_NoopTokenProvider(),
            base_url=_GITHUB_BASE,
            default_scope="",
            http_client=http_client,
        )

    def _current_token(self) -> str:
        """Return the current Bearer token, refreshing the App token if near expiry."""
        if self._pat is not None:
            return self._pat
        # App path -- mint or refresh.
        if (
            self._cached_token is None
            or self._cached_expires_at is None
            or datetime.now(UTC) + _TOKEN_REFRESH_MARGIN >= self._cached_expires_at
        ):
            # Review-fix MD-03: the constructor's __init__ guard guarantees
            # these three are non-None on the App-auth code path. We had
            # ``assert ... is not None`` here as type-narrowing for mypy,
            # but ``assert`` is silently elided under ``python -O`` /
            # ``PYTHONOPTIMIZE=1`` -- so a future patch that loosens the
            # constructor check would let None values reach
            # mint_installation_token and surface as a confusing TypeError
            # from inside jwt.encode. An explicit ``if ... raise`` survives
            # -O and still narrows the Optional[str] for mypy.
            if (
                self._app_id is None
                or self._private_key_pem is None
                or self._installation_id is None
            ):
                raise RuntimeError(
                    "GithubWorkItemProvider App-auth invariant broken: "
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

    def ping(self) -> None:
        """Preflight authentication + connectivity check.

        Issues ``GET /repos/{owner}/{repo}`` with the auth headers.
        Raises ``AuthError`` on 401, ``HttpError`` on other non-2xx,
        all delivered by the existing :class:`BaseRestClient` pipeline.
        """
        self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}",
            extra_headers=self._auth_headers(),
        )

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]:
        """Fetch each Issue with a single round-trip per id.

        GitHub has no ``/issues:batch`` endpoint -- N round-trips is the
        only path. Existing tenacity retry + per-endpoint rate-limit
        bucket apply uniformly across the loop.
        """
        out: list[WorkItem] = []
        for wi_id in ids:
            resp = self._client.send(
                "GET",
                f"/repos/{self._owner}/{self._repo}/issues/{wi_id}",
                extra_headers=self._auth_headers(),
            )
            body = resp.json_body
            if isinstance(body, dict):
                out.append(_to_work_item(body))
        return out

    def link_release(
        self,
        release_id: str,
        work_items: list[str],
        deploy_record: DeployRecord,
    ) -> None:
        """Post the structured comment on every linked Issue.

        ``release_id`` is part of the Protocol surface for cross-provider
        consistency; the GitHub impl uses it transitively via
        ``deploy_record.release_id`` already serialised in the comment by
        :func:`format_structured_comment`. The comment text is
        byte-identical to the ADO provider's output for the same
        DeployRecord -- the TRACE-06 parity invariant.
        """
        del release_id  # serialised inside deploy_record.release_id
        body_text = format_structured_comment(deploy_record)
        for wi_id in work_items:
            self._client.send(
                "POST",
                f"/repos/{self._owner}/{self._repo}/issues/{wi_id}/comments",
                json={"body": body_text},
                extra_headers=self._auth_headers(),
            )
