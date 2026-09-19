"""Comment-payload parity test for TRACE-06.

Both ADO and GitHub providers (Plans 11-04 and 11-05) MUST post the SAME
structured-comment text for the same DeployRecord. Plan 11-02 ships the
shared formatter ``sigantry_core.workitems._payload.format_structured_comment``
plus the ``build_comment_payload`` helper; both providers will import them
unchanged.

Two invariants are locked here:

1. The two golden fixtures ``ado.json`` and ``github.json`` are
   byte-identical (cmp returns 0). Drift in either fixture surfaces in
   CI and is treated as a TRACE-06 violation.
2. The fixtures match the runtime output of
   ``format_structured_comment(deterministic_record)`` byte-for-byte. The
   deterministic record is the canonical sample documented in the plan.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sigantry_core.release.record import DeployRecord
from sigantry_core.workitems._payload import (
    build_comment_payload,
    format_structured_comment,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "comment-parity"


def _deterministic_record() -> DeployRecord:
    """Canonical sample referenced from 11-02-PLAN action 3."""
    return DeployRecord(
        workspace="ws-prod",
        release_id="R-2026-04-26-1",
        work_items=["1234", "5678"],
        fabric_items_changed=[
            "nb_silver_pipeline.Notebook",
            "lh_gold.Lakehouse",
        ],
        test_evidence={"smoke": "passed", "integration": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    ).with_hash()


def test_golden_snapshots_byte_identical() -> None:
    """ADO and GitHub fixtures MUST be byte-identical (TRACE-06 invariant)."""
    ado = (_FIXTURES_DIR / "ado.json").read_bytes()
    gh = (_FIXTURES_DIR / "github.json").read_bytes()
    assert ado == gh, (
        "TRACE-06 invariant: golden parity fixtures diverged. "
        "Recreate from the deterministic record via "
        "format_structured_comment() (see 11-02-PLAN action 3)."
    )


def test_golden_snapshot_matches_runtime_output() -> None:
    """The fixture is the exact runtime output for the deterministic record."""
    text = format_structured_comment(_deterministic_record())
    ado_text = (_FIXTURES_DIR / "ado.json").read_text(encoding="utf-8")
    assert text == ado_text


def test_format_structured_comment_is_pure() -> None:
    """Same input always produces identical output."""
    a = format_structured_comment(_deterministic_record())
    b = format_structured_comment(_deterministic_record())
    assert a == b


def test_payload_keys_are_alphabetically_sorted_in_emitted_text() -> None:
    """Sorted keys are what makes downstream diffs stable across providers."""
    text = format_structured_comment(_deterministic_record())
    inner = text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    parsed = json.loads(inner)
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_payload_keys_are_the_locked_set() -> None:
    """The locked key set is the contract; adding or removing fields is a TRACE-06 break."""
    record = _deterministic_record()
    payload = build_comment_payload(record)
    assert set(payload.keys()) == {
        "approver",
        "audit_hash",
        "created_at",
        "fabric_items_changed_count",
        "release_id",
        "test_evidence",
        "workspace",
    }


def test_audit_hash_appears_in_emitted_comment() -> None:
    """The audit hash and the canonical header are both visible in the comment text."""
    record = _deterministic_record()
    text = format_structured_comment(record)
    assert record.audit_hash in text
    assert "Sigantry release record" in text
    assert "```json" in text


def test_emitted_comment_starts_with_header_then_fence() -> None:
    """The first line is the header; the second is the opening fence."""
    text = format_structured_comment(_deterministic_record())
    lines = text.split("\n")
    assert lines[0] == "Sigantry release record"
    assert lines[1] == "```json"
    assert lines[-1] == "```"


def test_fabric_items_changed_count_matches_record() -> None:
    """The payload exposes the count, NOT the raw list (TRACE-06 minimisation).

    Posting raw item paths into the comment leaks Fabric workspace structure
    into the work-item tracker; only the count is shared.
    """
    record = _deterministic_record()
    payload = build_comment_payload(record)
    assert payload["fabric_items_changed_count"] == len(record.fabric_items_changed)
    assert "fabric_items_changed" not in payload


def test_cross_provider_post_body_text_byte_identical() -> None:
    """End-to-end TRACE-06 lock: ADO and GitHub POST byte-identical comment text.

    Stronger than the formatter-level golden tests above: this captures the
    actual HTTP request bodies that each provider serialises and asserts the
    comment-text bytes are identical. ADO uses ``{"text": "..."}``; GitHub
    uses ``{"body": "..."}``; the JSON KEYS legitimately differ because the
    REST contracts differ. The TRACE-06 invariant is that the COMMENT
    TEXT itself is byte-equal regardless of which provider posts it.

    Captures both requests via the ``respx`` transport-mock context manager
    rather than the workitems-scoped ``respx_router`` fixture, because
    ``tests/release/`` does not inherit ``tests/sigantry_core/workitems/``
    fixtures.
    """
    import json as _json
    from unittest.mock import MagicMock

    import httpx
    import respx

    from sigantry_core.auth import TokenProvider
    from sigantry_core.workitems.ado import AdoWorkItemProvider
    from sigantry_core.workitems.github import GithubWorkItemProvider

    record = _deterministic_record()

    ado_bodies: list[bytes] = []

    def _capture_ado(request: httpx.Request) -> httpx.Response:
        ado_bodies.append(request.read())
        return httpx.Response(status_code=201, json={})

    gh_bodies: list[bytes] = []

    def _capture_gh(request: httpx.Request) -> httpx.Response:
        gh_bodies.append(request.read())
        return httpx.Response(status_code=201, json={})

    with respx.mock(assert_all_called=False) as router:
        router.post(
            url__regex=r"https://dev\.azure\.com/org/proj/_apis/wit/workItems/\d+/comments"
        ).mock(side_effect=_capture_ado)
        router.post(url__regex=r"https://api\.github\.com/repos/org/repo/issues/\d+/comments").mock(
            side_effect=_capture_gh
        )

        # ADO provider with a mocked TokenProvider -- exercise link_release.
        mp = MagicMock(spec=TokenProvider)
        mp.get_token.return_value = "test-token-ado"
        mp.tenant_id = "test-tenant"
        ado = AdoWorkItemProvider(organization="org", project="proj", token_provider=mp)
        ado.link_release(record.release_id, ["1234"], record)

        # GitHub provider via PAT auth -- exercise link_release.
        gh = GithubWorkItemProvider(owner="org", repo="repo", pat="fake-pat-test-pat")
        gh.link_release(record.release_id, ["42"], record)

    assert len(ado_bodies) == 1, "ADO link_release must POST exactly one comment"
    assert len(gh_bodies) == 1, "GitHub link_release must POST exactly one comment"

    ado_payload = _json.loads(ado_bodies[0])
    gh_payload = _json.loads(gh_bodies[0])

    # The two providers use different envelope keys ("text" vs "body") because
    # ADO REST and GitHub REST APIs differ. The TRACE-06 invariant is on the
    # COMMENT TEXT, not the envelope: both providers MUST route their text
    # through ``format_structured_comment`` so the bytes match exactly.
    assert ado_payload["text"] == gh_payload["body"], (
        "TRACE-06 violation: ADO text and GitHub body diverged. "
        "Both providers MUST route their comment text through "
        "sigantry_core.workitems._payload.format_structured_comment. "
        f"\nADO: {ado_payload['text']!r}"
        f"\nGitHub: {gh_payload['body']!r}"
    )

    # Reinforcing assertion: the text matches the formatter output for the
    # same record, locking the chain (record -> formatter -> provider POST).
    expected = format_structured_comment(record)
    assert ado_payload["text"] == expected
    assert gh_payload["body"] == expected
