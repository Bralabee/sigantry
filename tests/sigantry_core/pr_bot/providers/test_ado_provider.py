"""Unit tests for :class:`sigantry_core.pr_bot.providers.ado.AdoProvider`.

Eight tests covering the four Provider methods + the api-version /
commentType invariants:

1. test_ado_ping_success                     -- ping happy path + AAD scope.
2. test_ado_ping_unauthorised_raises         -- ping 401 -> AuthError.
3. test_ado_get_pr_happy_path                -- get_pr returns PullRequest.
4. test_ado_get_changed_files_via_iteration  -- 2-call iteration sequence.
5. test_ado_post_comment_success             -- post_comment returns thread id.
6. test_ado_post_comment_uses_api_version_71 -- RESEARCH §Pitfall 5 lock.
7. test_ado_post_comment_body_uses_numeric_commentType_1 -- §Pitfall 4 lock.
8. test_ado_post_comment_body_is_render_markdown_output  -- pre-stages the
   cross-provider parity test in Plan 14-05 Task 4.
"""

from __future__ import annotations

import importlib.util
import json as _json
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import AZURE_DEVOPS_SCOPE
from sigantry_core.pr_bot.payload import render_markdown
from sigantry_core.pr_bot.providers.ado import AdoProvider
from sigantry_core.pr_bot.providers.base import ChangedFile, Provider, PullRequest

_ORG = "sigantry-test"
_PROJECT = "sigantry-starter-test"
_REPO_ID = "00000000-0000-0000-0000-000000000aaa"
_BASE = f"https://dev.azure.com/{_ORG}"
_PR_ID = "42"
_PR_PATH = f"{_BASE}/{_PROJECT}/_apis/git/repositories/{_REPO_ID}/pullRequests/{_PR_ID}"


def _load_pr_bot_conftest() -> ModuleType:
    """Load the parent ``tests/sigantry_core/pr_bot/conftest.py`` by file path."""
    conftest_path = Path(__file__).resolve().parent.parent / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "_pr_bot_conftest_for_ado_provider", conftest_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PARENT_CONFTEST = _load_pr_bot_conftest()
deterministic_both_payload = _PARENT_CONFTEST.deterministic_both_payload


@pytest.fixture
def mock_token_provider() -> MagicMock:
    """MagicMock(spec=TokenProvider) returning a deterministic AAD token."""
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "fake-aad-token"
    mp.last_credential_class.return_value = "MockCredential"
    mp.tenant_id = "test-tenant-id"
    return mp


