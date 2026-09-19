"""Unit tests for :class:`sigantry_core.pr_bot.providers.github.GithubProvider`.

Eight tests covering the four Provider methods:

1. test_github_ping_success                     -- ping happy path + headers.
2. test_github_ping_unauthorised_raises         -- ping 401 -> AuthError.
3. test_github_get_pr_happy_path                -- get_pr returns PullRequest.
4. test_github_get_pr_not_found                 -- get_pr 404 -> NotFoundError.
5. test_github_get_changed_files_paginates      -- get_changed_files paginates.
6. test_github_get_changed_files_change_type_normalised -- renamed mapping.
7. test_github_post_comment_success             -- post_comment body shape.
8. test_github_post_comment_body_is_render_markdown_output -- pre-stages the
   cross-provider parity test in Plan 14-05 Task 4.

Mirrors the pattern from
``tests/sigantry_core/workitems/test_github_provider.py`` (Phase 11).
"""

from __future__ import annotations

import importlib.util
import json as _json
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

from sigantry_core.pr_bot.payload import render_markdown
from sigantry_core.pr_bot.providers.base import ChangedFile, Provider, PullRequest
from sigantry_core.pr_bot.providers.github import GithubProvider

_OWNER = "sigantry"
_REPO = "sigantry-test-repo"
_BASE = f"https://api.github.com/repos/{_OWNER}/{_REPO}"
_PAT = "fake-pat-test-pat-value"
_PR_ID = "42"


def _load_pr_bot_conftest() -> ModuleType:
    """Load the parent ``tests/sigantry_core/pr_bot/conftest.py`` by file path.

    The repo does not ship a top-level ``tests/__init__.py`` (Phase 13 idiom;
    see Plan 14-02 deviation 1); ``importlib.util`` is the established
    workaround for sharing conftest helpers across nested test modules.
    """
    conftest_path = Path(__file__).resolve().parent.parent / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "_pr_bot_conftest_for_github_provider", conftest_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PARENT_CONFTEST = _load_pr_bot_conftest()
deterministic_both_payload = _PARENT_CONFTEST.deterministic_both_payload


