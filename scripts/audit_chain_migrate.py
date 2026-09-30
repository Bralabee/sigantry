"""Audit-2026-05-07 W3.1 -- one-shot migration: chain existing audit ledgers.

Walks ``~/.sigantry/audit/*.jsonl`` (or any directory passed via
``--audit-dir``) and rewrites every line to carry ``prev_hash``
linkage:

    record[0].prev_hash = None
    record[i].prev_hash = record[i-1].audit_hash  for i >= 1

Each record's ``audit_hash`` is recomputed because ``prev_hash`` is
part of the canonical payload (W3.1 invariant).

Safety contract -- a ledger is the evidence, so the migration never
trades records for a valid-looking chain:

- **Verify before re-sealing.** Every record must match its own
  ``audit_hash`` in the form it was stored (a pre-W3.1 record was
  sealed without a ``prev_hash`` key, so the current model's
  ``verify_hash()`` cannot judge it), and every chain link a record
  carries must point at its predecessor. A ledger that fails either
  check is REFUSED: nothing is written and the script exits non-zero.
  Re-sealing an unverifiable record would launder it into a chain that
  ``sigantry release verify`` reports as valid.
- **Read the live ledger.** The ``<filename>.pre-w3.1.bak`` backup is a
  read source only when the live file is absent (a run of the previous
  version of this script interrupted between its rename and its write).
  Reading the backup while the live file exists replays the old content
  over every record written since the first migration.
- **Hold the writer's lock.** The read-verify-write sequence runs under
  :func:`audit_chain_lock`, the lock every audit writer takes, so no
  record can be appended between the read and the replace.
- **Never clobber a backup.** The live file is copied (not renamed) to
  ``<filename>.pre-w3.1.bak``, or to ``.pre-w3.1.bak.<n>`` when earlier
  backups exist, and then atomically replaced. The live ledger is never
  absent.

Idempotency: a ledger whose whole chain already verifies is left
untouched, so the script can be run any number of times.

Usage::

    # Migrate the default directory:
    python scripts/audit_chain_migrate.py

    # Migrate a custom directory:
    python scripts/audit_chain_migrate.py --audit-dir /path/to/audit

    # Dry-run (no file writes; still verifies and still refuses):
    python scripts/audit_chain_migrate.py --dry-run

Exit status: 0 when every ledger was migrated or already chained; 1 when
any ledger was refused (the refused ledgers are left exactly as found).

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
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sigantry_core.governance.audit_io import audit_chain_lock, verify_audit_chain
from sigantry_core.governance.records import (
    ApprovalRecord,
    DestructiveOpRecord,
    SecretChangeRecord,
)
from sigantry_core.release.record import _CANONICAL_KWARGS, DeployRecord
from sigantry_core.workspace.records import BootstrapRecord

_FILENAME_TO_RECORD: dict[str, type] = {
    "deploys.jsonl": DeployRecord,
    "approvals.jsonl": ApprovalRecord,
    "secret_changes.jsonl": SecretChangeRecord,
    "bootstraps.jsonl": BootstrapRecord,
    "destructive_ops.jsonl": DestructiveOpRecord,
}

_BACKUP_SUFFIX = ".pre-w3.1.bak"
_BACKUP_MODE = 0o600


class MigrationRefusedError(RuntimeError):
    """A ledger failed a pre-seal check; nothing was written to it."""


def _stored_hash_ok(raw: dict[str, Any]) -> bool:
    """Return True if ``raw`` matches its own ``audit_hash`` as stored.

    The hash is recomputed over the stored JSON object (minus
    ``audit_hash``), not over the current model's dump. A record sealed
    before a field existed -- ``prev_hash`` on every pre-W3.1 record --
    carries no key for it, and the current model would add one and so
    report a genuine record as tampered.
    """
    body = dict(raw)
    stored = body.pop("audit_hash", None)
    if not isinstance(stored, str):
        return False
    recomputed = hashlib.sha256(json.dumps(body, **_CANONICAL_KWARGS).encode("utf-8")).hexdigest()
    return recomputed == stored


def _chain_violation(raws: list[dict[str, Any]]) -> str | None:
    """Return why ``raws`` must not be re-sealed, or None if it may be.

    A record is *linked* when it carries a non-null ``prev_hash`` and
    *unlinked* otherwise (a pre-W3.1 record without the key, or one
    sealed with ``prev_hash = null``). A ledger may start with any
    number of unlinked records; the W3.1 writer then links its first
    record onto the last of them. So:

    - every record must match its own ``audit_hash`` as stored;
    - a linked record must point at its predecessor's ``audit_hash``
      (so the first record can never be linked);
    - once the chain has started, every later record must be linked --
      an unlinked record there is a fork or a reset head.
    """
    chain_started = False
    for i, raw in enumerate(raws):
        if not _stored_hash_ok(raw):
            return f"record {i} fails verification: it does not match its own audit_hash (edited or corrupt)"
        prev = raw.get("prev_hash")
        if prev is None:
            if chain_started:
                return (
                    f"record {i} is unlinked but follows linked records (fork or reset chain head)"
                )
            continue
        if i == 0:
            return "record 0 links to a predecessor the ledger does not hold (truncated head)"
        if prev != raws[i - 1].get("audit_hash"):
            return f"record {i} prev_hash does not match record {i - 1}'s audit_hash (broken chain link)"
        chain_started = True
    return None


def _is_already_chained(raws: list[dict[str, Any]]) -> bool:
    """Return True if the WHOLE ledger is already a verified W3.1 chain.

    Every record carries the ``prev_hash`` key, every link after the
    head is set, and :func:`_chain_violation` finds nothing. Checking
    only the first two records (as this function once did) passes a
    ledger whose later links are broken and leaves it broken.
    """
    if not raws:
        return True
    if not all("prev_hash" in raw for raw in raws):
        return False
    if any(raw["prev_hash"] is None for raw in raws[1:]):
        return False
    return _chain_violation(raws) is None


def _parse(text: str, *, record_cls: type, source: Path) -> tuple[list[dict[str, Any]], list[Any]]:
    raws: list[dict[str, Any]] = []
    records: list[Any] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            records.append(record_cls(**raw))
        except (ValueError, TypeError, ValidationError) as exc:
            raise MigrationRefusedError(
                f"{source.name} line {lineno} is not a valid {record_cls.__name__}: {exc}"
            ) from exc
        raws.append(raw)
    return raws, records


def _backup_path(jsonl_path: Path) -> Path:
    """Return the first backup name that does not exist yet."""
    base = jsonl_path.with_name(jsonl_path.name + _BACKUP_SUFFIX)
    if not base.exists():
        return base
    n = 1
    while base.with_name(f"{base.name}.{n}").exists():
        n += 1
    return base.with_name(f"{base.name}.{n}")


def _write_backup(path: Path, data: bytes) -> None:
    """Write ``data`` to a NEW file at ``path`` (owner-only, fsynced)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, _BACKUP_MODE)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _atomic_replace(jsonl_path: Path, new_text: str) -> None:
    """Replace ``jsonl_path`` with ``new_text`` atomically.

    Audit-2026-05-08 review follow-up (WR-03): write to a tempfile in
    the SAME directory (so ``os.replace`` is a same-filesystem rename)
    and then atomically replace the target. Either the new content is
    fully visible or the target stays as it was; no partial-write window.
    """
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
        # Drop the partially-written tmpfile so no orphan sits next to the ledger.
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