@pytest.fixture
def respx_router():
    """respx router for mocking httpx at the transport layer."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def _reset_correlation_context():
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
    try:
        from sigantry_core.client.rate_limit import reset_buckets
    except ImportError:
        yield
        return
    reset_buckets()
    yield
    reset_buckets()


def _provider(token_provider: MagicMock) -> AdoProvider:
    return AdoProvider(
        org=_ORG,
        project=_PROJECT,
        repository_id=_REPO_ID,
        token_provider=token_provider,
    )


# ---------------------------------------------------------------------------
# Construction smoke (pins Protocol conformance)
# ---------------------------------------------------------------------------


def test_ado_provider_construction_and_protocol(mock_token_provider: MagicMock) -> None:
    p = _provider(mock_token_provider)
    assert p.name == "ado"
    assert isinstance(p, Provider)


# ---------------------------------------------------------------------------
# Tests 1 + 2 -- ping
# ---------------------------------------------------------------------------


def test_ado_ping_success(respx_router: respx.Router, mock_token_provider: MagicMock) -> None:
    route = respx_router.get(
        f"{_BASE}/{_PROJECT}/_apis/projects/{_PROJECT}",
        params={"api-version": "7.1"},
    ).respond(status_code=200, json={"id": "abc", "name": _PROJECT})

    p = _provider(mock_token_provider)
    p.ping()
    assert route.called
    mock_token_provider.get_token.assert_called_with(AZURE_DEVOPS_SCOPE)


def test_ado_ping_unauthorised_raises(
    respx_router: respx.Router, mock_token_provider: MagicMock
) -> None:
    from sigantry_core.client.errors import AuthError

    respx_router.get(
        f"{_BASE}/{_PROJECT}/_apis/projects/{_PROJECT}",
        params={"api-version": "7.1"},
    ).respond(status_code=401, json={"message": "Unauthorized"})
    p = _provider(mock_token_provider)
    with pytest.raises(AuthError):
        p.ping()


# ---------------------------------------------------------------------------
# Test 3 -- get_pr
# ---------------------------------------------------------------------------


def test_ado_get_pr_happy_path(respx_router: respx.Router, mock_token_provider: MagicMock) -> None:
    respx_router.get(_PR_PATH, params={"api-version": "7.1"}).respond(
        status_code=200,
        json={
            "pullRequestId": int(_PR_ID),
            "title": "Add feature X",
            "targetRefName": "refs/heads/main",
            "sourceRefName": "refs/heads/feature/x",
            "lastMergeTargetCommit": {"commitId": "aaaa1111"},
            "lastMergeSourceCommit": {"commitId": "bbbb2222"},
        },
    )
    p = _provider(mock_token_provider)
    pr = p.get_pr(_PR_ID)
    assert isinstance(pr, PullRequest)
    assert pr.id == _PR_ID
    assert pr.title == "Add feature X"
    assert pr.base_ref == "main"
    assert pr.head_ref == "feature/x"
    assert pr.base_sha == "aaaa1111"
    assert pr.head_sha == "bbbb2222"
    assert pr.provider_name == "ado"


# ---------------------------------------------------------------------------
# Test 4 -- get_changed_files
# ---------------------------------------------------------------------------


def test_ado_get_changed_files_via_iteration(
    respx_router: respx.Router, mock_token_provider: MagicMock
) -> None:
    """Exercises the 2-call iterations + changes sequence."""
    respx_router.get(f"{_PR_PATH}/iterations", params={"api-version": "7.1"}).respond(
        status_code=200,
        json={"value": [{"id": 1, "createdDate": "2026-04-28T00:00:00Z"}]},
    )
    respx_router.get(f"{_PR_PATH}/iterations/1/changes", params={"api-version": "7.1"}).respond(
        status_code=200,
        json={
            "changeEntries": [
                {
                    "changeType": "edit",
                    "item": {"path": "/Sales.tmdl"},
                },
                {
                    "changeType": "rename",
                    "item": {
                        "path": "/Renamed.tmdl",
                        "sourceServerItem": "/Old.tmdl",
                    },
                },
            ]
        },
    )

    p = _provider(mock_token_provider)
    files = p.get_changed_files(_PR_ID)

    assert len(files) == 2
    assert isinstance(files[0], ChangedFile)
    assert files[0].path == "/Sales.tmdl"
    assert files[0].change_type == "modified"
    assert files[1].path == "/Renamed.tmdl"
    assert files[1].change_type == "renamed"
    assert files[1].previous_path == "/Old.tmdl"


# ---------------------------------------------------------------------------
# Test 4b -- regression: multi-flag changeType semantic priority (MD-01)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change_type", "expected"),
    [
        # The motivating case from REVIEW MD-01: delete must win over rename
        # even when it appears AFTER another flag in the comma-separated list.
        ("sourceRename, delete", "removed"),
        # Symmetric case: comma order shouldn't matter.
        ("delete, sourceRename", "removed"),
        # rename + edit -> renamed (rename outranks edit).
        ("edit, rename", "renamed"),
        # sourceRename alone normalises to renamed.
        ("sourceRename", "renamed"),
        # add + edit -> added (add outranks edit).
        ("edit, add", "added"),
        # Mixed-case input with whitespace tokens still resolves correctly.
        ("  Delete , SourceRename  ", "removed"),
        # Plain edit stays edit (sanity baseline).
        ("edit", "modified"),
    ],
)
def test_ado_get_changed_files_multi_flag_change_type(
    respx_router: respx.Router,
    mock_token_provider: MagicMock,
    change_type: str,
    expected: str,
) -> None:
    """REVIEW 2026-04-28 MD-01 regression.

    Multi-flag ADO ``changeType`` strings (e.g. ``"sourceRename, delete"``)
    must apply semantic priority (delete > add > rename > edit) instead of
    first-token-wins. A delete that appears second in the comma-separated
    list MUST classify as ``"removed"``, not ``"renamed"``.
    """
    respx_router.get(f"{_PR_PATH}/iterations", params={"api-version": "7.1"}).respond(
        status_code=200,
        json={"value": [{"id": 1, "createdDate": "2026-04-28T00:00:00Z"}]},
    )
    respx_router.get(f"{_PR_PATH}/iterations/1/changes", params={"api-version": "7.1"}).respond(
        status_code=200,
        json={
            "changeEntries": [
                {
                    "changeType": change_type,
                    "item": {"path": "/Sales.tmdl"},
                },
            ]
        },
    )

    p = _provider(mock_token_provider)
    files = p.get_changed_files(_PR_ID)

    assert len(files) == 1
    assert files[0].change_type == expected


# ---------------------------------------------------------------------------
# Tests 5 + 6 + 7 + 8 -- post_comment
# ---------------------------------------------------------------------------


def test_ado_post_comment_success(
    respx_router: respx.Router, mock_token_provider: MagicMock
) -> None:
    posted: list[bytes] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        posted.append(request.read())
        return httpx.Response(status_code=201, json={"id": 100})

    respx_router.post(url__regex=rf"{_PR_PATH}/threads.*").mock(side_effect=_capture)

    p = _provider(mock_token_provider)
    thread_id = p.post_comment(_PR_ID, "test body")

    assert thread_id == "100"
    assert len(posted) == 1
    captured = _json.loads(posted[0])
    assert captured == {
        "comments": [{"parentCommentId": 0, "content": "test body", "commentType": 1}],
        "status": 1,
    }


def test_ado_post_comment_uses_api_version_71(
    respx_router: respx.Router, mock_token_provider: MagicMock
) -> None:
    """RESEARCH §Pitfall 5 -- regression catcher for api-version drift."""
    captured_params: list[dict[str, Any]] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured_params.append(dict(request.url.params))
        return httpx.Response(status_code=201, json={"id": 1})

    respx_router.post(url__regex=rf"{_PR_PATH}/threads.*").mock(side_effect=_capture)

    p = _provider(mock_token_provider)
    p.post_comment(_PR_ID, "body")
    assert captured_params == [{"api-version": "7.1"}]


def test_ado_post_comment_body_uses_numeric_commentType_1(  # noqa: N802 -- mirrors the ADO REST field name (commentType) per RESEARCH §Pitfall 4
    respx_router: respx.Router, mock_token_provider: MagicMock
) -> None:
    """RESEARCH §Pitfall 4 -- commentType is numeric 1 on input, NOT the string 'text'."""
    posted: list[bytes] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        posted.append(request.read())
        return httpx.Response(status_code=201, json={"id": 1, "commentType": "text"})

    respx_router.post(url__regex=rf"{_PR_PATH}/threads.*").mock(side_effect=_capture)

    p = _provider(mock_token_provider)
    p.post_comment(_PR_ID, "body")

    captured = _json.loads(posted[0])
    comment_type = captured["comments"][0]["commentType"]
    assert comment_type == 1
    assert isinstance(comment_type, int)
    assert not isinstance(comment_type, bool)
    # Also lock the status field.
    assert captured["status"] == 1
    assert isinstance(captured["status"], int)


def test_ado_post_comment_body_is_render_markdown_output(
    respx_router: respx.Router, mock_token_provider: MagicMock
) -> None:
    """Pre-stages STARTER-07 parity: ADO captured comment content == render_markdown(payload)."""
    posted: list[bytes] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        posted.append(request.read())
        return httpx.Response(status_code=201, json={"id": 1})

    respx_router.post(url__regex=rf"{_PR_PATH}/threads.*").mock(side_effect=_capture)

    payload = deterministic_both_payload()
    rendered = render_markdown(payload)

    p = _provider(mock_token_provider)
    p.post_comment(_PR_ID, rendered)

    assert len(posted) == 1
    captured = _json.loads(posted[0])
    assert captured["comments"][0]["content"] == rendered
