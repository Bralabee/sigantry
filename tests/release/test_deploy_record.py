"""Unit tests for DeployRecord (TRACE-04).

Replaces the Wave 0 xfail stubs with real assertions for Plan 11-02.

Each test exercises one invariant of the deterministic SHA-256 audit hash:
constructive determinism (same input -> same hash), exclusion of the
``audit_hash`` field from its own input, frozen-model immutability, the
``extra='forbid'`` defensive contract, and the millisecond-truncation
datetime determinism (Pitfall 8 in 11-RESEARCH.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from sigantry_core.release.record import DeployRecord


def _build_sample(**overrides: Any) -> DeployRecord:
    defaults: dict[str, Any] = dict(
        workspace="ws-prod",
        release_id="R-2026-04-26-1",
        work_items=["1234", "5678"],
        fabric_items_changed=["nb_silver.Notebook", "lh_gold.Lakehouse"],
        test_evidence={"smoke": "passed", "integration": "passed"},
        approver="alice@example.invalid",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return DeployRecord(**defaults)


def test_with_hash_then_verify_hash_returns_true() -> None:
    record = _build_sample().with_hash()
    assert record.verify_hash() is True
    assert record.audit_hash != ""
    assert len(record.audit_hash) == 64  # SHA-256 hex length


def test_audit_hash_is_deterministic_across_construction() -> None:
    a = _build_sample().with_hash()
    b = _build_sample().with_hash()
    assert a.audit_hash == b.audit_hash


def test_canonical_payload_excludes_audit_hash() -> None:
    """Tampering with audit_hash post-hash MUST be detected by verify_hash().

    If canonical_payload() included the hash field in its input, tampering
    would still yield ``verify_hash() == True`` because the recomputation
    would also include the tampered value. The exclusion is what makes the
    audit detective control work.
    """
    hashed = _build_sample().with_hash()
    tampered = hashed.model_copy(update={"audit_hash": "0" * 64})
    assert tampered.verify_hash() is False


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        DeployRecord(
            workspace="x",
            release_id="x",
            approver="x",
            created_at=datetime(2026, 4, 26, tzinfo=UTC),
            bogus_field="should-fail",
        )


def test_frozen_assignment_raises() -> None:
    record = _build_sample()
    with pytest.raises(ValidationError):
        record.workspace = "different"  # type: ignore[misc]


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        DeployRecord(
            workspace="x",
            release_id="x",
            approver="x",
            created_at=datetime(2026, 4, 26),  # naive
        )


def test_microsecond_truncation_to_millis() -> None:
    """Two records with microseconds in the same millisecond bucket hash equal.

    Pitfall 8 (11-RESEARCH.md line 468): two records constructed with
    microseconds 123456 and 123999 -- both truncate to 123000 -- must hash
    identically so a JSON round-trip cannot break verify_hash().
    """
    t1 = datetime(2026, 4, 26, 12, 0, 0, 123456, tzinfo=UTC)
    t2 = datetime(2026, 4, 26, 12, 0, 0, 123999, tzinfo=UTC)
    a = _build_sample(created_at=t1).with_hash()
    b = _build_sample(created_at=t2).with_hash()
    assert a.audit_hash == b.audit_hash


def test_distinct_inputs_produce_distinct_hashes() -> None:
    base = _build_sample().with_hash()
    variant = _build_sample(release_id="R-2026-04-26-2").with_hash()
    assert base.audit_hash != variant.audit_hash


def test_canonical_payload_returns_bytes_without_audit_hash_key() -> None:
    """canonical_payload() returns UTF-8 bytes; the JSON body must not contain audit_hash."""
    record = _build_sample().with_hash()
    payload = record.canonical_payload()
    assert isinstance(payload, bytes)
    text = payload.decode("utf-8")
    assert '"audit_hash"' not in text
    # Sanity: every other top-level key from the record IS present.
    for key in (
        "approver",
        "created_at",
        "fabric_items_changed",
        "release_id",
        "test_evidence",
        "work_items",
        "workspace",
    ):
        assert f'"{key}"' in text


def test_with_hash_overrides_constructor_supplied_audit_hash() -> None:
    """A constructor-supplied audit_hash MUST be overwritten by .with_hash()."""
    seeded = _build_sample(audit_hash="not-a-real-hash")
    rehashed = seeded.with_hash()
    assert rehashed.audit_hash != "not-a-real-hash"
    assert rehashed.verify_hash() is True
