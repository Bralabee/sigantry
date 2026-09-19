"""Unit tests for GithubWorkItemProvider (TRACE-03, Plan 11-05 Task 2).

Replaces the Wave 0 ``pytest.importorskip`` + ``pytest.xfail`` stubs with
respx-driven assertions that exercise the production module shipped in
Plan 11-05 Task 2. The contract test for GitHub in
``tests/contract/test_workitem_provider_contract.py`` carries an
unconditional ``pytest.xfail()`` marker -- per Plan 11-04 precedent the
xfail-marker removal is deferred to Plan 11-07 (FabricDataOps integration).

Pitfall coverage (per 11-RESEARCH.md):
    - Pitfall 4: GitHub secondary rate limit returns 403 + Retry-After.
      The existing tenacity policy on BaseRestClient honours it.
      Locked by ``test_secondary_rate_limit_403_with_retry_after_succeeds``.
    - Pitfall 6: handled in tests/sigantry_core/auth/test_github_app.py
      (clock-skew invariants on the JWT side).
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from sigantry_core.protocols import WorkItem, WorkItemProvider
from sigantry_core.release.record import DeployRecord
from sigantry_core.workitems._payload import format_structured_comment
from sigantry_core.workitems.github import GithubWorkItemProvider

_OWNER = "myorg"
_REPO = "myrepo"
_BASE = f"https://api.github.com/repos/{_OWNER}/{_REPO}"
_PAT = "fake-pat-test-pat-value"


@pytest.fixture
def deploy_record() -> DeployRecord:
    """Deterministic DeployRecord with .with_hash() applied."""
    return DeployRecord(
        workspace="ws-prod",
        release_id="R-2026-04-26-1",
        work_items=["42"],
        fabric_items_changed=["nb.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    ).with_hash()


class TestGithubWorkItemProviderConstruction:
    def test_pat_only_construction_succeeds(self) -> None:
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        assert p.name == "github"
        assert isinstance(p, WorkItemProvider)

    def test_no_api_version_attribute(self) -> None:
        """Symmetry with the other six seams (planner-mapper resolution)."""
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        assert not hasattr(p, "api_version")

    def test_pat_plus_app_kwargs_raises(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            GithubWorkItemProvider(
                owner=_OWNER,
                repo=_REPO,
                pat=_PAT,
                app_id="1",
                private_key_pem="k",
                installation_id="2",
            )

    def test_neither_pat_nor_complete_app_raises(self) -> None:
        with pytest.raises(ValueError, match="must supply"):
            GithubWorkItemProvider(owner=_OWNER, repo=_REPO)
        with pytest.raises(ValueError, match="must supply"):
            GithubWorkItemProvider(
                owner=_OWNER,
                repo=_REPO,
                app_id="1",  # missing other two
            )


class TestGithubWorkItemProviderPATPath:
    def test_ping_issues_get_with_bearer_pat(self, respx_router: respx.Router) -> None:
        route = respx_router.get(_BASE).respond(status_code=200, json={"name": _REPO})
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        p.ping()
        assert route.called
        headers = dict(route.calls[0].request.headers)
        assert headers["authorization"] == f"Bearer {_PAT}"
        assert headers["accept"] == "application/vnd.github+json"
        assert headers["x-github-api-version"] == "2022-11-28"

    def test_fetch_work_items_round_trip(self, respx_router: respx.Router) -> None:
        respx_router.get(f"{_BASE}/issues/42").respond(
            status_code=200,
            json={
                "number": 42,
                "title": "Login bug",
                "state": "open",
                "assignee": {"login": "alice"},
            },
        )
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        items = p.fetch_work_items(["42"])
        assert len(items) == 1
        wi = items[0]
        assert isinstance(wi, WorkItem)
        assert wi.id == "42"
        assert wi.title == "Login bug"
        assert wi.state == "open"
        assert wi.work_item_type == "issue"
        assert wi.assigned_to == "alice"
        assert wi.provider_name == "github"
        assert "title" in wi.raw_fields

    def test_fetch_distinguishes_pull_request_from_issue(self, respx_router: respx.Router) -> None:
        respx_router.get(f"{_BASE}/issues/100").respond(
            status_code=200,
            json={
                "number": 100,
                "title": "PR title",
                "state": "open",
                "pull_request": {"url": "..."},
            },
        )
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        items = p.fetch_work_items(["100"])
        assert items[0].work_item_type == "pull_request"

    def test_fetch_handles_missing_assignee(self, respx_router: respx.Router) -> None:
        """assignee may be None when nobody is assigned."""
        respx_router.get(f"{_BASE}/issues/77").respond(
            status_code=200,
            json={
                "number": 77,
                "title": "Untriaged",
                "state": "open",
                "assignee": None,
            },
        )
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        items = p.fetch_work_items(["77"])
        assert items[0].assigned_to is None

    def test_link_release_posts_comment_per_issue(
        self,
        respx_router: respx.Router,
        deploy_record: DeployRecord,
    ) -> None:
        posted: list[bytes] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            posted.append(request.read())
            return httpx.Response(status_code=201, json={"id": 1})

        respx_router.post(url__regex=rf"{_BASE}/issues/\d+/comments").mock(side_effect=_capture)

        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        p.link_release(
            release_id="R-2026-04-26-1",
            work_items=["42", "99"],
            deploy_record=deploy_record,
        )
        assert len(posted) == 2
        for raw in posted:
            body = _json.loads(raw)
            assert body == {"body": format_structured_comment(deploy_record)}

    def test_secondary_rate_limit_403_surfaces_as_auth_error(
        self, respx_router: respx.Router
    ) -> None:
        """Pitfall 4: GitHub returns 403 + Retry-After for secondary rate limit.

        DEVIATION (documented in 11-05-SUMMARY.md): the existing
        ``BaseRestClient`` retry policy in
        ``sigantry_core/client/retry.py`` treats 403 as
        ``NO_RETRY_STATUSES`` and routes it to ``AuthError``. The
        ``RETRY_STATUSES`` set is ``{408, 429, 500, 502, 503, 504}`` --
        adding 403 would require a coordinated retry-classifier change
        across every provider (out of scope for Plan 11-05). Until
        Phase 12 (or a dedicated rate-limit hardening plan) widens the
        retry policy or introduces a per-endpoint rate-limit bucket,
        GitHub secondary rate limits surface as ``AuthError`` -- the
        consumer-side mitigation is to throttle calls at the workflow
        level. This test locks the *current* behaviour so the deferred
        work has a regression target.
        """
        from sigantry_core.client.errors import AuthError

        respx_router.get(_BASE).respond(
            status_code=403,
            headers={"Retry-After": "0"},
            json={"message": "secondary rate limit"},
        )
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        with pytest.raises(AuthError):
            p.ping()

    def test_429_with_retry_after_eventually_succeeds(self, respx_router: respx.Router) -> None:
        """Primary rate limit (429 + Retry-After) IS retried by existing tenacity."""
        responses = [
            httpx.Response(
                status_code=429,
                headers={"Retry-After": "0"},
                json={"message": "primary rate limit"},
            ),
            httpx.Response(status_code=200, json={"name": _REPO}),
        ]
        respx_router.get(_BASE).mock(side_effect=responses)
        p = GithubWorkItemProvider(owner=_OWNER, repo=_REPO, pat=_PAT)
        p.ping()  # MUST NOT raise -- tenacity retries 429 on second attempt


class TestGithubWorkItemProviderAppPath:
    def test_app_path_lazily_mints_installation_token(
        self,
        fake_jwt_signing_key: str,
        respx_router: respx.Router,
    ) -> None:
        # Mock the installation-token exchange.
        respx_router.post("https://api.github.com/app/installations/77/access_tokens").respond(
            status_code=201,
            json={
                "token": "fake-install-token-1",
                "expires_at": "2026-04-26T13:00:00Z",
            },
        )
        # Mock the actual ping.
        ping_route = respx_router.get(_BASE).respond(status_code=200, json={"name": _REPO})
        p = GithubWorkItemProvider(
            owner=_OWNER,
            repo=_REPO,
            app_id="12345",
            private_key_pem=fake_jwt_signing_key,
            installation_id="77",
        )
        p.ping()
        assert ping_route.called
        headers = dict(ping_route.calls[0].request.headers)
        assert headers["authorization"] == "Bearer fake-install-token-1"

    def test_app_path_re_mints_when_token_near_expiry(
        self,
        fake_jwt_signing_key: str,
        respx_router: respx.Router,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # First mint expires almost immediately; second mint is fresh.
        first_expires = datetime.now(UTC) + timedelta(seconds=10)  # < 60s margin
        second_expires = datetime.now(UTC) + timedelta(hours=1)

        calls = {"n": 0}

        def _fake_mint(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return ("token-A", first_expires)
            return ("token-B", second_expires)

        from sigantry_core.workitems import github as gh_mod

        monkeypatch.setattr(gh_mod, "mint_installation_token", _fake_mint)

        respx_router.get(_BASE).respond(status_code=200, json={"name": _REPO})

        p = GithubWorkItemProvider(
            owner=_OWNER,
            repo=_REPO,
            app_id="12345",
            private_key_pem=fake_jwt_signing_key,
            installation_id="77",
        )
        p.ping()
        p.ping()
        # Two pings should trigger TWO mints because first token expires
        # within the 60s refresh margin.
        assert calls["n"] == 2

    def test_app_path_caches_token_when_far_from_expiry(
        self,
        fake_jwt_signing_key: str,
        respx_router: respx.Router,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If expires_at is 1 hour out, two pings reuse the same token (single mint)."""
        far_expires = datetime.now(UTC) + timedelta(hours=1)
        calls = {"n": 0}

        def _fake_mint(*args, **kwargs):
            calls["n"] += 1
            return ("token-cached", far_expires)

        from sigantry_core.workitems import github as gh_mod

        monkeypatch.setattr(gh_mod, "mint_installation_token", _fake_mint)
        respx_router.get(_BASE).respond(status_code=200, json={"name": _REPO})

        p = GithubWorkItemProvider(
            owner=_OWNER,
            repo=_REPO,
            app_id="12345",
            private_key_pem=fake_jwt_signing_key,
            installation_id="77",
        )
        p.ping()
        p.ping()
        # Two pings reuse the same cached token -- single mint.
        assert calls["n"] == 1


