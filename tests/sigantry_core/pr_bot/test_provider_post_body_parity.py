"""Cross-provider POST-body byte-identical parity test (Plan 14-05 Task 4).

Closes the runtime half of STARTER-07: drives both ``GithubProvider`` and
``AdoProvider`` via respx mocks, JSON-decodes the captured POST bodies, and
asserts that the comment-text strings inside the two different REST envelopes
are byte-identical AND byte-identical to ``render_markdown(payload)``.

REST envelope keys legitimately differ between providers:

- GitHub: ``{"body": <text>}``
- ADO:    ``{"comments": [{"content": <text>, "commentType": 1, ...}],
             "status": 1}``

The STARTER-07 invariant lives on the COMMENT TEXT, not the envelope.
Both providers post the byte-identical output of
:func:`sigantry_core.pr_bot.payload.render_markdown` -- the renderer is
the single source of truth for the bytes that hit the wire.

This file is the structural mirror of
``tests/release/test_comment_parity.py:test_cross_provider_post_body_text_byte_identical``
(Phase 11 TRACE-06).
"""

from __future__ import annotations

import importlib.util
import json as _json
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth import TokenProvider
from sigantry_core.pr_bot.payload import render_markdown
from sigantry_core.pr_bot.providers import AdoProvider, GithubProvider

_OWNER = "sigantry"
_REPO = "sigantry-test-repo"
_ORG = "sigantry-test"
_PROJECT = "sigantry-starter-test"
_REPO_ID = "00000000-0000-0000-0000-000000000aaa"
_PR_ID = "42"

_GH_URL = f"https://api.github.com/repos/{_OWNER}/{_REPO}/issues/{_PR_ID}/comments"
_ADO_URL_REGEX = (
    rf"https://dev\.azure\.com/{_ORG}/{_PROJECT}/_apis/git/repositories/"
    rf"{_REPO_ID}/pullRequests/{_PR_ID}/threads.*"
)


def _load_pr_bot_conftest() -> ModuleType:
    """Load tests/sigantry_core/pr_bot/conftest.py by file path.

    Reuses the same idiom used by Plan 14-02's test_payload_parity.py and
    Plan 14-05's per-provider test files -- the repo lacks a top-level
    ``tests/__init__.py`` (Phase 13 idiom) so module-path imports under
    ``tests.sigantry_core.*`` do not resolve.
    """
    conftest_path = Path(__file__).resolve().parent / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "_pr_bot_conftest_for_post_body_parity", conftest_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CONFTEST = _load_pr_bot_conftest()
deterministic_both_payload = _CONFTEST.deterministic_both_payload
deterministic_tmdl_only_payload = _CONFTEST.deterministic_tmdl_only_payload
deterministic_lakehouse_only_payload = _CONFTEST.deterministic_lakehouse_only_payload
deterministic_no_changes_payload = _CONFTEST.deterministic_no_changes_payload


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


def _stub_token_provider() -> MagicMock:
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "fake-aad-token"
    mp.last_credential_class.return_value = "MockCredential"
    mp.tenant_id = "test-tenant-id"
    return mp


def _drive_both_providers(rendered_body: str) -> tuple[dict, dict]:
    """Drive both providers with ``rendered_body`` and return their JSON-decoded POST bodies.

    Returns ``(gh_body, ado_body)`` -- the JSON-decoded request bodies
    captured at the respx transport layer for the GitHub and ADO POST
    requests respectively.
    """
    gh_captured: list[bytes] = []
    ado_captured: list[bytes] = []

    def _capture_gh(request: httpx.Request) -> httpx.Response:
        gh_captured.append(request.read())
        return httpx.Response(status_code=201, json={"id": 999})

    def _capture_ado(request: httpx.Request) -> httpx.Response:
        ado_captured.append(request.read())
        return httpx.Response(status_code=201, json={"id": 100})

    with respx.mock(assert_all_called=False) as router:
        router.post(_GH_URL).mock(side_effect=_capture_gh)
        router.post(url__regex=_ADO_URL_REGEX).mock(side_effect=_capture_ado)

        gh = GithubProvider(_OWNER, _REPO, pat="fake-pat-test")
        gh.post_comment(_PR_ID, rendered_body)

        ado = AdoProvider(
            org=_ORG,
            project=_PROJECT,
            repository_id=_REPO_ID,
            token_provider=_stub_token_provider(),
        )
        ado.post_comment(_PR_ID, rendered_body)

    assert len(gh_captured) == 1, "GitHub post_comment must POST exactly one request"
    assert len(ado_captured) == 1, "ADO post_comment must POST exactly one request"

    gh_body = _json.loads(gh_captured[0])
    ado_body = _json.loads(ado_captured[0])
    return gh_body, ado_body


def test_cross_provider_post_body_text_byte_identical() -> None:
    """STARTER-07 runtime invariant: ADO comments[0].content == GitHub body == render_markdown(payload).

    Drives both providers via respx mocks for the deterministic both-diff
    payload; captures POST bodies; asserts the three byte-equalities at
    once. Mirrors Phase 11 ``test_cross_provider_post_body_text_byte_identical``
    (TRACE-06).
    """
    payload = deterministic_both_payload()
    expected = render_markdown(payload)

    gh_body, ado_body = _drive_both_providers(expected)

    # Three byte-equality assertions:
    assert gh_body["body"] == expected, (
        "STARTER-07 violation: GitHub body diverged from render_markdown(payload)."
    )
    assert ado_body["comments"][0]["content"] == expected, (
        "STARTER-07 violation: ADO comments[0].content diverged from render_markdown(payload)."
    )
    # Cross-provider byte-identical assertion (the STARTER-07 invariant proper):
    assert gh_body["body"] == ado_body["comments"][0]["content"], (
        "STARTER-07 violation: GitHub body and ADO content diverged. "
        "Both providers MUST post the byte-identical output of "
        "sigantry_core.pr_bot.payload.render_markdown."
        f"\nGitHub: {gh_body['body']!r}"
        f"\nADO:    {ado_body['comments'][0]['content']!r}"
    )

    # Bonus: lock the ADO commentType=1 numeric invariant (RESEARCH §Pitfall 4).
    assert ado_body["comments"][0]["commentType"] == 1
    assert isinstance(ado_body["comments"][0]["commentType"], int)
    assert not isinstance(ado_body["comments"][0]["commentType"], bool)


def test_post_body_parity_for_all_four_payload_scenarios() -> None:
    """Same invariant holds for every deterministic factory: tmdl_only, lakehouse_only, both, no_changes.

    Locks the parity property across the full Cartesian product of
    diff-section combinations -- a regression in the renderer (or in
    either provider's envelope wrapping) on any of the four scenarios
    surfaces as a single failing assertion.
    """
    factories = [
        ("tmdl_only", deterministic_tmdl_only_payload),
        ("lakehouse_only", deterministic_lakehouse_only_payload),
        ("both", deterministic_both_payload),
        ("no_changes", deterministic_no_changes_payload),
    ]

    for name, factory in factories:
        payload = factory()
        expected = render_markdown(payload)
        gh_body, ado_body = _drive_both_providers(expected)
        assert gh_body["body"] == expected, f"GitHub body drift for scenario={name}"
        assert ado_body["comments"][0]["content"] == expected, (
            f"ADO content drift for scenario={name}"
        )
        assert gh_body["body"] == ado_body["comments"][0]["content"], (
            f"Cross-provider byte-identical violation for scenario={name}"
        )