def migrate_one_file(
    jsonl_path: Path,
    *,
    record_cls: type,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Rewrite ``jsonl_path`` so every record carries a chain link.

    Returns ``(records_processed, records_changed)``. A record is
    "changed" when its ``audit_hash`` differs after re-sealing with
    ``prev_hash`` populated; ``records_changed == 0`` means the ledger
    was already chained and nothing was written.

    Raises :class:`MigrationRefusedError`, having written nothing, when
    the ledger fails a pre-seal check (see the module docstring).
    """
    backup = jsonl_path.with_name(jsonl_path.name + _BACKUP_SUFFIX)
    if not jsonl_path.is_file() and not backup.is_file():
        return (0, 0)

    with audit_chain_lock(jsonl_path):
        # Choose the read source under the lock, so no writer can change
        # the answer between the choice and the read.
        if jsonl_path.is_file():
            read_source = jsonl_path
        elif backup.is_file():
            # Audit-2026-05-08 review follow-up (WR-03): a run of the
            # previous version of this script renamed the ledger to the
            # backup and was interrupted before writing the migrated
            # file. The backup holds the full pre-migration content.
            later = sorted(backup.parent.glob(backup.name + ".*"))
            if later:
                raise MigrationRefusedError(
                    f"{jsonl_path.name} is missing but later backups exist "
                    f"({', '.join(p.name for p in later)}); restore the ledger by hand"
                )
            read_source = backup
        else:  # removed while we waited for the lock
            return (0, 0)

        original = read_source.read_bytes()
        raws, records = _parse(original.decode("utf-8"), record_cls=record_cls, source=read_source)

        if _is_already_chained(raws):
            return (len(records), 0)

        reason = _chain_violation(raws)
        if reason is not None:
            raise MigrationRefusedError(f"{read_source.name}: {reason}")

        sealed: list[Any] = []
        prev = None
        changed = 0
        for raw, rec in zip(raws, records, strict=True):
            new_rec = rec.model_copy(update={"prev_hash": prev}).with_hash()
            if new_rec.audit_hash != raw.get("audit_hash"):
                changed += 1
            sealed.append(new_rec)
            prev = new_rec.audit_hash

        # Post-condition: what we would write verifies the way
        # ``sigantry release verify`` checks it, and keeps every record.
        ok, bad_index, why = verify_audit_chain(sealed)
        if not ok or len(sealed) != len(raws):
            raise MigrationRefusedError(
                f"{read_source.name}: re-sealed chain does not verify at record {bad_index}: {why}"
            )

        if dry_run:
            return (len(records), changed)

        if read_source is jsonl_path:
            _write_backup(_backup_path(jsonl_path), original)

        new_lines = [
            json.dumps(s.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            for s in sealed
        ]
        _atomic_replace(jsonl_path, "\n".join(new_lines) + "\n")
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
        help="Verify and report what would change without touching files on disk.",
    )
    args = parser.parse_args(argv)

    audit_dir: Path = args.audit_dir
    if not audit_dir.is_dir():
        print(f"audit dir not found: {audit_dir}", file=sys.stderr)
        return 0  # not a hard error -- a fresh install has no ledger yet.

    for jsonl_path in sorted(audit_dir.glob("*.jsonl")):
        if jsonl_path.name not in _FILENAME_TO_RECORD:
            print(f"skip (unknown filename): {jsonl_path.name}")

    total_processed = 0
    total_changed = 0
    refused: list[str] = []
    for name, record_cls in sorted(_FILENAME_TO_RECORD.items()):
        jsonl_path = audit_dir / name
        try:
            processed, changed = migrate_one_file(
                jsonl_path,
                record_cls=record_cls,
                dry_run=args.dry_run,
            )
        except MigrationRefusedError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            refused.append(name)
            continue
        if processed == 0:
            continue
        if changed == 0:
            action = "already chained"
        else:
            action = "would migrate" if args.dry_run else "migrated"
        print(f"{action}: {name}  records={processed}  rehashed={changed}")
        total_processed += processed
        total_changed += changed

    print(f"\nTotal: records={total_processed}  rehashed={total_changed}")
    if refused:
        print(
            f"{len(refused)} ledger(s) refused and left exactly as found: {', '.join(refused)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
