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
  ``audit_hash`` in the form it was stored. A pre-W3.1 record was
  sealed before ``prev_hash`` existed and carries no key for it, so the
  current model's ``verify_hash()`` cannot judge it. Pre-W3.1 records
  may only form a prefix; from the first record that carries
  ``prev_hash`` on, every record must link to its predecessor. A ledger
  that fails either check is REFUSED: nothing is written and the script
  exits non-zero. Re-sealing an unverifiable record, or a chain that was
  truncated or reset, would launder it into a chain that
  ``sigantry release verify`` reports as valid.
- **Read the live ledger, and account for every backup.** A
  ``<filename>.pre-w3.1.bak`` backup is a read source only when the live
  file is absent (a run of the previous version of this script
  interrupted between its rename and its write). When the live file
  exists, every backup beside it must be contained in it -- its records,
  in order, at the head of the live ledger. Otherwise the ledger is
  refused: a backup holding records the live ledger lacks is the only
  copy of those records.
- **Hold the writer's lock.** The read-verify-write sequence runs under
  :func:`audit_chain_lock`, the lock every audit writer takes, so no
  record can be appended between the read and the replace.
- **Never clobber a backup.** The live file is copied (not renamed) to
  ``<filename>.pre-w3.1.bak``, or to ``.pre-w3.1.bak.<n>`` when earlier
  backups exist; the backup appears under its name only once complete.
  The live ledger is then atomically replaced, so it is never absent.
- **Check what was written.** The serialised result is parsed back and
  must verify as a whole chain with the same number of records.

Idempotency: a ledger whose whole chain already verifies is left
untouched, so the script can be run any number of times.

Usage::

    # Migrate the default directory:
    python scripts/audit_chain_migrate.py

    # Migrate a custom directory:
    python scripts/audit_chain_migrate.py --audit-dir /path/to/audit

    # Dry-run: verifies and refuses exactly like a real run, but takes no
    # lock and creates no file (so it also works on a read-only copy):
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
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
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
_PUBLISH_ATTEMPTS = 1000


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
    try:  # a lone-surrogate escape decodes but cannot be re-encoded
        canonical = json.dumps(body, **_CANONICAL_KWARGS).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hashlib.sha256(canonical).hexdigest() == stored


def _chain_violation(raws: list[dict[str, Any]]) -> str | None:
    """Return why ``raws`` must not be re-sealed, or None if it may be.

    A pre-W3.1 record carries no ``prev_hash`` key. A ledger may start
    with any number of them; the W3.1 writer then links its first record
    onto the last of them. So:

    - every record must match its own ``audit_hash`` as stored;
    - the first record that carries ``prev_hash`` starts the chain, and
      from there every record must carry it and point at its
      predecessor's ``audit_hash`` (``None`` only at index 0). A null
      link later on is a reset head -- what a truncated ledger or a
      writer that lost the tail produces -- and ``release verify``
      rejects it, so re-sealing it would launder it.
    """
    chain_started = False
    for i, raw in enumerate(raws):
        if not _stored_hash_ok(raw):
            return (
                f"record {i} fails verification: it does not match its own "
                "audit_hash (edited or corrupt)"
            )
        if "prev_hash" not in raw:
            if chain_started:
                return f"record {i} has no prev_hash but follows chained records"
            continue
        chain_started = True
        expected = None if i == 0 else raws[i - 1].get("audit_hash")
        if raw["prev_hash"] == expected:
            continue
        if i == 0:
            return "record 0 links to a predecessor the ledger does not hold (truncated head)"
        if raw["prev_hash"] is None:
            return f"record {i} restarts the chain (prev_hash is null): reset or forked chain head"
        return (
            f"record {i} prev_hash does not match record {i - 1}'s audit_hash (broken chain link)"
        )
    return None


def _is_already_chained(raws: list[dict[str, Any]]) -> bool:
    """Return True if the WHOLE ledger is already a verified W3.1 chain.

    Every record carries the ``prev_hash`` key and :func:`_chain_violation`
    finds nothing. Checking only the first two records (as this function
    once did) passes a ledger whose later links are broken and leaves it
    broken.
    """
    if not all("prev_hash" in raw for raw in raws):
        return False
    return _chain_violation(raws) is None


