"""Unit tests for sigantry_core.workspace.records (BootstrapRecord)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from sigantry_core.workspace.records import (
    BootstrapRecord,
    emit_bootstrap_record,
)


def _make_record(**overrides: object) -> BootstrapRecord:
    """Construct a record with sensible defaults; let callers override fields."""
    defaults: dict[str, object] = {
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "workspace_name": "test-ws",
        "stage": "DEV",
        "capacity_id": "22222222-2222-2222-2222-222222222222",
        "blueprint": "minimal_starter",
        "folders_created": ["000 Orchestrate", "100 Ingest"],
        "folders_present": ["000 Orchestrate", "100 Ingest", "200 Store"],
        "git_target": None,
        "step_outcomes": {
            "workspace": "created",
            "capacity": "already-converged",
            "folders": "created",
            "git": "skipped",
            "initialize": "skipped",
        },
        "operator": "alice@example.invalid",
        "created_at": datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return BootstrapRecord(**defaults)  # type: ignore[arg-type]


def test_record_with_hash_populates_audit_hash() -> None:
    rec = _make_record().with_hash()
    assert rec.audit_hash != ""
    assert len(rec.audit_hash) == 64  # sha256 hex


def test_record_verify_hash_round_trip() -> None:
    rec = _make_record().with_hash()
    assert rec.verify_hash() is True


def test_record_verify_hash_detects_field_tamper() -> None:
    """Mutating any field after with_hash invalidates the digest."""
    rec = _make_record().with_hash()
    tampered = rec.model_copy(update={"workspace_id": "deadbeef-…"})
    assert tampered.verify_hash() is False


def test_record_verify_hash_detects_hash_tamper() -> None:
    """Tampering the hash itself is detected because verify recomputes."""
    rec = _make_record().with_hash()
    tampered = rec.model_copy(update={"audit_hash": "0" * 64})
    assert tampered.verify_hash() is False


def test_record_rejects_extra_fields() -> None:
    """ConfigDict(extra='forbid') keeps the audit-record schema closed."""
    with pytest.raises(ValidationError, match="extra"):
        BootstrapRecord(  # type: ignore[call-arg]
            workspace_id="x",
            workspace_name="y",
            stage="DEV",
            capacity_id="z",
            blueprint="minimal_starter",
            operator="op",
            created_at=datetime.now(UTC),
            unexpected_field="boom",
        )


def test_record_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _make_record(created_at=datetime(2026, 5, 1, 12, 0, 0))


def test_record_truncates_microseconds_to_millis() -> None:
    """Round-trip determinism guarantee (Pitfall 8)."""
    raw = datetime(2026, 5, 1, 12, 0, 0, microsecond=123456, tzinfo=UTC)
    rec = _make_record(created_at=raw)
    assert rec.created_at.microsecond == 123000  # truncated to ms grid


def test_record_two_records_at_same_millis_hash_identically() -> None:
    """Determinism: two records with identical content + ms-truncated time
    produce the same audit_hash."""
    t = datetime(2026, 5, 1, 12, 0, 0, microsecond=999000, tzinfo=UTC)
    a = _make_record(created_at=t).with_hash()
    b = _make_record(created_at=t).with_hash()
    assert a.audit_hash == b.audit_hash


def test_record_with_git_target_round_trips() -> None:
    target = {
        "organization_name": "myorg",
        "project_name": "myproj",
        "repository_name": "myrepo",
        "branch_name": "main",
        "directory_name": "fabric_items",
    }
    rec = _make_record(git_target=target).with_hash()
    assert rec.git_target == target
    assert rec.verify_hash() is True


def test_emit_bootstrap_record_appends_to_jsonl(tmp_path: Path) -> None:
    rec = _make_record()
    written = emit_bootstrap_record(rec, audit_dir=tmp_path)
    assert written == tmp_path / "bootstraps.jsonl"
    assert written.is_file()
    line = written.read_text("utf-8").splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["workspace_id"] == rec.workspace_id
    assert len(parsed["audit_hash"]) == 64
    # Re-construct from the JSONL and verify hash round-trips.
    rehydrated = BootstrapRecord(**parsed)
    assert rehydrated.verify_hash() is True


def test_emit_creates_audit_dir_on_demand(tmp_path: Path) -> None:
    target_dir = tmp_path / "deeply" / "nested" / "audit"
    rec = _make_record()
    written = emit_bootstrap_record(rec, audit_dir=target_dir)
    assert written.is_file()


def test_emit_appends_does_not_overwrite(tmp_path: Path) -> None:
    """JSONL ledger is append-only; second emit adds a second line."""
    a = _make_record(workspace_id="aaaa-aaaa")
    b = _make_record(workspace_id="bbbb-bbbb")
    emit_bootstrap_record(a, audit_dir=tmp_path)
    emit_bootstrap_record(b, audit_dir=tmp_path)
    lines = (tmp_path / "bootstraps.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["workspace_id"] == "aaaa-aaaa"
    assert parsed[1]["workspace_id"] == "bbbb-bbbb"
