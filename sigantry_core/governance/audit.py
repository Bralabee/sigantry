"""Audit-plane emitters + W2.6 back-compat re-exports.

Audit-2026-05-07 W2.6 split: this module previously held three concerns
in 378 lines -- the destructive-op decorator, the file-I/O helpers, and
the three ``emit_*_record`` functions. The decorator and file helpers
are now in :mod:`sigantry_core.governance.destructive` and
:mod:`sigantry_core.governance.audit_io`; the three emit functions stay
here (this is the public import surface that downstream code reaches
for) and now delegate the file write through
:func:`sigantry_core.governance.audit_io.write_audit_record` so the
~30-line body each emitter previously carried collapses to a single
``payload = record.model_dump(...); write_audit_record(...)`` call.

Backwards-compatibility surface (used by 30+ callers across the
toolkit + tests):

- :class:`DestructiveOpError`, :func:`destructive_op` -- re-exported
  from :mod:`.destructive`.
- :data:`_DEFAULT_AUDIT_DIR`, :data:`_AUDIT_FILE_MODE`,
  :func:`_audit_file_opener` -- re-exported from :mod:`.audit_io`
  (private-prefixed names; ``release/ledger.py`` uses
  ``_DEFAULT_AUDIT_DIR``).
- :data:`logger` -- re-exported from :mod:`.destructive` so any caller
  that monkey-patches ``sigantry_core.governance.audit.logger`` keeps
  working (the logger NAME is the same -- ``sigantry_core.governance.audit``
  -- so JSONL log consumers / journald filters need no change).

Original module docstring (preserved for context):

Every write that deletes or takes offline a user-visible resource
decorates its entry point with ``@destructive_op(resource_kind, action)``.
Tokens themselves are never read or logged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sigantry_core.governance.audit_io import (
    _DEFAULT_AUDIT_DIR,
    write_audit_record,
)
from sigantry_core.governance.destructive import (
    DestructiveOpError,
    destructive_op,
    logger,
)

if TYPE_CHECKING:
    from sigantry_core.governance.records import (
        ApprovalRecord,
        DestructiveOpRecord,
        SecretChangeRecord,
    )
    from sigantry_core.release.record import DeployRecord


def emit_deploy_record(
    record: DeployRecord,
    *,
    audit_dir: Path | None = None,
) -> None:
    """Write-through to the non-pluggable observation plane (TRACE-07).

    Two channels, BOTH bypassing every ``TelemetrySink``:

    1. ``logger.info("deploy_record", extra={...record fields...})`` on
       the same ``sigantry_core.governance.audit`` logger that handles
       ``@destructive_op`` events.
    2. Append-only jsonl line at ``<audit_dir>/deploys.jsonl`` with
       ``flush()`` + ``fsync()`` so the line survives a crash.

    A misconfigured or hostile ``TelemetrySink`` plugin CANNOT suppress
    either channel -- neither is routed through any sink registry. The
    audit plane is non-pluggable by design; adding an ``AuditSink``
    Protocol seam is an explicit anti-pattern (RESEARCH.md line 396).

    Parameters
    ----------
    record : DeployRecord
        The audit record to persist. Must already have ``audit_hash``
        populated via ``.with_hash()``; this function does NOT compute
        it. The record is serialised via ``model_dump(mode="json")``
        identically to the canonicalisation that produced the hash, so
        a reader can ``DeployRecord(**json.loads(line)).verify_hash()``
        against any line on disk.
    audit_dir : Path | None
        Override the default ``~/.sigantry/audit/`` directory. Used by
        tests with ``tmp_path`` and by CLI consumers passing
        ``--audit-dir`` (Plan 11-06).

    Raises
    ------
    OSError
        If the audit directory cannot be created or the jsonl file
        cannot be written. Audit-plane failures are NOT swallowed --
        a write that did not happen is a release that did not record.
    """
    write_audit_record(
        target_dir=audit_dir if audit_dir is not None else _DEFAULT_AUDIT_DIR,
        filename="deploys.jsonl",
        event_name="deploy_record",
        record=record,
        audit_logger=logger,
    )


def emit_approval_record(
    record: ApprovalRecord,
    *,
    audit_dir: Path | None = None,
) -> None:
    """Write-through to the non-pluggable observation plane (Phase 16 / SEAM-03).

    Same shape as :func:`emit_deploy_record` and
    :func:`emit_secret_change_record`: structured logger event +
    append-only jsonl line at ``<audit_dir>/approvals.jsonl`` with file
    mode 0o600 + parent directory mode 0o700. Audit-plane failures are
    NOT swallowed -- a write that did not happen is an approval that
    did not record.

    ApprovalRecord NEVER carries a token or webhook URL (pydantic
    ``extra='forbid'`` on the model schema). The ``audit_hash`` covers
    metadata only -- ``request_id`` + ``release_id`` + ``env`` +
    ``approvers`` + ``outcome`` + ``decided_by`` + ``decided_at`` +
    ``last_observed_status``.

    The ``last_observed_status`` field carries the upstream poll-loop
    status at the moment the gate returned (RESEARCH §Pitfall 4). It is
    the signal that distinguishes a CLIENT-side timeout (we stopped
    polling; ADO still says ``pending``) from a server-side rejection
    (ADO terminal status is ``rejected`` / ``canceled``).

    Parameters
    ----------
    record : ApprovalRecord
        The audit record to persist. Must already have ``audit_hash``
        populated via :meth:`ApprovalRecord.with_hash`; this function
        does NOT compute it.
    audit_dir : Path | None
        Override the default ``~/.sigantry/audit/`` directory.

    Raises
    ------
    OSError
        If the audit directory cannot be created or the jsonl file
        cannot be written.
    """
    write_audit_record(
        target_dir=audit_dir if audit_dir is not None else _DEFAULT_AUDIT_DIR,
        filename="approvals.jsonl",
        event_name="approval_record",
        record=record,
        audit_logger=logger,
    )


def emit_secret_change_record(
    record: SecretChangeRecord,
    *,
    audit_dir: Path | None = None,
) -> None:
    """Write-through to the non-pluggable observation plane (Phase 16 / SEAM-02).

    Same shape as :func:`emit_deploy_record`: structured logger event +
    append-only jsonl line at ``<audit_dir>/secret_changes.jsonl`` with
    file mode 0o600 + parent directory mode 0o700. Audit-plane failures
    are NOT swallowed -- a write that did not happen is a secret change
    that did not record.

    SecretChangeRecord NEVER carries the secret value itself (pydantic
    ``extra='forbid'`` on the model schema). The ``audit_hash`` covers
    metadata only -- ``operation`` + ``key`` + ``store_name`` + ``actor``
    + ``timestamp``.

    Parameters
    ----------
    record : SecretChangeRecord
        The audit record to persist. Must already have ``audit_hash``
        populated via :meth:`SecretChangeRecord.with_hash`; this function
        does NOT compute it.
    audit_dir : Path | None
        Override the default ``~/.sigantry/audit/`` directory.

    Raises
    ------
    OSError
        If the audit directory cannot be created or the jsonl file
        cannot be written.
    """
    write_audit_record(
        target_dir=audit_dir if audit_dir is not None else _DEFAULT_AUDIT_DIR,
        filename="secret_changes.jsonl",
        event_name="secret_change_record",
        record=record,
        audit_logger=logger,
    )


def emit_destructive_op_record(
    record: DestructiveOpRecord,
    *,
    audit_dir: Path | None = None,
) -> None:
    """Write-through to the non-pluggable observation plane (W3.1 follow-up).

    Audit-2026-05-07 W3.1 (Wave 1 re-audit follow-up): the
    ``@destructive_op`` decorator previously only emitted a structured
    logger event -- whether the audit reached durable storage depended
    entirely on the operator's logging config. This writer brings
    destructive-op auditing into parity with ``emit_deploy_record`` /
    ``emit_approval_record`` / ``emit_secret_change_record``: an
    append-only ``destructive_ops.jsonl`` ledger with file mode 0o600,
    fsync-after-flush, and ``prev_hash`` chain linkage.

    Audit-plane failures are NOT swallowed -- a write that did not
    happen is a destructive op that did not record.

    Parameters
    ----------
    record : DestructiveOpRecord
        The audit record to persist. The writer recomputes
        ``prev_hash`` + ``audit_hash`` against the on-disk ledger
        state, so callers may pass the record un-chained.
    audit_dir : Path | None
        Override the default ``~/.sigantry/audit/`` directory.

    Raises
    ------
    OSError
        If the audit directory cannot be created or the jsonl file
        cannot be written.
    """
    write_audit_record(
        target_dir=audit_dir if audit_dir is not None else _DEFAULT_AUDIT_DIR,
        filename="destructive_ops.jsonl",
        event_name="destructive_op_record",
        record=record,
        audit_logger=logger,
    )


# Audit-2026-05-08 review follow-up (IN-02): the W2.6 split moved the
# audit-file primitives (``_AUDIT_FILE_MODE``, ``_DEFAULT_AUDIT_DIR``,
# ``_audit_file_opener``) to ``governance.audit_io``. They previously
# appeared in this module's ``__all__`` for re-export convenience --
# but ``__all__`` documents *public* surface, and the underscore prefix
# signals private. Mixing the two sent a confusing signal. Consumers
# that need those primitives should import them directly from
# ``governance.audit_io`` (the canonical source).
__all__ = [
    "DestructiveOpError",
    "destructive_op",
    "emit_approval_record",
    "emit_deploy_record",
    "emit_destructive_op_record",
    "emit_secret_change_record",
    "logger",
]
