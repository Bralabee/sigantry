"""Audit-2026-05-07 W3.1 -- falsifiability tests for prev_hash chaining
across every audit ledger (DeployRecord, ApprovalRecord,
SecretChangeRecord, BootstrapRecord, DestructiveOpRecord).

Pre-W3.1 each record carried only ``audit_hash`` (verify-without-trust
of the record's contents). An attacker who could append to the JSONL
could not silently mutate a record (verify_hash would fail) but COULD
silently DELETE or REORDER records -- the audit row was self-contained
and carried no cross-row anchoring. W3.1 adds ``prev_hash`` + the
``verify_audit_chain`` helper so deletion/reordering/insertion tampering
is detected.

Tests below pin:

- All 5 record types carry a ``prev_hash`` field (default ``None``).
- ``prev_hash`` is part of ``canonical_payload`` -- tampering with it
  invalidates ``audit_hash``.
- ``write_audit_record`` reads the previous record's ``audit_hash``
  and seals the chain link before write.
- ``verify_audit_chain`` accepts a valid chain and rejects the four
  classic tampering patterns: head non-None prev_hash, broken middle
  link, deleted record, mutated middle record.
- ``read_last_audit_hash`` returns ``None`` for missing/empty files.
- ``emit_destructive_op_record`` writes a chained jsonl line (W1
  re-audit follow-up parity).
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sigantry_core.governance.audit_io import (
    _TAIL_WINDOW_BYTES,
    AuditLedgerCorruptionError,
    read_last_audit_hash,
    verify_audit_chain,
    write_audit_record,
)
from sigantry_core.governance.records import (
    ApprovalRecord,
    DestructiveOpRecord,
    SecretChangeRecord,
)
from sigantry_core.release.record import DeployRecord
from sigantry_core.workspace.records import BootstrapRecord

# --- prev_hash field on every record type ------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SecretChangeRecord(
            operation="set",
            key="K",
            store_name="key_vault",
            actor="alice",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        lambda: ApprovalRecord(
            request_id="req-1",
            release_id="rel-1",
            env="dev",
            approvers=["alice"],
            outcome="approved",
        ),
        lambda: DeployRecord(
            workspace="ws",
            release_id="r-1",
            approver="alice",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        lambda: BootstrapRecord(
            workspace_id="ws-id",
            workspace_name="ws-name",
            stage="DEV",
            capacity_id="cap-id",
            blueprint="minimal_starter",
            operator="alice",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        lambda: DestructiveOpRecord(
            resource_kind="workspace",
            action="delete",
            principal="alice",
            outcome="succeeded",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    ],
    ids=["secret", "approval", "deploy", "bootstrap", "destructive"],
)
def test_record_carries_prev_hash_field(factory) -> None:
    """Every audit-record type defaults ``prev_hash`` to ``None``."""
    rec = factory()
    assert hasattr(rec, "prev_hash")
    assert rec.prev_hash is None


def test_prev_hash_is_part_of_canonical_payload() -> None:
    """Tampering with ``prev_hash`` invalidates ``audit_hash``.

    Falsifiability: pre-W3.1 the canonical payload excluded prev_hash
    (because the field didn't exist), so a chain-link tamper could
    NOT be detected by verify_hash. Post-W3.1 it can.
    """
    rec_a = SecretChangeRecord(
        operation="set",
        key="K",
        store_name="kv",
        actor="alice",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        prev_hash="abc" * 22,  # 64 chars
    ).with_hash()
    # Mutate prev_hash on a model_copy; verify_hash must reject.
    rec_b = rec_a.model_copy(update={"prev_hash": "def" * 22})
    assert not rec_b.verify_hash(), (
        "verify_hash() did not detect prev_hash mutation -- chain-link tampering would slip past."
    )


# --- read_last_audit_hash ----------------------------------------------------


def test_read_last_audit_hash_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert read_last_audit_hash(tmp_path / "missing.jsonl") is None


def test_read_last_audit_hash_returns_none_for_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.touch()
    assert read_last_audit_hash(p) is None


def test_read_last_audit_hash_returns_last_record_hash(tmp_path: Path) -> None:
    p = tmp_path / "logs.jsonl"
    p.write_text(
        '{"audit_hash": "first"}\n{"audit_hash": "second"}\n{"audit_hash": "third"}\n',
        encoding="utf-8",
    )
    assert read_last_audit_hash(p) == "third"


def test_read_last_audit_hash_raises_on_corrupt_tail(tmp_path: Path) -> None:
    """A present-but-unparseable last line RAISES rather than returning None.

    S-T3 corrected contract. The pre-fix behaviour returned ``None`` here,
    which the next :func:`write_audit_record` then treated as an empty
    ledger and sealed off ``prev_hash=None`` -- forking the chain and
    triggering false-tamper verdicts on an otherwise-untampered ledger.
    A corrupt-but-present last line must now fail loudly so the write
    refuses instead of extending the fork.
    """
    p = tmp_path / "logs.jsonl"
    p.write_text('{"audit_hash": "good"}\nNOT-JSON\n', encoding="utf-8")
    with pytest.raises(AuditLedgerCorruptionError):
        read_last_audit_hash(p)


def test_read_last_audit_hash_reads_record_larger_than_tail_window(tmp_path: Path) -> None:
    """A record LARGER than the initial tail window is still read in full.

    S-T3 falsifying test (window/growth). Write a >8 KiB record followed by
    a normal one via the REAL writer, then reconstruct both records off
    disk and assert :func:`verify_audit_chain` still returns a valid chain.

    Fail direction (pre-fix, fixed 8-KiB tail cap): the first record's line
    exceeds 8192 bytes, so ``read_last_audit_hash`` reads only its final
    8 KiB -- a mid-line fragment -- which fails ``json.loads`` and returns
    ``None``. The second write then seals ``prev_hash=None`` and
    ``verify_audit_chain`` reports ``(False, 1, ...)``. This assertion of a
    valid chain fails.
    """
    import json as _json
    import logging

    log = logging.getLogger("test")
    audit_path = tmp_path / "big.jsonl"

    # A DeployRecord whose fabric_items_changed list forces the serialised
    # line well past the 8-KiB initial window.
    big_items = [f"item_{i:04d}.Notebook" for i in range(600)]
    assert len(_json.dumps(big_items)) > _TAIL_WINDOW_BYTES
    rec_big = DeployRecord(
        workspace="ws",
        release_id="r-big",
        approver="alice",
        fabric_items_changed=big_items,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rec_small = DeployRecord(
        workspace="ws",
        release_id="r-small",
        approver="alice",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    write_audit_record(
        target_dir=tmp_path,
        filename="big.jsonl",
        event_name="deploy_record",
        record=rec_big,
        audit_logger=log,
    )
    # The chain head read after the big write must be the big record's hash,
    # NOT None (the fork trigger).
    head = read_last_audit_hash(audit_path)
    assert head is not None and len(head) == 64

    write_audit_record(
        target_dir=tmp_path,
        filename="big.jsonl",
        event_name="deploy_record",
        record=rec_small,
        audit_logger=log,
    )

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [DeployRecord(**_json.loads(ln)) for ln in lines]
    assert verify_audit_chain(records) == (True, None, None)


# --- write_audit_record threads the chain -------------------------------------


def test_write_audit_record_chains_records(tmp_path: Path) -> None:
    """Two consecutive writes produce a valid chain link."""
    import logging

    log = logging.getLogger("test")
    rec1 = SecretChangeRecord(
        operation="set",
        key="K1",
        store_name="kv",
        actor="alice",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rec2 = SecretChangeRecord(
        operation="set",
        key="K2",
        store_name="kv",
        actor="alice",
        timestamp=datetime(2026, 1, 2, tzinfo=UTC),
    )

    write_audit_record(
        target_dir=tmp_path,
        filename="t.jsonl",
        event_name="secret_change_record",
        record=rec1,
        audit_logger=log,
    )
    first_hash = read_last_audit_hash(tmp_path / "t.jsonl")
    assert first_hash is not None and len(first_hash) == 64

    write_audit_record(
        target_dir=tmp_path,
        filename="t.jsonl",
        event_name="secret_change_record",
        record=rec2,
        audit_logger=log,
    )
    # Read both records back and verify the chain.
    import json

    lines = (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec1_back = SecretChangeRecord(**json.loads(lines[0]))
    rec2_back = SecretChangeRecord(**json.loads(lines[1]))

    assert rec1_back.prev_hash is None
    assert rec2_back.prev_hash == rec1_back.audit_hash
    ok, _, _ = verify_audit_chain([rec1_back, rec2_back])
    assert ok


# --- verify_audit_chain catches every classic tampering pattern --------------


def _chained(records: list[SecretChangeRecord]) -> list[SecretChangeRecord]:
    """Helper: thread prev_hash through a list of records and seal each."""
    sealed: list[SecretChangeRecord] = []
    prev = None
    for rec in records:
        s = rec.model_copy(update={"prev_hash": prev}).with_hash()
        sealed.append(s)
        prev = s.audit_hash
    return sealed


def _make(timestamp_day: int) -> SecretChangeRecord:
    return SecretChangeRecord(
        operation="set",
        key=f"K{timestamp_day}",
        store_name="kv",
        actor="alice",
        timestamp=datetime(2026, 1, timestamp_day, tzinfo=UTC),
    )


def test_verify_audit_chain_accepts_valid_chain() -> None:
    chain = _chained([_make(d) for d in (1, 2, 3, 4)])
    ok, idx, reason = verify_audit_chain(chain)
    assert ok, (idx, reason)


def test_verify_audit_chain_rejects_non_none_head_prev_hash() -> None:
    """Chain head (record 0) MUST have prev_hash=None; non-None signals
    that record 0 was deleted and the rest renumbered."""
    chain = _chained([_make(d) for d in (1, 2)])
    # Tamper: pretend record 0 has a prev_hash (should be None).
    bad_head = chain[0].model_copy(update={"prev_hash": "fakehead"}).with_hash()
    ok, idx, _ = verify_audit_chain([bad_head, chain[1]])
    assert not ok and idx == 0


def test_verify_audit_chain_rejects_broken_middle_link() -> None:
    chain = _chained([_make(d) for d in (1, 2, 3)])
    # Tamper: middle record's prev_hash points at a different hash.
    bad_middle = chain[1].model_copy(update={"prev_hash": "wrong"}).with_hash()
    ok, idx, _ = verify_audit_chain([chain[0], bad_middle, chain[2]])
    assert not ok and idx == 1


def test_verify_audit_chain_rejects_deleted_middle_record() -> None:
    """Removing record 1 from a 3-record chain breaks record 2's prev_hash."""
    chain = _chained([_make(d) for d in (1, 2, 3)])
    ok, idx, _ = verify_audit_chain([chain[0], chain[2]])
    assert not ok and idx == 1  # record at new position 1 is the offender


def test_verify_audit_chain_rejects_mutated_middle_record() -> None:
    """Mutating a record's payload after sealing invalidates verify_hash."""
    chain = _chained([_make(d) for d in (1, 2, 3)])
    # Tamper without re-hashing -- verify_hash MUST fire.
    bad_middle = chain[1].model_copy(update={"actor": "mallory"})
    ok, idx, _ = verify_audit_chain([chain[0], bad_middle, chain[2]])
    assert not ok and idx == 1


# --- emit_destructive_op_record (W1 re-audit follow-up parity) ---------------


def test_destructive_op_success_path_swallows_audit_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wave 3 re-audit FU-1: an audit-write OSError on the SUCCESS path
    must NOT mask the wrapped function's successful return.

    Pre-FU-1 the success-path emit_destructive_op_record call was not
    wrapped in try/except; an audit-disk full / permission-denied
    propagated and turned a successful op into a failed one. Post-FU-1
    the success path mirrors the failure path's defensive handling.
    """
    from sigantry_core.governance.destructive import destructive_op

    @destructive_op("test", "delete")
    def _do_thing(*, force: bool = False) -> str:
        return "ok"

    # Make every emit_destructive_op_record call raise OSError.
    def _raise(*_args, **_kwargs) -> None:
        raise OSError("disk full (synthetic)")

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    monkeypatch.setattr(audit_mod, "emit_destructive_op_record", _raise)

    # The wrapped function MUST return "ok" -- the audit failure is
    # logged but does not propagate.
    assert _do_thing(force=True) == "ok"


def test_destructive_op_failure_path_preserves_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wave 3 re-audit FU-1 (sibling): an audit-write OSError on the
    FAILURE path must NOT mask the wrapped function's original
    exception. Pre-W1.8 the audit log only fired on success; post-W1.8
    +W3.1 it fires on both paths but the failure path catches OSError
    and re-raises the ORIGINAL exception.
    """
    from sigantry_core.governance.destructive import destructive_op

    class _Boom(RuntimeError):  # noqa: N818
        pass

    @destructive_op("test", "delete")
    def _do_thing(*, force: bool = False) -> str:
        raise _Boom("original-error")

    def _raise_oserror(*_args, **_kwargs) -> None:
        raise OSError("disk full (synthetic)")

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    monkeypatch.setattr(audit_mod, "emit_destructive_op_record", _raise_oserror)

    # The original _Boom MUST propagate, not the synthetic OSError.
    with pytest.raises(_Boom, match="original-error"):
        _do_thing(force=True)


def test_emit_destructive_op_record_writes_chained_jsonl(tmp_path: Path) -> None:
    """``destructive_ops.jsonl`` is created with chained records.

    Falsifiability: pre-W1-re-audit-follow-up the @destructive_op
    decorator only emitted to the structured logger. No
    ``destructive_ops.jsonl`` existed. Now both succeed-path and
    fail-path emissions write to disk.
    """
    from sigantry_core.governance.audit import emit_destructive_op_record

    rec1 = DestructiveOpRecord(
        resource_kind="workspace",
        action="delete",
        principal="alice",
        outcome="succeeded",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rec2 = DestructiveOpRecord(
        resource_kind="capacity",
        action="pause",
        principal="alice",
        runbook_id="INC-9",
        outcome="failed",
        exc_type="AuthError",
        timestamp=datetime(2026, 1, 2, tzinfo=UTC),
    )
    emit_destructive_op_record(rec1, audit_dir=tmp_path)
    emit_destructive_op_record(rec2, audit_dir=tmp_path)

    jsonl = tmp_path / "destructive_ops.jsonl"
    assert jsonl.is_file()
    import json

    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec1_back = DestructiveOpRecord(**json.loads(lines[0]))
    rec2_back = DestructiveOpRecord(**json.loads(lines[1]))
    ok, _, _ = verify_audit_chain([rec1_back, rec2_back])
    assert ok