@pytest.fixture
def respx_router():
    """respx router for mocking httpx at the transport layer."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def _reset_correlation_context():
    """Prevent correlation-id leak across tests (workitems-conftest pattern)."""
    try:
        from sigantry_core.client import logging as client_logging
    except ImportError:
        yield
        return
    client_logging._CORRELATION_ID.set(None)
    client_logging._OPERATION_ID.set(None)
    yield
    client_logging._CORRELATION_ID.set(None)
    client_logging._OPERATION_ID.set(None)


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    """Prevent pyrate-limiter bucket state leaking across tests."""
    try:
        from sigantry_core.client.rate_limit import reset_buckets
    except ImportError:
        yield
        return
    reset_buckets()
    yield
    reset_buckets()


# ---------------------------------------------------------------------------
# Construction smoke (not in the 8-test count, but pins the seam)
# ---------------------------------------------------------------------------


def test_github_provider_construction_pat_and_protocol() -> None:
    """PAT-only construction succeeds + isinstance(Provider) holds."""
    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    assert p.name == "github"
    assert isinstance(p, Provider)


def test_github_provider_construction_rejects_pat_plus_app() -> None:
    with pytest.raises(ValueError, match="not both"):
        GithubProvider(
            _OWNER,
            _REPO,
            pat=_PAT,
            app_id="1",
            private_key_pem="k",
            installation_id="2",
        )


def test_github_provider_construction_rejects_neither() -> None:
    with pytest.raises(ValueError, match="must supply"):
        GithubProvider(_OWNER, _REPO)


# ---------------------------------------------------------------------------
# Tests 1 + 2 -- ping
# ---------------------------------------------------------------------------


def test_github_ping_success(respx_router: respx.Router) -> None:
    route = respx_router.get(_BASE).respond(status_code=200, json={"name": _REPO})
    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    p.ping()
    assert route.called
    headers = dict(route.calls[0].request.headers)
    assert headers["authorization"] == f"Bearer {_PAT}"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"


def test_github_ping_unauthorised_raises(respx_router: respx.Router) -> None:
    from sigantry_core.client.errors import AuthError

    respx_router.get(_BASE).respond(status_code=401, json={"message": "Bad credentials"})
    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    with pytest.raises(AuthError):
        p.ping()


# ---------------------------------------------------------------------------
# Tests 3 + 4 -- get_pr
# ---------------------------------------------------------------------------


def test_github_get_pr_happy_path(respx_router: respx.Router) -> None:
    respx_router.get(f"{_BASE}/pulls/{_PR_ID}").respond(
        status_code=200,
        json={
            "number": int(_PR_ID),
            "title": "Add feature X",
            "base": {"ref": "main", "sha": "aaaa1111"},
            "head": {"ref": "feature/x", "sha": "bbbb2222"},
        },
    )
    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    pr = p.get_pr(_PR_ID)
    assert isinstance(pr, PullRequest)
    assert pr.id == _PR_ID
    assert pr.title == "Add feature X"
    assert pr.base_ref == "main"
    assert pr.head_ref == "feature/x"
    assert pr.base_sha == "aaaa1111"
    assert pr.head_sha == "bbbb2222"
    assert pr.provider_name == "github"


def test_github_get_pr_not_found(respx_router: respx.Router) -> None:
    from sigantry_core.client.errors import NotFoundError

    respx_router.get(f"{_BASE}/pulls/{_PR_ID}").respond(
        status_code=404, json={"message": "Not Found"}
    )
    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    with pytest.raises(NotFoundError):
        p.get_pr(_PR_ID)


# ---------------------------------------------------------------------------
# Tests 5 + 6 -- get_changed_files
# ---------------------------------------------------------------------------


def test_github_get_changed_files_paginates(respx_router: respx.Router) -> None:
    """30 entries on page 1, 1 entry on page 2 -- second page short-circuits."""
    page_1 = [{"filename": f"file_{i:03d}.tmdl", "status": "modified"} for i in range(30)]
    page_2 = [{"filename": "file_030.tmdl", "status": "added"}]

    routes_called: list[str] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        routes_called.append(str(page))
        if page == "1":
            return httpx.Response(status_code=200, json=page_1)
        if page == "2":
            return httpx.Response(status_code=200, json=page_2)
        return httpx.Response(status_code=200, json=[])

    respx_router.get(url__regex=rf"{_BASE}/pulls/{_PR_ID}/files.*").mock(side_effect=_capture)

    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    files = p.get_changed_files(_PR_ID)

    assert len(files) == 31
    assert routes_called == ["1", "2"]
    assert files[0].path == "file_000.tmdl"
    assert files[30].change_type == "added"


def test_github_get_changed_files_change_type_normalised(
    respx_router: respx.Router,
) -> None:
    """A renamed entry preserves previous_filename + maps change_type=renamed."""
    respx_router.get(url__regex=rf"{_BASE}/pulls/{_PR_ID}/files.*").respond(
        status_code=200,
        json=[
            {
                "filename": "Sales.New.tmdl",
                "status": "renamed",
                "previous_filename": "Sales.Old.tmdl",
            }
        ],
    )
    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    files = p.get_changed_files(_PR_ID)
    assert len(files) == 1
    assert isinstance(files[0], ChangedFile)
    assert files[0].path == "Sales.New.tmdl"
    assert files[0].change_type == "renamed"
    assert files[0].previous_path == "Sales.Old.tmdl"


# ---------------------------------------------------------------------------
# Tests 7 + 8 -- post_comment
# ---------------------------------------------------------------------------


def test_github_post_comment_success(respx_router: respx.Router) -> None:
    posted: list[bytes] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        posted.append(request.read())
        return httpx.Response(status_code=201, json={"id": 999})

    respx_router.post(url__regex=rf"{_BASE}/issues/{_PR_ID}/comments").mock(side_effect=_capture)

    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    comment_id = p.post_comment(_PR_ID, "test body")

    assert comment_id == "999"
    assert len(posted) == 1
    assert _json.loads(posted[0]) == {"body": "test body"}


def test_github_post_comment_body_is_render_markdown_output(
    respx_router: respx.Router,
) -> None:
    """Pre-stages STARTER-07 parity: captured POST body MUST equal render_markdown(payload).

    This invariant is the runtime half of STARTER-07 (the renderer half
    landed in Plan 14-02). Plan 14-05 Task 4 then asserts byte-equality
    BETWEEN the GitHub and ADO captured POST bodies for the same payload.
    """
    posted: list[bytes] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        posted.append(request.read())
        return httpx.Response(status_code=201, json={"id": 1})

    respx_router.post(url__regex=rf"{_BASE}/issues/{_PR_ID}/comments").mock(side_effect=_capture)

    payload = deterministic_both_payload()
    rendered = render_markdown(payload)

    p = GithubProvider(_OWNER, _REPO, pat=_PAT)
    p.post_comment(_PR_ID, rendered)

    assert len(posted) == 1
    captured_body = _json.loads(posted[0])
    assert captured_body == {"body": rendered}
    # Defensive: the byte string inside the envelope is exactly the renderer output.
    assert captured_body["body"] == rendered
