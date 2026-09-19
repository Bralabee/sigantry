"""Audit-2026-05-07 review follow-up (BL-02 / concerns H1) — falsifiability
tests for the cross-process flock around the W3.1 ``prev_hash`` chain.

Pre-fix: ``write_audit_record`` and ``emit_bootstrap_record`` did the
``read_last_audit_hash + seal + append`` sequence without a file lock.
Two concurrent writers (CLI on a workstation + CI runner; two
``apply_sync`` jobs against the same operator host) raced the
predecessor-hash read and produced a fork — ``verify_audit_chain``
then rejected the file as if it had been tampered with. The W3.1
integrity primitive was unusable under any multi-writer load.

Post-fix: the read-seal-append section runs inside an exclusive OS
file lock on a sibling ``.lock`` file (``fcntl.flock`` on POSIX,
``msvcrt.locking`` on Windows -- see test_audit_io_platform_lock.py).
The chain stays monotonic under any concurrency the operator surface
produces.

Falsifiability:

- Reverting the ``with audit_chain_lock(...)`` block in
  ``audit_io.write_audit_record`` (so the read+append run unguarded)
  flips ``test_concurrent_processes_produce_valid_chain`` from PASS
  to FAIL with ``prev_hash mismatch`` from ``verify_audit_chain``.
- Removing the same lock from ``workspace.records.emit_bootstrap_record``
  flips ``test_concurrent_bootstrap_writers_produce_valid_chain``
  identically.
- Skipping the explicit ``LOCK_UN`` in ``audit_chain_lock`` (and
  preventing the fd close from running) deadlocks
  ``test_audit_chain_lock_releases_on_exception``.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import stat
import sys
from datetime import UTC, datetime
from multiprocessing.synchronize import Barrier
from pathlib import Path

import pytest

from sigantry_core.governance.audit_io import (
    audit_chain_lock,
    verify_audit_chain,
    write_audit_record,
)
from sigantry_core.governance.records import SecretChangeRecord
from sigantry_core.workspace.records import BootstrapRecord, emit_bootstrap_record


def _concurrent_audit_writer(
    barrier: Barrier,
    target_dir: str,
    filename: str,
    n_records: int,
    worker_id: int,
) -> None:
    """Forked worker — hammer ``write_audit_record`` after barrier release."""
    log = logging.getLogger(f"audit-test-worker-{worker_id}")
    barrier.wait()
    for i in range(n_records):
        rec = SecretChangeRecord(
            operation="set",
            key=f"W{worker_id}-K{i}",
            store_name="kv",
            actor=f"worker-{worker_id}",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        write_audit_record(
            target_dir=Path(target_dir),
            filename=filename,
            event_name="secret_change_record",
            record=rec,
            audit_logger=log,
        )


def _concurrent_bootstrap_writer(
    barrier: Barrier,
    audit_dir: str,
    n_records: int,
    worker_id: int,
) -> None:
    """Forked worker — hammer ``emit_bootstrap_record`` after barrier release."""
    barrier.wait()
    for i in range(n_records):
        rec = BootstrapRecord(
            workspace_id=f"ws-{worker_id}-{i}",
            workspace_name=f"ws-name-{worker_id}-{i}",
            stage="DEV",
            capacity_id=f"cap-{worker_id}",
            blueprint="minimal_starter",
            operator=f"worker-{worker_id}",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        emit_bootstrap_record(rec, audit_dir=audit_dir)


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX fork not supported on Windows.",
)
@pytest.mark.parametrize(
    "n_workers,records_per_worker",
    [(4, 8)],
    ids=["4workers-8records"],
)
def test_concurrent_processes_produce_valid_chain(
    tmp_path: Path, n_workers: int, records_per_worker: int
) -> None:
    """Forked-multiprocess writers serialise via flock and yield a valid chain.

    Pre-fix this test fails: with N>=2 workers writing M>=4 records each,
    ``verify_audit_chain`` returns ``(False, idx, "prev_hash mismatch")``
    because two records claim the same predecessor (the race between
    the tail read and the append). Post-fix the chain is monotonic.
    """
    target_dir = tmp_path / "audit"
    target_dir.mkdir()
    filename = "concurrent.jsonl"

    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(n_workers)
    procs = [
        ctx.Process(
            target=_concurrent_audit_writer,
            args=(barrier, str(target_dir), filename, records_per_worker, wid),
        )
        for wid in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, (
            f"audit-writer worker pid={p.pid} exited with {p.exitcode}; "
            f"likely a flock contention bug or an SDK exception under fork"
        )

    jsonl = target_dir / filename
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    expected = n_workers * records_per_worker
    assert len(lines) == expected, (
        f"expected {expected} records, got {len(lines)} — "
        f"a writer dropped a record under contention (lost-update race)"
    )
    records = [SecretChangeRecord(**json.loads(ln)) for ln in lines]
    is_valid, bad_idx, reason = verify_audit_chain(records)
    if not is_valid:
        # Surface the prev_hash sequence around the failure for diagnosis.
        window_lo = max(0, (bad_idx or 0) - 1)
        window_hi = min(len(records), (bad_idx or 0) + 2)
        window = [
            (r.audit_hash[:12], r.prev_hash[:12] if r.prev_hash else None)
            for r in records[window_lo:window_hi]
        ]
        pytest.fail(
            f"concurrent writers produced an INVALID chain at record {bad_idx}: {reason}\n"
            f"window (audit_hash[:12], prev_hash[:12]) {window_lo}..{window_hi}: {window}"
        )


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX fork not supported on Windows.",
)
def test_concurrent_bootstrap_writers_produce_valid_chain(tmp_path: Path) -> None:
    """``emit_bootstrap_record`` shares the lock — chain stays valid under fork."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    n_workers, records_per_worker = 3, 5

    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(n_workers)
    procs = [
        ctx.Process(
            target=_concurrent_bootstrap_writer,
            args=(barrier, str(audit_dir), records_per_worker, wid),
        )
        for wid in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    jsonl = audit_dir / "bootstraps.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_workers * records_per_worker
    records = [BootstrapRecord(**json.loads(ln)) for ln in lines]
    is_valid, bad_idx, reason = verify_audit_chain(records)
    assert is_valid, f"bootstrap chain invalid at record {bad_idx}: {reason}"


def test_audit_chain_lock_releases_on_exception(tmp_path: Path) -> None:
    """Lock is released when the with-block raises — subsequent acquires don't deadlock.

    Falsifiability: removing the explicit ``LOCK_UN`` in
    ``audit_chain_lock`` AND preventing the fd close (e.g. by stashing
    the file object outside the with) would deadlock the second
    acquire below; the test would hang and the per-test pytest
    timeout would fail it.
    """
    target = tmp_path / "release.jsonl"

    class _Boom(Exception):  # noqa: N818 -- deliberate sentinel name; matches campaign convention.
        pass

    with pytest.raises(_Boom), audit_chain_lock(target):
        raise _Boom("inside critical section")

    # If the first acquire didn't release, this blocks indefinitely.
    with audit_chain_lock(target):
        pass


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX permission modes not supported on Windows.",
)
def test_audit_chain_lock_file_carries_owner_only_mode(tmp_path: Path) -> None:
    """The sibling ``.lock`` file is created with mode 0o600 (matches the ledger).

    The lock file is co-resident with the audit jsonl which carries
    low-grade PII; both must inherit the audit-plane protection level.
    """
    target = tmp_path / "perms.jsonl"
    with audit_chain_lock(target):
        pass

    lock_path = target.with_suffix(".jsonl.lock")
    assert lock_path.exists(), "lock file was not created at expected sibling path"
    mode = stat.S_IMODE(lock_path.stat().st_mode)
    assert mode == 0o600, (
        f"lock file mode {mode:o} != 0o600 — drops below the audit-plane "
        f"protection level set by _audit_file_opener"
    )


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX permission modes not supported on Windows.",
)
def test_audit_chain_lock_creates_audit_dir_with_owner_only_mode(tmp_path: Path) -> None:
    """The lock helper creates the audit dir with 0o700 if absent.

    Pin the directory-mode invariant so a future refactor that drops
    the ``mode=0o700`` from ``mkdir`` falls out at this gate.
    """
    audit_root = tmp_path / "fresh-audit"
    target = audit_root / "ledger.jsonl"
    assert not audit_root.exists()
    with audit_chain_lock(target):
        pass

    assert audit_root.is_dir()
    dir_mode = stat.S_IMODE(audit_root.stat().st_mode)
    assert dir_mode == 0o700, (
        f"audit dir mode {dir_mode:o} != 0o700 — owner-only protection level "
        f"required because the dir holds low-grade PII"
    )
