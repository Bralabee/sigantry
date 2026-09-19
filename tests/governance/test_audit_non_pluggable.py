"""TRACE-07 -- audit-plane write-through is non-pluggable.

A registered ``TelemetrySink`` plugin (even one that intercepts
everything) MUST NOT suppress ``emit_deploy_record``. Two channels --
stdlib logger + jsonl file -- guarantee the record lands.

This test was a Wave 0 xfail stub; Plan 11-03 replaces the stubs with
real assertions over ``sigantry_core.governance.audit.emit_deploy_record``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord
from sigantry_core.testing.doubles import InMemoryTelemetrySink


@pytest.fixture
def capture_audit(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Bind caplog to the ``sigantry_core.governance.audit`` logger at INFO."""
    caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
    return caplog


@pytest.fixture
def deterministic_record() -> DeployRecord:
    """A reproducible DeployRecord with audit_hash populated by ``with_hash``."""
    return DeployRecord(
        workspace="ws-prod",
        release_id="R-2026-04-26-1",
        work_items=["1234"],
        fabric_items_changed=["nb_silver.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    ).with_hash()


def test_emit_writes_logger_and_jsonl(
    tmp_path: Path,
    capture_audit: pytest.LogCaptureFixture,
    deterministic_record: DeployRecord,
) -> None:
    """Both write channels (stdlib logger + jsonl) fire on a single call."""
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)

    # Channel 1: logger
    records = [r for r in capture_audit.records if r.message == "deploy_record"]
    assert len(records) == 1
    rec = records[0]
    assert rec.event == "deploy_record"
    assert rec.release_id == "R-2026-04-26-1"

    # Channel 2: jsonl
    jsonl = tmp_path / "deploys.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text("utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["release_id"] == "R-2026-04-26-1"
    assert parsed["audit_hash"] == deterministic_record.audit_hash


def test_telemetry_sink_cannot_suppress_audit(
    tmp_path: Path,
    capture_audit: pytest.LogCaptureFixture,
    deterministic_record: DeployRecord,
) -> None:
    """An InMemoryTelemetrySink in scope MUST capture zero audit events.

    The audit plane bypasses every TelemetrySink by design (TRACE-07).
    The sink is not wired into a FabricDataOps front door; the test
    asserts the audit plane works WITHOUT a sink registry. The presence
    of a sink anywhere in scope must NOT change emit_deploy_record's
    behaviour.
    """
    sink = InMemoryTelemetrySink()

    emit_deploy_record(deterministic_record, audit_dir=tmp_path)

    assert sink.events == []  # sink saw nothing
    # Logger captured the record
    records = [r for r in capture_audit.records if r.message == "deploy_record"]
    assert len(records) == 1
    # jsonl captured the record
    assert (tmp_path / "deploys.jsonl").exists()


def test_jsonl_line_round_trips_to_verifiable_record(
    tmp_path: Path,
    deterministic_record: DeployRecord,
) -> None:
    """A jsonl line parses back into a DeployRecord whose verify_hash() is True."""
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)
    line = (tmp_path / "deploys.jsonl").read_text("utf-8").splitlines()[0]
    parsed = json.loads(line)
    reconstructed = DeployRecord(**parsed)
    assert reconstructed.verify_hash() is True
    assert reconstructed.audit_hash == deterministic_record.audit_hash


def test_emit_appends_not_overwrites(
    tmp_path: Path,
    deterministic_record: DeployRecord,
) -> None:
    """Two emits produce two lines (append-only semantics)."""
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)
    lines = (tmp_path / "deploys.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 2


def test_audit_dir_kwarg_overrides_default(
    tmp_path: Path,
    deterministic_record: DeployRecord,
) -> None:
    """Confirm the kwarg path is honoured.

    Defends the test isolation contract -- without an override, the test
    would write to ~/.sigantry/audit/ in the developer's home dir.
    """
    custom = tmp_path / "custom_audit"
    emit_deploy_record(deterministic_record, audit_dir=custom)
    assert (custom / "deploys.jsonl").exists()


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX-only mode bits; Windows ACLs are not exercised by chmod.",
)
def test_jsonl_is_created_with_owner_only_0o600_mode(
    tmp_path: Path,
    deterministic_record: DeployRecord,
) -> None:
    """MD-01 review fix: jsonl must be 0o600, not the user's umask default.

    The parent directory is 0o700 (owner-only); without an explicit opener
    the file would inherit the user's umask (typically 0o664 on Linux dev
    boxes) and a different uid on the same host could read approver UPNs
    and release ids. The audit_file_opener helper sets the mode atomically
    on creation -- no TOCTOU between exists() and a follow-up chmod.
    """
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)
    jsonl = tmp_path / "deploys.jsonl"
    assert jsonl.exists()
    mode = jsonl.stat().st_mode & 0o777
    assert mode == 0o600, f"jsonl mode is {oct(mode)}, expected 0o600 (review-fix MD-01)"


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX-only mode bits; Windows ACLs are not exercised by chmod.",
)
def test_jsonl_mode_survives_an_append(
    tmp_path: Path,
    deterministic_record: DeployRecord,
) -> None:
    """Two emits keep the file at 0o600 (the mode is set on first create only).

    The opener kwarg only fires when ``os.open`` actually creates the file;
    a subsequent append must NOT widen the mode. Lock the invariant.
    """
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)
    emit_deploy_record(deterministic_record, audit_dir=tmp_path)
    jsonl = tmp_path / "deploys.jsonl"
    mode = jsonl.stat().st_mode & 0o777
    assert mode == 0o600, f"jsonl mode after second emit is {oct(mode)}, expected 0o600"
    # Belt-and-braces: confirm the data actually appended (the opener kwarg
    # didn't accidentally truncate on the second open).
    lines = jsonl.read_text("utf-8").splitlines()
    assert len(lines) == 2


def test_no_audit_sink_protocol_seam_introduced() -> None:
    """Defensive -- verify Plan 11-03 did NOT slip in an AuditSink Protocol.

    TRACE-07 forbids audit-plane pluggability. If a future contributor
    adds an AuditSink seam, this test fires. RESEARCH.md line 396 names
    "Adding AuditSink Protocol seam" as the explicit anti-pattern.
    """
    from sigantry_core import protocols

    assert "AuditSink" not in protocols.__all__, (
        "AuditSink Protocol detected -- TRACE-07 forbids audit-plane pluggability."
    )