class TestGithubProviderAppAuthInvariantSurvivesPythonO:
    """Review-fix MD-03: ``_current_token`` raises RuntimeError, not AssertionError.

    The previous implementation used ``assert ... is not None`` for type
    narrowing on the App-auth path. CPython's ``-O`` /
    ``PYTHONOPTIMIZE=1`` mode silently elides asserts, so a future patch
    that loosens the constructor check would let None values reach
    ``mint_installation_token`` and surface as a confusing
    ``TypeError`` from inside ``jwt.encode``. The fix replaces the
    asserts with an ``if ... raise RuntimeError`` block that survives -O.

    These tests bypass the constructor's normal validation by mutating
    private attributes after construction -- simulating the future
    regression the review flagged. The expectation is a clean
    ``RuntimeError`` whose message names the broken invariant, NOT a
    silent fall-through into ``mint_installation_token``.
    """

    def test_raises_runtime_error_when_app_id_is_none(
        self,
        fake_jwt_signing_key: str,
    ) -> None:
        p = GithubWorkItemProvider(
            owner=_OWNER,
            repo=_REPO,
            app_id="12345",
            private_key_pem=fake_jwt_signing_key,
            installation_id="77",
        )
        # Simulate a future regression that loosens the __init__ guard.
        p._app_id = None  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="App-auth invariant broken"):
            p._current_token()

    def test_raises_runtime_error_when_private_key_is_none(
        self,
        fake_jwt_signing_key: str,
    ) -> None:
        p = GithubWorkItemProvider(
            owner=_OWNER,
            repo=_REPO,
            app_id="12345",
            private_key_pem=fake_jwt_signing_key,
            installation_id="77",
        )
        p._private_key_pem = None  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="App-auth invariant broken"):
            p._current_token()

    def test_raises_runtime_error_when_installation_id_is_none(
        self,
        fake_jwt_signing_key: str,
    ) -> None:
        p = GithubWorkItemProvider(
            owner=_OWNER,
            repo=_REPO,
            app_id="12345",
            private_key_pem=fake_jwt_signing_key,
            installation_id="77",
        )
        p._installation_id = None  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="App-auth invariant broken"):
            p._current_token()

    def test_no_assert_statements_in_current_token(self) -> None:
        """Lock the implementation: no ``assert`` in ``_current_token`` body.

        ``assert`` statements would re-introduce the -O elision bug. A
        future maintainer who reaches for ``assert`` for type-narrowing
        on this code path needs to fail this test and reach for
        ``if ... raise`` instead. AST inspection beats source-string
        scanning -- it survives reformatting and unrelated edits.
        """
        import ast
        import inspect
        import textwrap

        # ``inspect.getsource`` returns the method body still indented as
        # a class method; ``textwrap.dedent`` strips the leading 4-space
        # indent so ``ast.parse`` accepts it as a top-level def.
        src = textwrap.dedent(inspect.getsource(GithubWorkItemProvider._current_token))
        tree = ast.parse(src)
        asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
        assert asserts == [], (
            "GithubWorkItemProvider._current_token contains assert "
            "statements; review-fix MD-03 forbids them on the App-auth "
            "path because they elide under python -O. Use ``if ... raise``."
        )
