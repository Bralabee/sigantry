"""SecretChangeRecord + ApprovalRecord audit-line emission tests
(Plan 16-02 + Plan 16-03).

Per RESEARCH §3 correction: the new pydantic models live in
``sigantry_core.governance.records`` (NOT ``sigantry_core.release.audit``,
which does not exist). Audit lines append via the existing
``sigantry_core.governance.audit`` jsonl writer.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from sigantry_core.governance.audit import (
    emit_approval_record,
    emit_secret_change_record,
)
from sigantry_core.governance.records import ApprovalRecord, SecretChangeRecord


def test_secret_change_record_jsonl_appended(tmp_path: Path) -> None:
    """``emit_secret_change_record`` appends a parseable jsonl line.

    Plan 16-02: the audit-plane writes to a separate ``secret_changes.jsonl``
    file, NOT ``deploys.jsonl`` (which holds DeployRecord lines).
    """
    record = SecretChangeRecord(
        operation="set",
        key="STRIPE_API_KEY",
        store_name="key_vault",
        actor="alice@example.com",
        timestamp=datetime.now(UTC),
    ).with_hash()

    emit_secret_change_record(record, audit_dir=tmp_path)

    jsonl = tmp_path / "secret_changes.jsonl"
    assert jsonl.is_file(), "secret_changes.jsonl must be created on emit"
    lines = jsonl.read_text("utf-8").splitlines()
    assert len(lines) == 1, "exactly one jsonl line per emit call"

    parsed = json.loads(lines[0])
    assert parsed["operation"] == "set"
    assert parsed["key"] == "STRIPE_API_KEY"
    assert parsed["store_name"] == "key_vault"
    assert parsed["actor"] == "alice@example.com"
    assert parsed["audit_hash"] == record.audit_hash
    # Anti-pattern guard: no `value` field can have leaked into the record.
    assert "value" not in parsed, "secret value MUST NOT appear in the audit record"

    # Sibling file: deploys.jsonl is NOT written by this emit.
    assert not (tmp_path / "deploys.jsonl").exists()


def test_secret_change_record_audit_hash_round_trips(tmp_path: Path) -> None:
    """Write-then-read round-trip: parse a jsonl line back into the model
    and ``verify_hash()`` returns True."""
    original = SecretChangeRecord(
        operation="delete",
        key="LEGACY_KEY",
        store_name="ado_variable_group",
        actor="ci-runner",
        timestamp=datetime.now(UTC),
    ).with_hash()

    emit_secret_change_record(original, audit_dir=tmp_path)

    line = (tmp_path / "secret_changes.jsonl").read_text("utf-8").splitlines()[0]
    parsed = json.loads(line)
    rehydrated = SecretChangeRecord(**parsed)

    assert rehydrated == original, "round-trip must reconstruct the exact record"
    assert rehydrated.verify_hash(), (
        "verify_hash() must succeed on a record reconstructed from disk"
    )

    # Tamper detection: flip one bit in the audit_hash and verify_hash() should fail.
    tampered_hash = rehydrated.audit_hash[:-1] + ("0" if rehydrated.audit_hash[-1] != "0" else "1")
    tampered = rehydrated.model_copy(update={"audit_hash": tampered_hash})
    assert not tampered.verify_hash(), "verify_hash() must fail on tampered audit_hash"


def test_secret_change_record_no_value_field() -> None:
    """Anti-Pattern pin: the record schema MUST NOT carry the secret value.

    Only operation + key + actor + timestamp + audit_hash. Constructing
    with an extra ``value=`` kwarg raises pydantic.ValidationError thanks
    to ``ConfigDict(extra='forbid')`` on the model.
    """
    with pytest.raises(ValidationError) as excinfo:
        SecretChangeRecord(
            operation="set",
            key="STRIPE_API_KEY",
            store_name="key_vault",
            actor="alice@example.com",
            timestamp=datetime.now(UTC),
            value="super-secret-plaintext",  # type: ignore[call-arg]
        )
    # pydantic v2 error message contains "extra" / "forbidden"; assert one of them.
    err = str(excinfo.value).lower()
    assert "extra" in err or "forbid" in err, (
        f"expected pydantic extra-forbid message; got: {excinfo.value}"
    )


def test_approval_record_jsonl_appended(tmp_path: Path) -> None:
    """``emit_approval_record`` appends a parseable jsonl line.

    Plan 16-03: the audit-plane writes to a separate ``approvals.jsonl``
    file, distinct from ``deploys.jsonl`` (DeployRecord, Plan 11-02) and
    ``secret_changes.jsonl`` (SecretChangeRecord, Plan 16-02).
    """
    record = ApprovalRecord(
        request_id="req-42",
        release_id="rel-2026-04-28",
        env="prod",
        approvers=["alice@example.com", "bob@example.com"],
        outcome="approved",
        decided_by="alice@example.com",
        decided_at=datetime.now(UTC),
        last_observed_status="approved",
    ).with_hash()

    emit_approval_record(record, audit_dir=tmp_path)

    jsonl = tmp_path / "approvals.jsonl"
    assert jsonl.is_file(), "approvals.jsonl must be created on emit"
    # File mode 0o600 (owner-only) -- mirrors emit_deploy_record + emit_secret_change_record.
    if not sys.platform.startswith("win"):
        assert jsonl.stat().st_mode & 0o777 == 0o600, (
            f"approvals.jsonl must be 0o600; got {oct(jsonl.stat().st_mode & 0o777)}"
        )
    lines = jsonl.read_text("utf-8").splitlines()
    assert len(lines) == 1, "exactly one jsonl line per emit call"

    parsed = json.loads(lines[0])
    assert parsed["request_id"] == "req-42"
    assert parsed["release_id"] == "rel-2026-04-28"
    assert parsed["env"] == "prod"
    assert parsed["outcome"] == "approved"
    assert parsed["decided_by"] == "alice@example.com"
    assert parsed["last_observed_status"] == "approved"
    assert parsed["audit_hash"] == record.audit_hash

    # Sibling files must NOT be written by this emit.
    assert not (tmp_path / "deploys.jsonl").exists()
    assert not (tmp_path / "secret_changes.jsonl").exists()


def test_approval_record_audit_hash_round_trips(tmp_path: Path) -> None:
    """Write-then-read round-trip: parse a jsonl line back into the model
    and ``verify_hash()`` returns True. Tampering the audit_hash flips
    ``verify_hash()`` to ``False`` -- the canonical-JSON SHA-256 invariant
    detects post-write modification."""
    original = ApprovalRecord(
        request_id="req-43",
        release_id="rel-2026-04-28",
        env="preprod",
        approvers=["carol@example.com"],
        outcome="timeout",
        decided_by=None,
        decided_at=None,
        last_observed_status="pending",  # RESEARCH §Pitfall 4 marker
    ).with_hash()

    emit_approval_record(original, audit_dir=tmp_path)

    line = (tmp_path / "approvals.jsonl").read_text("utf-8").splitlines()[0]
    parsed = json.loads(line)
    rehydrated = ApprovalRecord(**parsed)

    assert rehydrated == original, "round-trip must reconstruct the exact record"
    assert rehydrated.verify_hash(), (
        "verify_hash() must succeed on a record reconstructed from disk"
    )

    # last_observed_status survives the round-trip (RESEARCH §Pitfall 4 pin).
    assert rehydrated.last_observed_status == "pending"
    assert rehydrated.outcome == "timeout"

    # Tamper detection: flip one bit in the audit_hash.
    tampered_hash = rehydrated.audit_hash[:-1] + ("0" if rehydrated.audit_hash[-1] != "0" else "1")
    tampered = rehydrated.model_copy(update={"audit_hash": tampered_hash})
    assert not tampered.verify_hash(), "verify_hash() must fail on tampered audit_hash"
