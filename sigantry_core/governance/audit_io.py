"""Audit-plane file-I/O helpers (Audit-2026-05-07 W2.6 split).

Carved out of :mod:`sigantry_core.governance.audit` so the file-I/O
concerns -- default audit directory, owner-only file mode, fsync-after-
flush write helper -- live in one place. The audit module re-exports
the public names for backwards-compatibility.

The audit jsonl files carry low-grade PII (approver UPNs, release ids,
secret keys without values). Every write goes through
:func:`write_audit_record` which:

1. Creates the parent directory with mode ``0o700`` (owner-only) if
   absent.
2. Opens the jsonl with mode ``0o600`` atomically via ``open()``'s
   ``opener`` kwarg -- avoiding the TOCTOU race that would exist
   between ``exists()`` and a follow-up ``chmod`` (review fix MD-01).
3. ``flush()`` + ``fsync()`` so the line survives a crash.

The default directory is ``~/.sigantry/audit/``; tests override via the
``audit_dir`` kwarg.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any, Final

# The audit-chain lock needs an OS-level file lock. POSIX exposes
# ``fcntl.flock``; Windows exposes ``msvcrt.locking``. Import whichever
# the platform provides -- an unconditional ``import fcntl`` makes every
# ``sigantry`` command crash on Windows with ``ModuleNotFoundError``
# at import time, long before any locking behaviour is reached
# (tester-reported, 2026-06-12).
try:
    import fcntl
except ImportError:  # pragma: no cover -- Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover -- POSIX
    msvcrt = None  # type: ignore[assignment]

from sigantry_core.client.logging import get_correlation_id

_DEFAULT_AUDIT_DIR: Final[Path] = Path.home() / ".sigantry" / "audit"
_AUDIT_FILE_MODE: Final[int] = 0o600
# Initial tail-read window for :func:`read_last_audit_hash`. The window
# DOUBLES until the last line is captured in full, so this is a starting
# size, not a cap -- the old fixed 8-KiB cap truncated any record longer
# than the window mid-line, which then failed to parse and silently forked
# the hash chain (review finding S-T3).
_TAIL_WINDOW_BYTES: Final[int] = 8192


class AuditLedgerCorruptionError(RuntimeError):
    """The audit ledger's last line is present but not parseable as JSON.

    Raised by :func:`read_last_audit_hash` -- which is called inside
    :func:`write_audit_record` under the chain lock -- so a write against a
    corrupt ledger REFUSES rather than silently sealing the next record with
    ``prev_hash=None``. Returning ``None`` on a corrupt-but-present last line
    (the pre-S-T3 behaviour) forked the SHA-256 chain: every subsequent
    record chained off ``None`` and :func:`verify_audit_chain` /
    ``sigantry release verify`` then reported false tampering on an
    otherwise-untampered ledger. Fail loudly instead of extending the fork.
    """


def _audit_file_opener(path: str, flags: int) -> int:
    """``open()`` opener that creates the audit jsonl with mode 0o600.

    The parent directory is 0o700 (owner-only); the jsonl file must match
    that protection level because it carries low-grade PII (approver UPNs,
    release ids). Using ``os.open`` + the ``opener`` kwarg of ``open``
    sets the mode atomically on creation and avoids the TOCTOU race that
    would exist between ``exists()`` and a follow-up ``chmod`` (review fix
    MD-01).
    """
    return os.open(path, flags, _AUDIT_FILE_MODE)


def _acquire_file_lock(lf: IO[str]) -> None:
    """Take a blocking exclusive OS lock on ``lf`` for the current platform.

    POSIX: ``fcntl.flock(LOCK_EX)`` -- blocks until acquired.

    Windows: ``msvcrt.locking(LK_LOCK, 1)`` on byte 0. The byte range is
    relative to the current file position, so the position is pinned to 0
    first (the lock file is always empty; locking past EOF is permitted).
    ``LK_LOCK`` retries internally for ~10 seconds and then raises
    ``OSError``, so contention errnos are looped on to match the blocking
    ``flock`` semantics; any other errno is a real error and re-raises.

    Neither available (no real CPython platform): proceed unserialised --
    the pre-BL-02 behaviour.
    """
    if fcntl is not None:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:
        contention = {errno.EACCES, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
        lf.seek(0)
        while True:
            try:
                msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:
                if exc.errno not in contention:
                    raise


def _release_file_lock(lf: IO[str]) -> None:
    """Release the lock taken by :func:`_acquire_file_lock`."""
    if fcntl is not None:
        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        lf.seek(0)
        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def audit_chain_lock(jsonl_path: Path) -> Iterator[None]:
    """Hold an exclusive OS file lock around an audit-ledger critical section.

    Audit-2026-05-07 review follow-up (BL-02 / concerns H1): the W3.1
    ``prev_hash`` chain is computed by reading the last record's
    ``audit_hash`` and threading it onto the new record before sealing.
    Without inter-process serialisation, two concurrent writers (CLI +
    CI runner; two ``apply_sync`` jobs against the same operator
    workstation) can both read the same predecessor hash and append two
    records that claim the same parent. ``verify_audit_chain`` then
    rejects the file as if it had been tampered with -- the chain
    integrity primitive is broken under any multi-writer load.

    This contextmanager wraps the read-seal-append sequence with an
    exclusive OS lock taken on a sibling ``.lock`` file (so out-of-band
    readers calling :func:`read_last_audit_hash` for inspection do not
    contend). The lock file is created with the same 0o600 mode as the
    ledger via :func:`_audit_file_opener`; the parent directory is
    created with 0o700 if absent.

    The lock primitive is per-platform (see :func:`_acquire_file_lock`):
    ``fcntl.flock`` on POSIX (Ubuntu CI agents, Linux/macOS dev),
    ``msvcrt.locking`` on Windows.

    Example::

        with audit_chain_lock(jsonl_path):
            prev = read_last_audit_hash(jsonl_path)
            sealed = record.model_copy(update={"prev_hash": prev}).with_hash()
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(sealed.model_dump()) + "\n")
    """
    lock_path = jsonl_path.with_suffix(jsonl_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ``a`` so the file is created on first use; we never write to it,
    # only flock on the fd. The opener pins 0o600 mode atomically on
    # creation (matches the ledger's protection level).
    with open(str(lock_path), "a", opener=_audit_file_opener) as lf:
        _acquire_file_lock(lf)
        try:
            yield
        finally:
            # Explicit unlock so the order of fd-close vs lock-release is
            # deterministic across Python implementations. ``close`` would
            # release as a side effect; we don't rely on that.
            _release_file_lock(lf)


def read_last_audit_hash(jsonl_path: Path) -> str | None:
    """Return the ``audit_hash`` of the LAST record in ``jsonl_path``.

    Audit-2026-05-07 W3.1 chain linkage: every audit record carries
    ``prev_hash`` pointing at the previous record's ``audit_hash`` so a
    verifier can detect insertion / deletion / reordering tampering. The
    writer reads the last record's hash and threads it onto the new
    record before sealing.

    Returns ``None`` for an empty or missing file -- the first record on
    the ledger has no predecessor. Returns ``None`` if the last (fully
    read) record parses but carries no ``audit_hash`` (defensive -- a
    future migration that drops the chain field must not silently corrupt
    the next chain link).

    RAISES :class:`AuditLedgerCorruptionError` if the last non-empty line
    is present but does not parse as JSON. This is a change from the
    pre-S-T3 behaviour, which returned ``None`` and thereby forked the
    chain; a corrupt last line is now a hard error so the pending write
    refuses rather than sealing off ``prev_hash=None``.

    Reads only the file tail so multi-MB ledger files do not pay an O(N)
    cost on every write; the window GROWS until the last line is captured
    whole, so records larger than the initial :data:`_TAIL_WINDOW_BYTES`
    are read in full (the old fixed 8-KiB cap truncated them mid-line).
    """
    if not jsonl_path.is_file():
        return None
    size = jsonl_path.stat().st_size
    if size == 0:
        return None
    last_line = _read_last_nonempty_line(jsonl_path, size)
    if last_line is None:
        return None
    text = last_line.decode("utf-8", errors="replace")
    try:
        last = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuditLedgerCorruptionError(
            f"audit ledger {jsonl_path} has an unparseable last line "
            f"({len(last_line)} bytes); refusing to read the chain head. "
            f"A corrupt-but-present last line must not be treated as an "
            f"empty ledger -- that silently forks the SHA-256 chain."
        ) from exc
    last_hash = last.get("audit_hash")
    if isinstance(last_hash, str) and last_hash:
        return last_hash
    return None


def _read_last_nonempty_line(jsonl_path: Path, size: int) -> bytes | None:
    """Return the last newline-delimited non-empty line, read in full.

    Reads the file tail in growing windows -- doubling from
    :data:`_TAIL_WINDOW_BYTES` -- until the last line is captured whole:
    either the window reaches the start of the file, or it contains the
    newline that terminates the *previous* record (so the trailing segment
    is a complete line rather than a front-truncated fragment). This
    removes the fixed-window cap that truncated any record longer than
    8 KiB mid-line -- the truncated JSON then failed to parse and the
    chain forked silently (review finding S-T3).

    Returns ``None`` when the file holds only newlines / whitespace.
    """
    window = _TAIL_WINDOW_BYTES
    with open(str(jsonl_path), "rb") as f:
        while True:
            read_start = max(0, size - window)
            f.seek(read_start)
            data = f.read(size - read_start)
            # Drop trailing record terminator(s): each record is written
            # with a single "\n"; tolerate a stray trailing blank line.
            stripped = data.rstrip(b"\n")
            if not stripped:
                return None
            # Complete once we have reached the start of the file OR
            # captured the newline that precedes the last record.
            if read_start == 0 or b"\n" in stripped:
                return stripped.rsplit(b"\n", 1)[-1]
            # Last line not yet whole and more file remains -- widen. The
            # loop terminates because ``window`` doubles until it covers the
            # whole file, at which point ``read_start == 0``.
            window *= 2


def verify_audit_chain(records: list[Any]) -> tuple[bool, int | None, str | None]:
    """Verify a list of records forms a valid SHA-256 chain.

    Audit-2026-05-07 W3.1: each record's ``prev_hash`` must equal the
    previous record's ``audit_hash`` and each record must satisfy
    :meth:`verify_hash`. Returns ``(True, None, None)`` for a valid
    chain, or ``(False, index, reason)`` for the first violation found.

    The first record is required to carry ``prev_hash is None`` --
    a non-None value on record 0 means the chain head was tampered with
    (e.g. by deleting record 0 and renumbering).

    Parameters
    ----------
    records
        List of audit-record instances. Each must expose a ``verify_hash()``
        method, an ``audit_hash`` attribute, and a ``prev_hash`` attribute
        (``DeployRecord``, ``SecretChangeRecord``, ``ApprovalRecord``, and
        ``BootstrapRecord`` all qualify post-W3.1).

    Returns
    -------
    tuple[bool, int | None, str | None]
        ``(is_valid, first_bad_index, reason)``. On a valid chain
        returns ``(True, None, None)``. On the first violation returns
        ``False`` plus the offending index + a human-readable reason.
    """
    expected_prev: str | None = None
    for i, rec in enumerate(records):
        if not rec.verify_hash():
            return (False, i, f"record {i} fails verify_hash() (audit_hash mismatch)")
        if rec.prev_hash != expected_prev:
            return (
                False,
                i,
                f"record {i} prev_hash={rec.prev_hash!r} but expected {expected_prev!r} "
                f"(audit_hash of record {i - 1 if i > 0 else 'NONE'})",
            )
        expected_prev = rec.audit_hash
    return (True, None, None)


def write_audit_record(
    *,
    target_dir: Path | None,
    filename: str,
    event_name: str,
    record: Any,
    audit_logger: logging.Logger,
) -> None:
    """Write-through to the non-pluggable observation plane (W2.6 dedupe + W3.1 chain).

    Two channels, BOTH bypassing every ``TelemetrySink``:

    1. ``audit_logger.info(event_name, extra={...payload...})`` -- the
       structured-logging side of the audit record.
    2. Append-only jsonl line at ``<target_dir>/<filename>`` with
       ``flush()`` + ``fsync()`` so the line survives a crash.

    Audit-2026-05-07 W3.1: this writer is the chain-linkage authority.
    Each new record is rebound with ``prev_hash`` set to the previous
    record's ``audit_hash`` (or ``None`` for the chain head) before the
    seal. Callers pass the record un-chained; the writer enforces the
    chain so a misconfigured caller can't accidentally write an
    unchained record.

    Behaviour preserved from W2.6: canonicalised JSON (sort_keys,
    ensure_ascii=False), 0o600 file mode + 0o700 parent-dir mode,
    fsync-after-flush.

    Parameters
    ----------
    target_dir
        The audit directory. ``None`` resolves to
        :data:`_DEFAULT_AUDIT_DIR` (``~/.sigantry/audit/``).
    filename
        The jsonl filename within ``target_dir`` (e.g.
        ``"deploys.jsonl"``, ``"approvals.jsonl"``,
        ``"secret_changes.jsonl"``).
    event_name
        The structured-logger event name (e.g. ``"deploy_record"``).
    record
        The audit-record instance to persist. Must expose
        ``.model_copy(update=...)``, ``.with_hash()``, and
        ``.model_dump(mode="json")``. The writer ignores any
        constructor-supplied ``audit_hash`` / ``prev_hash`` -- the
        chain-link is computed against the on-disk state at write
        time.
    audit_logger
        The logger to emit the structured event on. Existing callers
        pass ``sigantry_core.governance.audit.logger`` so the logger
        name is preserved across the split.

    Raises
    ------
    OSError
        If the audit directory cannot be created or the jsonl file
        cannot be written. Audit-plane failures are NOT swallowed --
        a write that did not happen is a release/approval/secret change
        that did not record.
    """
    resolved_dir = target_dir if target_dir is not None else _DEFAULT_AUDIT_DIR
    resolved_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    jsonl_path = resolved_dir / filename

    # Audit-2026-05-07 review follow-up (BL-02): serialise W3.1 chain
    # linkage. The read+seal+append sequence below MUST run under an
    # exclusive flock; without it, two writers race the predecessor-hash
    # read and produce a fork ``verify_audit_chain`` rejects as tampered.
    # The structured-logger emit also lives inside the lock so the order
    # of (logger event, jsonl line) is deterministic across writers.
    with audit_chain_lock(jsonl_path):
        # W3.1: read previous record's audit_hash and seal a chained record.
        prev = read_last_audit_hash(jsonl_path)
        chained = record.model_copy(update={"prev_hash": prev}).with_hash()
        payload = chained.model_dump(mode="json")

        audit_logger.info(
            event_name,
            extra={
                "event": event_name,
                "correlation_id": get_correlation_id(),
                **payload,
            },
        )
        line = json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
        # MD-01 (review fix): open the jsonl with mode 0o600 atomically. The
        # parent directory is 0o700 (owner-only); without this opener kwarg the
        # file inherits the user's umask (typically 0o664) and a different uid
        # on the same host could read approver UPNs and release ids. Using
        # builtin ``open()`` + the ``opener`` kwarg avoids TOCTOU between
        # exists() and chmod. NOTE: ``Path.open`` does not forward ``opener``
        # in Python 3.11, so we go through the builtin and pass ``str(path)``.
        with open(
            str(jsonl_path),
            "a",
            encoding="utf-8",
            opener=_audit_file_opener,
        ) as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


__all__ = [
    "_AUDIT_FILE_MODE",
    "_DEFAULT_AUDIT_DIR",
    "AuditLedgerCorruptionError",
    "_audit_file_opener",
    "audit_chain_lock",
    "read_last_audit_hash",
    "verify_audit_chain",
    "write_audit_record",
]
