"""Unit tests for sigantry_core.release.ledger (Plan 12-03 / PIPELINE-04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.ledger import (
    diff_records,
    find_by_release_id,
    iter_records,
)
from sigantry_core.release.record import DeployRecord


def _make_record(
    release_id: str,
    workspace: str = "ws-test",
    items: list[str] | None = None,
) -> DeployRecord:
    return DeployRecord(
        workspace=workspace,
        release_id=release_id,
        work_items=[],
        fabric_items_changed=items if items is not None else ["nb_a.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()


def test_iter_records_yields_in_file_order(tmp_path: Path) -> None:
    emit_deploy_record(_make_record("R1"), audit_dir=tmp_path)
    emit_deploy_record(_make_record("R2"), audit_dir=tmp_path)
    records = list(iter_records(audit_dir=tmp_path))
    assert [r.release_id for r in records] == ["R1", "R2"]


def test_iter_records_skips_malformed_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    emit_deploy_record(_make_record("R-good-1"), audit_dir=tmp_path)
    emit_deploy_record(_make_record("R-good-2"), audit_dir=tmp_path)
    # Inject a malformed line BETWEEN the two good records. S-T3 made the
    # WRITE path (read_last_audit_hash inside write_audit_record) REFUSE
    # when the *last* line is corrupt -- so injecting the bad line as the
    # tail and then emitting again would (correctly) raise
    # AuditLedgerCorruptionError, which is a different contract
    # (test_read_last_audit_hash_raises_on_corrupt_tail). This test is about
    # the READER: iter_records must skip a malformed line already present in
    # a ledger. Putting the bad line in the middle exercises exactly that
    # without colliding with the writer's chain-seal path.
    jsonl = tmp_path / "deploys.jsonl"
    good1, good2 = jsonl.read_text(encoding="utf-8").splitlines()
    jsonl.write_text(f"{good1}\n{{this is not valid json\n{good2}\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="sigantry_core.release.ledger"):
        records = list(iter_records(audit_dir=tmp_path))
    assert [r.release_id for r in records] == ["R-good-1", "R-good-2"]
    assert any("ledger_line_unparseable" in m for m in caplog.messages)


def test_iter_records_skips_tampered_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    emit_deploy_record(_make_record("R-tamper"), audit_dir=tmp_path)
    jsonl = tmp_path / "deploys.jsonl"
    # Mutate a single byte of the audit_hash.
    original = jsonl.read_text("utf-8")
    parsed = json.loads(original.strip())
    last_char = parsed["audit_hash"][-1]
    flipped = "0" if last_char != "0" else "1"
    parsed["audit_hash"] = parsed["audit_hash"][:-1] + flipped
    jsonl.write_text(json.dumps(parsed) + "\n", "utf-8")
    with caplog.at_level("WARNING", logger="sigantry_core.release.ledger"):
        records = list(iter_records(audit_dir=tmp_path))
    assert records == []
    assert any("ledger_line_tampered" in m for m in caplog.messages)


def test_iter_records_round_trip_after_emit_deploy_record(tmp_path: Path) -> None:
    """Pitfall 6 regression: write via emit, read via iter, verify_hash() True."""
    emit_deploy_record(_make_record("R-rt"), audit_dir=tmp_path)
    records = list(iter_records(audit_dir=tmp_path))
    assert len(records) == 1
    assert records[0].verify_hash() is True


def test_iter_records_forward_compat_yields_with_info_when_hash_intact(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WR-03 review fix: forward-compat lines (extra unknown keys) are
    parsed via the fallback path that strips unknown keys before
    constructing the v3.0 ``DeployRecord``.

    Boundary case: if the writer happened to compute its on-disk hash
    against the SAME payload that the v3.0 reader sees after stripping
    (i.e. the writer recomputed the hash AFTER injecting the unknown
    field, and the canonical payload coincidentally matches the
    stripped shape), ``verify_hash()`` succeeds and the record is
    yielded with a distinct INFO event ``ledger_line_forward_compat``.

    In practice a real v3.x writer's hash will INCLUDE the new field
    in the canonical payload, so the stripped reproduction won't
    verify (covered by
    ``test_iter_records_forward_compat_warn_skipped_when_hash_mismatch``).
    This test asserts the recoverable branch -- the reader does NOT
    silently lose forward-compat records that ARE verifiable, and the
    INFO event distinguishes them from tampered / unparseable lines.
    """
    # 1. Anchor record before the forward-compat line.
    emit_deploy_record(_make_record("R-good"), audit_dir=tmp_path)

    # 2. Build a forward-compat line whose audit_hash matches the
    #    STRIPPED canonical payload. Take a known v3.0 record and inject
    #    an unknown additive field WITHOUT recomputing the hash. The
    #    writer here is "well-behaved" -- it persisted its hash against
    #    the canonical (extras-excluded) payload, the way model_copy +
    #    canonical_payload would compute it for an extras-tolerant
    #    successor schema. The reader's strip recovers the same shape.
    base = _make_record("R-future")
    payload = base.model_dump(mode="json")
    payload["new_v3_1_field"] = "future-value"
    jsonl = tmp_path / "deploys.jsonl"
    with jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

    # 3. Anchor record after.
    emit_deploy_record(_make_record("R-after"), audit_dir=tmp_path)

    with caplog.at_level("INFO", logger="sigantry_core.release.ledger"):
        records = list(iter_records(audit_dir=tmp_path))
    # Forward-compat record is yielded at the v3.0 schema shape, between
    # the two anchor records.
    assert [r.release_id for r in records] == ["R-good", "R-future", "R-after"]
    assert any("ledger_line_forward_compat" in m for m in caplog.messages)


