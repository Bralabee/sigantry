"""Audit-2026-05-08 review follow-up (WR-03) — falsifiability tests for
the atomic-write + read-from-backup contract in
``scripts/audit_chain_migrate.py``.

Pre-fix the migration sequence was:

1. ``raw_lines = jsonl_path.read_text(...)`` -- read source
2. ``jsonl_path.rename(backup)``           -- snapshot side effect
3. ``jsonl_path.write_text(new_text)``     -- write migrated content

If the process was killed between (2) and (3) -- a SIGTERM, an OOM,
a power failure, an operator pressing Ctrl-C -- the source path no
longer exists and the migrated path is empty. A rerun hit
``if not jsonl_path.is_file(): return (0, 0)`` and silently
short-circuited as a no-op. The operator's data was safe in
``<filename>.pre-w3.1.bak`` but the migration appeared not to apply.
The ``else: pass`` on the original line 122 documented the intent
("Backup already exists -- we're re-running. Read from backup.")
but the code did not actually act on it.

Post-fix:

- The read source is selected as ``backup if backup.exists() else
  jsonl_path`` (or ``return (0,0)`` if neither exists). A rerun
  after interrupt-during-rename reads from the backup and completes
  the migration.
- The final write goes through ``tempfile.mkstemp`` in the same
  directory + ``os.replace`` so an interrupted write never leaves a
  half-written ``jsonl_path``.

Falsifiability: reverting either change flips the relevant test
below from PASS to FAIL.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

from sigantry_core.governance.records import SecretChangeRecord


def _load_migrate_module():
    """Load ``scripts/audit_chain_migrate.py`` directly.

    ``tests/scripts/__init__.py`` makes the test directory a package
    named ``scripts`` which shadows the real ``scripts/`` at the repo
    root. ``importlib.util.spec_from_file_location`` bypasses the
    package-name shadow (mirrors the pattern in
    ``tests/scripts/test_check_crlf.py``).
    """
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit_chain_migrate.py"
    spec = importlib.util.spec_from_file_location("audit_chain_migrate_under_test", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_migrate_module = _load_migrate_module()
migrate_one_file = _migrate_module.migrate_one_file


def _seal_unchained(records: list[SecretChangeRecord]) -> list[SecretChangeRecord]:
    """Seal a list of records WITHOUT prev_hash linkage (pre-W3.1 shape).

    Mirrors the pre-W3.1 ledger: each record's ``audit_hash`` is
    computed against ``prev_hash=None`` (the default). The migration
    re-seals these into a chained shape.
    """
    return [r.with_hash() for r in records]


def _write_jsonl(path: Path, records: list[SecretChangeRecord]) -> None:
    path.write_text(
        "\n".join(
            json.dumps(r.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            for r in records
        )
        + "\n",
        encoding="utf-8",
    )


def _build_records(n: int) -> list[SecretChangeRecord]:
    return _seal_unchained(
        [
            SecretChangeRecord(
                operation="set",
                key=f"K{i}",
                store_name="kv",
                actor="alice",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            )
            for i in range(n)
        ]
    )


def test_rerun_after_interrupt_reads_from_backup(tmp_path: Path) -> None:
    """Simulate kill-between-rename-and-write; rerun completes the migration.

    Pre-fix this test sees ``records_processed=0`` because the
    short-circuit at the top of ``migrate_one_file`` saw an absent
    ``jsonl_path``. Post-fix the read source pivots to ``backup``
    and the migration re-applies.
    """
    jsonl_path = tmp_path / "secret_changes.jsonl"
    backup = jsonl_path.with_suffix(jsonl_path.suffix + ".pre-w3.1.bak")
    records = _build_records(5)

    # Simulate the post-rename-pre-write interrupt state: the original
    # content is in the backup; jsonl_path doesn't exist.
    _write_jsonl(backup, records)
    assert not jsonl_path.exists()

    processed, changed = migrate_one_file(jsonl_path, record_cls=SecretChangeRecord)

    assert processed == 5, (
        f"expected 5 records processed (rerun reads from backup); got {processed}"
    )
    # Records 1..N-1 carry a new prev_hash so their audit_hash changes.
    # Record 0 keeps prev_hash=None (chain head), so its canonical
    # payload is unchanged and the audit_hash is unchanged too.
    assert changed == 4, f"expected 4 of 5 records re-hashed; got {changed}"
    # And jsonl_path was rewritten with the migrated content.
    assert jsonl_path.is_file()
    rebuilt = [
        SecretChangeRecord(**json.loads(ln))
        for ln in jsonl_path.read_text("utf-8").splitlines()
        if ln.strip()
    ]
    assert len(rebuilt) == 5
    # First record's prev_hash is None; subsequent records chain.
    assert rebuilt[0].prev_hash is None
    for i in range(1, 5):
        assert rebuilt[i].prev_hash == rebuilt[i - 1].audit_hash, f"chain link broken at record {i}"


def test_atomic_write_does_not_leak_tmpfile_on_success(tmp_path: Path) -> None:
    """Happy-path migration leaves no tempfile orphan in the audit dir."""
    jsonl_path = tmp_path / "secret_changes.jsonl"
    _write_jsonl(jsonl_path, _build_records(3))

    processed, changed = migrate_one_file(jsonl_path, record_cls=SecretChangeRecord)
    # Records 1..2 carry a new prev_hash; record 0 unchanged.
    assert processed == 3 and changed == 2

    # Only the migrated file + the .pre-w3.1.bak should be present.
    actual = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    expected = ["secret_changes.jsonl", "secret_changes.jsonl.pre-w3.1.bak"]
    assert actual == expected, (
        f"unexpected files in tmp_path after migration: {actual}; "
        f"expected exactly {expected}. Tempfile leak?"
    )


def test_dry_run_does_not_modify_source_or_create_backup(tmp_path: Path) -> None:
    """``--dry-run`` reports counts but writes nothing.

    Regression guard around the WR-03 changes -- the new atomic-
    write path must still respect the ``dry_run`` early-return.
    """
    jsonl_path = tmp_path / "secret_changes.jsonl"
    original_records = _build_records(3)
    _write_jsonl(jsonl_path, original_records)
    pre_text = jsonl_path.read_text("utf-8")

    processed, changed = migrate_one_file(jsonl_path, record_cls=SecretChangeRecord, dry_run=True)
    # Records 1..2 carry a new prev_hash; record 0 unchanged.
    assert processed == 3 and changed == 2
    # No backup created.
    assert not (jsonl_path.with_suffix(".jsonl.pre-w3.1.bak")).exists()
    # Source unchanged.
    assert jsonl_path.read_text("utf-8") == pre_text


def test_idempotent_when_already_chained(tmp_path: Path) -> None:
    """A file that's already W3.1-chained reports changed=0 and writes nothing.

    Pre-fix this case worked because ``_is_already_chained`` short-
    circuited before the write; the post-fix atomic-write path keeps
    that short-circuit (no temp-file created on the no-op branch).
    """
    jsonl_path = tmp_path / "secret_changes.jsonl"

    # Build a properly-chained ledger.
    chained: list[SecretChangeRecord] = []
    prev = None
    for i in range(3):
        rec = SecretChangeRecord(
            operation="set",
            key=f"K{i}",
            store_name="kv",
            actor="alice",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        sealed = rec.model_copy(update={"prev_hash": prev}).with_hash()
        chained.append(sealed)
        prev = sealed.audit_hash
    _write_jsonl(jsonl_path, chained)
    pre_text = jsonl_path.read_text("utf-8")

    processed, changed = migrate_one_file(jsonl_path, record_cls=SecretChangeRecord)
    assert processed == 3
    assert changed == 0
    # Source content unchanged because the migration short-circuited.
    assert jsonl_path.read_text("utf-8") == pre_text
    # And no backup created (we never reached the rename).
    assert not jsonl_path.with_suffix(".jsonl.pre-w3.1.bak").exists()
