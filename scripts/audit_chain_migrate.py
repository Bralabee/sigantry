"""Audit-2026-05-07 W3.1 -- one-shot migration: chain existing audit ledgers.

Walks ``~/.sigantry/audit/*.jsonl`` (or any directory passed via
``--audit-dir``) and rewrites every line to carry ``prev_hash``
linkage:

    record[0].prev_hash = None
    record[i].prev_hash = record[i-1].audit_hash  for i >= 1

Each record's ``audit_hash`` is recomputed because ``prev_hash`` is
part of the canonical payload (W3.1 invariant). The original file
is renamed to ``<filename>.pre-w3.1.bak`` before the rewrite so a
manual recovery is possible if the script is interrupted mid-write.

Idempotency: if the first record's ``prev_hash`` is already absent
or ``null`` AND the second record's ``prev_hash`` matches the first
record's ``audit_hash``, the script no-ops (already migrated). The
caller can run the script multiple times without doubling the chain
links.

Usage::

    # Migrate the default directory:
    python scripts/audit_chain_migrate.py

    # Migrate a custom directory:
    python scripts/audit_chain_migrate.py --audit-dir /path/to/audit

    # Dry-run (no file writes):
    python scripts/audit_chain_migrate.py --dry-run

The five known ledger filenames (one per record type):

- ``deploys.jsonl``           -> :class:`DeployRecord`
- ``approvals.jsonl``         -> :class:`ApprovalRecord`
- ``secret_changes.jsonl``    -> :class:`SecretChangeRecord`
- ``bootstraps.jsonl``        -> :class:`BootstrapRecord`
- ``destructive_ops.jsonl``   -> :class:`DestructiveOpRecord`

Files outside this set are listed but not migrated -- the script does
not know how to canonicalise their payloads.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from sigantry_core.governance.records import (
    ApprovalRecord,
    DestructiveOpRecord,
    SecretChangeRecord,
)
from sigantry_core.release.record import DeployRecord
from sigantry_core.workspace.records import BootstrapRecord

_FILENAME_TO_RECORD: dict[str, type] = {
    "deploys.jsonl": DeployRecord,
    "approvals.jsonl": ApprovalRecord,
    "secret_changes.jsonl": SecretChangeRecord,
    "bootstraps.jsonl": BootstrapRecord,
    "destructive_ops.jsonl": DestructiveOpRecord,
}


def _is_already_chained(records: list[Any]) -> bool:
    """Return True if records[0] has prev_hash=None and records[1].prev_hash
    points at records[0].audit_hash. Two-element check is sufficient because
    a partially-chained file is in a corrupt state and we'll rebuild fully.
    """
    if not records:
        return True
    if records[0].prev_hash is not None:
        return False
    if len(records) < 2:
        return True  # single-record file: head OK, nothing more to verify.
    return records[1].prev_hash == records[0].audit_hash


def migrate_one_file(
    jsonl_path: Path,
    *,
    record_cls: type,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Rewrite ``jsonl_path`` so every record carries a chain link.

    Returns ``(records_processed, records_changed)``. A record is
    "changed" when its ``audit_hash`` differs after re-sealing with
    ``prev_hash`` populated.
    """
    backup = jsonl_path.with_suffix(jsonl_path.suffix + ".pre-w3.1.bak")

    # Audit-2026-05-08 review follow-up (WR-03): if the previous run was
    # interrupted between rename(jsonl_path -> backup) and write_text on
    # jsonl_path, the source file is gone but the backup carries the
    # full pre-migration content. Read from whichever exists -- the
    # migration is then idempotent across interruption points. Pre-fix
    # the read came from ``jsonl_path`` unconditionally, so an
    # interrupt-then-rerun saw an empty path and short-circuited as a
    # no-op (records=0) -- the operator's data was safe in the backup
    # but the migration appeared not to apply.
    if backup.exists():
        read_source = backup
    elif jsonl_path.is_file():
        read_source = jsonl_path
    else:
        return (0, 0)

    raw_lines = [ln for ln in read_source.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records = [record_cls(**json.loads(ln)) for ln in raw_lines]

    if _is_already_chained(records):
        return (len(records), 0)

    sealed: list[Any] = []
    prev = None
    changed = 0
    for rec in records:
        new_rec = rec.model_copy(update={"prev_hash": prev}).with_hash()
        if new_rec.audit_hash != rec.audit_hash:
            changed += 1
        sealed.append(new_rec)
        prev = new_rec.audit_hash

    if dry_run:
        return (len(records), changed)

    # Take a backup before the rewrite if we don't already have one.
    # The rename below is the durable signal that the migration started.
    if not backup.exists() and jsonl_path.is_file():
        jsonl_path.rename(backup)

    new_lines = [
        json.dumps(s.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) for s in sealed
    ]
    new_text = "\n".join(new_lines) + "\n"

    # Audit-2026-05-08 review follow-up (WR-03): atomic write. Pre-fix
    # ``jsonl_path.write_text(new_text)`` could leave a half-written
    # file if the process was killed mid-write. Write to a tempfile in
    # the SAME directory (so ``os.replace`` is atomic on POSIX -- it's
    # a same-filesystem rename) and then atomically replace the target.
    # Either the new content is fully visible or the target stays at
    # whatever it was before; no partial-write window.
    target_dir = jsonl_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{jsonl_path.name}.",
        suffix=".tmp",
        dir=str(target_dir),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(jsonl_path))
    except BaseException:
        # On error: drop the partially-written tmpfile so we don't
        # leak orphans next to the audit ledger.
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise
    return (len(records), changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=Path.home() / ".sigantry" / "audit",
        help="Audit directory to migrate (default: ~/.sigantry/audit).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without touching files on disk.",
    )
    args = parser.parse_args(argv)

    audit_dir: Path = args.audit_dir
    if not audit_dir.is_dir():
        print(f"audit dir not found: {audit_dir}", file=sys.stderr)
        return 0  # not a hard error -- a fresh install has no ledger yet.

    total_processed = 0
    total_changed = 0
    for jsonl_path in sorted(audit_dir.glob("*.jsonl")):
        record_cls = _FILENAME_TO_RECORD.get(jsonl_path.name)
        if record_cls is None:
            print(f"skip (unknown filename): {jsonl_path.name}")
            continue
        processed, changed = migrate_one_file(
            jsonl_path,
            record_cls=record_cls,
            dry_run=args.dry_run,
        )
        action = "would migrate" if args.dry_run else "migrated"
        print(f"{action}: {jsonl_path.name}  records={processed}  rehashed={changed}")
        total_processed += processed
        total_changed += changed

    print(f"\nTotal: records={total_processed}  rehashed={total_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