def _parse(
    data: bytes, *, record_cls: type, source: Path
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Parse a ledger the way the audit readers do, refusing anything invalid.

    Lines are split on ``\\n`` only (a trailing ``\\r`` is dropped).
    ``str.splitlines()`` would also split on U+2028, U+2029 and U+0085,
    which the writers emit raw inside string fields (``ensure_ascii=False``),
    and so would break a genuine record in two.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationRefusedError(f"{source.name} is not valid UTF-8: {exc}") from exc
    raws: list[dict[str, Any]] = []
    records: list[Any] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        line = line.removesuffix("\r")
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            records.append(record_cls(**raw))
        except (ValueError, TypeError, ValidationError, RecursionError) as exc:
            raise MigrationRefusedError(
                f"{source.name} line {lineno} is not a valid {record_cls.__name__}: {exc!r:.300}"
            ) from exc
        raws.append(raw)
    return raws, records


def _content(record: Any) -> dict[str, Any]:
    """A record's content without its chain fields, normalised by the model."""
    dumped: dict[str, Any] = record.model_dump(mode="json")
    dumped.pop("prev_hash", None)
    dumped.pop("audit_hash", None)
    return dumped


def _base_backup(jsonl_path: Path) -> Path:
    return jsonl_path.with_name(jsonl_path.name + _BACKUP_SUFFIX)


def _backups(jsonl_path: Path) -> list[Path]:
    """The backups this script writes: ``.pre-w3.1.bak`` then ``.bak.<n>`` by n.

    Only an ASCII-decimal suffix counts (``str.isdigit`` also accepts
    characters such as superscripts, which ``int()`` rejects); any other
    ``.bak.<suffix>`` file is not ours and is left alone.
    """
    base = _base_backup(jsonl_path)
    numbered: list[tuple[int, Path]] = []
    for p in jsonl_path.parent.glob(glob.escape(base.name) + ".*"):
        suffix = p.name[len(base.name) + 1 :]
        if re.fullmatch(r"[0-9]+", suffix) and p.is_file():
            numbered.append((int(suffix), p))
    return ([base] if base.is_file() else []) + [p for _, p in sorted(numbered)]


def _refuse_unless_backups_contained(
    jsonl_path: Path, live_records: list[Any], *, record_cls: type
) -> None:
    """Refuse if any backup holds records the live ledger does not.

    A completed migration leaves the backup's records, re-sealed, at the
    head of the live ledger. If the old version of this script was
    interrupted after its rename and a writer then started a fresh
    ledger, the pre-migration records exist ONLY in the backup -- and the
    live ledger alone would pass as "already chained".
    """
    live = [_content(r) for r in live_records]
    for backup in _backups(jsonl_path):
        _, backup_records = _parse(backup.read_bytes(), record_cls=record_cls, source=backup)
        held = [_content(r) for r in backup_records]
        if live[: len(held)] != held:
            missing = next(
                (i for i, c in enumerate(held) if i >= len(live) or live[i] != c), len(held)
            )
            raise MigrationRefusedError(
                f"{backup.name} holds {len(held)} record(s) that are not all at the head of "
                f"{jsonl_path.name} (first difference at backup record {missing}); the backup "
                "may be their only copy -- reconcile it by hand"
            )


def _new_backup_path(jsonl_path: Path) -> Path:
    """Return the first backup name that is free.

    ``os.path.lexists``, not ``Path.exists``: a dangling or looping symlink
    at a backup name makes ``exists()`` report False while the link/rename
    that follows raises ``FileExistsError`` -- the same name forever.
    """
    base = _base_backup(jsonl_path)
    if not os.path.lexists(base):
        return base
    n = 1
    while os.path.lexists(base.with_name(f"{base.name}.{n}")):
        n += 1
    return base.with_name(f"{base.name}.{n}")


def _write_exclusive(target: Path, data: bytes) -> None:
    """Create ``target`` (must not exist) holding ``data``: owner-only, binary, fsynced."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(str(target), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            target.unlink()
        raise


def _write_backup(jsonl_path: Path, data: bytes) -> Path:
    """Write ``data`` to a NEW backup file; it appears under its name only once complete.

    The bytes go to a temp file in the same directory (``mkstemp``: owner-only,
    binary) and are fsynced, then published under the backup name without
    overwriting anything: a hard link on POSIX (``os.rename`` would replace an
    existing file there), ``os.rename`` on Windows (which refuses to). Where
    the filesystem has no hard links (vfat, exFAT, many SMB/FUSE mounts) the
    backup is created directly under its name with ``O_EXCL`` instead.
    """
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{jsonl_path.name}.", suffix=".bak.tmp", dir=str(jsonl_path.parent)
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(_PUBLISH_ATTEMPTS):
            target = _new_backup_path(jsonl_path)
            try:
                if os.name == "nt":
                    os.rename(tmp, target)
                else:
                    try:
                        os.link(tmp, target)
                    except FileExistsError:
                        raise
                    except OSError:  # no hard links on this filesystem
                        _write_exclusive(target, data)
            except FileExistsError:
                continue
            return target
        raise MigrationRefusedError(
            f"could not find a free backup name beside {jsonl_path.name} "
            f"after {_PUBLISH_ATTEMPTS} attempts"
        )
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


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
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
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


@contextlib.contextmanager
def _no_lock(_path: Path) -> Iterator[None]:
    yield


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
    was already chained.

    Raises :class:`MigrationRefusedError`, having written nothing, when
    the ledger fails a pre-seal check (see the module docstring).
    """
    backup = _base_backup(jsonl_path)
    if jsonl_path.is_symlink():
        raise MigrationRefusedError(
            f"{jsonl_path.name} is a symlink; replacing it would detach the ledger from "
            "its target -- migrate the target directory instead"
        )
    if not jsonl_path.is_file() and not _backups(jsonl_path):
        return (0, 0)

    # A dry run writes nothing, so it takes no lock (which would create a
    # lock file). A concurrent write can make it report a transient refusal.
    lock = _no_lock if dry_run else audit_chain_lock
    with lock(jsonl_path):
        # Choose the read source under the lock, so no writer can change
        # the answer between the choice and the read.
        backups = _backups(jsonl_path)
        if jsonl_path.is_file():
            read_source = jsonl_path
        elif backups:
            # Audit-2026-05-08 review follow-up (WR-03): a run of the
            # previous version of this script renamed the ledger to the
            # backup and was interrupted before writing the migrated
            # file. The backup holds the full pre-migration content --
            # but only if it is the ONLY backup. Numbered backups come
            # from this version, which never leaves the ledger absent,
            # so their presence means something else removed it.
            if backups != [backup]:
                raise MigrationRefusedError(
                    f"{jsonl_path.name} is missing but backups exist "
                    f"({', '.join(p.name for p in backups)}) that this recovery cannot "
                    "choose between; restore the ledger by hand"
                )
            read_source = backup
        else:  # removed while we waited for the lock
            return (0, 0)

        original = read_source.read_bytes()
        raws, records = _parse(original, record_cls=record_cls, source=read_source)
        recovering = read_source is not jsonl_path
        if not recovering:
            _refuse_unless_backups_contained(jsonl_path, records, record_cls=record_cls)

        if _is_already_chained(raws):
            # Intact as stored; it must also verify the way ``release verify``
            # checks it (through the current model). A difference means the
            # model drifted from what was sealed -- re-sealing would change
            # every hash, which is not this script's call.
            ok, bad_index, why = verify_audit_chain(records)
            if not ok:
                raise MigrationRefusedError(
                    f"{read_source.name}: the chain is intact as stored but does not verify "
                    f"under the current record model (record {bad_index}: {why}); "
                    "reconcile it by hand"
                )
            if not recovering:
                return (len(records), 0)
            changed = 0  # restore the chained backup as the live ledger
            sealed = records
        else:
            reason = _chain_violation(raws)
            if reason is not None:
                raise MigrationRefusedError(f"{read_source.name}: {reason}")
            sealed = []
            prev = None
            changed = 0
            for raw, rec in zip(raws, records, strict=True):
                new_rec = rec.model_copy(update={"prev_hash": prev}).with_hash()
                if new_rec.audit_hash != raw.get("audit_hash"):
                    changed += 1
                sealed.append(new_rec)
                prev = new_rec.audit_hash

        new_text = "".join(
            json.dumps(s.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) + "\n"
            for s in sealed
        )
        # Post-condition on what would actually be written: parsed back,
        # it keeps every record and verifies the way ``sigantry release
        # verify`` checks it.
        _, reread = _parse(new_text.encode("utf-8"), record_cls=record_cls, source=jsonl_path)
        ok, bad_index, why = verify_audit_chain(reread)
        if not ok or len(reread) != len(raws):
            raise MigrationRefusedError(
                f"{read_source.name}: re-sealed ledger does not verify when read back "
                f"({len(reread)} of {len(raws)} records; record {bad_index}: {why})"
            )

        if dry_run:
            return (len(records), changed)

        if not recovering:
            _write_backup(jsonl_path, original)
        _atomic_replace(jsonl_path, new_text)
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
        help=(
            "Verify and report what would change. Takes no lock and creates no file; "
            "refuses exactly as a real run would."
        ),
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
        recovering = not os.path.lexists(jsonl_path)
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
        except OSError as exc:
            # Unreadable file, lock or filesystem failure: this ledger is not
            # migrated (a failed write leaves it as it was -- the replace is
            # atomic), and the others still run.
            print(f"FAILED: {name}: {exc!r}", file=sys.stderr)
            refused.append(name)
            continue
        if processed == 0 and not jsonl_path.exists():
            continue
        if recovering:
            action = "would recover from backup" if args.dry_run else "recovered from backup"
        elif changed == 0:
            action = "already chained"
        else:
            action = "would migrate" if args.dry_run else "migrated"
        print(f"{action}: {name}  records={processed}  rehashed={changed}")
        total_processed += processed
        total_changed += changed

    print(f"\nTotal: records={total_processed}  rehashed={total_changed}")
    if refused:
        print(
            f"{len(refused)} ledger(s) refused or failed and left as found: {', '.join(refused)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