def test_iter_records_forward_compat_warn_skipped_when_hash_mismatch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WR-03 review fix: a real v3.x writer would recompute its on-disk
    audit_hash AGAINST the full payload (including the unknown field).
    A v3.0 reader that strips the unknown field then re-runs
    ``verify_hash()`` will see a mismatch -- the line is skipped at
    WARN level with the distinct ``ledger_line_forward_compat_hash_mismatch``
    event so triage can distinguish "newer writer with hash over new
    fields" from "tampered ledger entry".
    """
    emit_deploy_record(_make_record("R-good"), audit_dir=tmp_path)

    base = _make_record("R-future-strict")
    payload = base.model_dump(mode="json")
    payload["new_v3_1_field"] = "future-value"
    # Mutate the audit_hash to simulate a writer that hashed AGAINST
    # the full payload including the unknown field. Any non-matching
    # 64-char hex is enough to break verify_hash() against the strip.
    payload["audit_hash"] = "f" * 64
    jsonl = tmp_path / "deploys.jsonl"
    with jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

    emit_deploy_record(_make_record("R-after"), audit_dir=tmp_path)

    with caplog.at_level("WARNING", logger="sigantry_core.release.ledger"):
        records = list(iter_records(audit_dir=tmp_path))
    # Forward-compat-with-hash-mismatch line is SKIPPED, not yielded;
    # the two anchor records are still readable.
    assert [r.release_id for r in records] == ["R-good", "R-after"]
    assert any("ledger_line_forward_compat_hash_mismatch" in m for m in caplog.messages)


def test_iter_records_genuine_schema_break_warn_skipped(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WR-03 review fix: a line missing a REQUIRED field (not just unknown
    keys) must STILL be WARN-skipped, not silently coerced.

    The fallback path activates only when ``payload`` carries unknown
    keys. A schema break that removes a required field falls through
    to the unparseable WARN.
    """
    # Hand-craft a line with a required field missing (no ``approver``).
    bogus = {
        "workspace": "ws-X",
        "release_id": "R-broken",
        "work_items": [],
        "fabric_items_changed": [],
        "test_evidence": {},
        "audit_hash": "0" * 64,
        "created_at": "2026-04-27T00:00:00+00:00",
    }
    jsonl = tmp_path / "deploys.jsonl"
    jsonl.write_text(json.dumps(bogus) + "\n", "utf-8")

    with caplog.at_level("WARNING", logger="sigantry_core.release.ledger"):
        records = list(iter_records(audit_dir=tmp_path))
    assert records == []
    assert any("ledger_line_unparseable" in m for m in caplog.messages)


def test_find_by_release_id_returns_none_when_missing(tmp_path: Path) -> None:
    emit_deploy_record(_make_record("R-only"), audit_dir=tmp_path)
    assert find_by_release_id("R-missing", audit_dir=tmp_path) is None


def test_find_by_release_id_raises_on_duplicate(tmp_path: Path) -> None:
    """Pattern 4 invariant: duplicate release_id is a defect."""
    emit_deploy_record(_make_record("R-dup"), audit_dir=tmp_path)
    emit_deploy_record(_make_record("R-dup"), audit_dir=tmp_path)
    with pytest.raises(ValueError, match="2 records for release_id"):
        find_by_release_id("R-dup", audit_dir=tmp_path)


def test_diff_records_added_removed_unchanged_buckets(tmp_path: Path) -> None:
    a = _make_record("R-a", items=["lh_x.Lakehouse", "nb_y.Notebook"])
    b = _make_record("R-b", items=["nb_y.Notebook", "lh_z.Lakehouse"])
    diff = diff_records(a, b)
    added_ids = [e["fabric_item_id"] for e in diff["added"]]
    removed_ids = [e["fabric_item_id"] for e in diff["removed"]]
    unchanged_ids = [e["fabric_item_id"] for e in diff["unchanged"]]
    assert added_ids == ["lh_z.Lakehouse"]
    assert removed_ids == ["lh_x.Lakehouse"]
    assert unchanged_ids == ["nb_y.Notebook"]


def test_diff_records_each_entry_has_logical_name_item_type_fabric_item_id() -> None:
    """Pattern 5 schema: each entry carries the three-key dict (SemVer-committed)."""
    a = _make_record("R-x", items=["lh_x.Lakehouse"])
    b = _make_record("R-y", items=["nb_y.Notebook"])
    diff = diff_records(a, b)
    for bucket in ("added", "removed", "unchanged"):
        for entry in diff[bucket]:
            assert set(entry.keys()) == {"logical_name", "item_type", "fabric_item_id"}
