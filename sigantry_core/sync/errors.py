"""Typed exceptions for ``sigantry_core.sync`` (Phase 13 / D-32).

Plan 13-01 seeds the module with the manifest-validation exception. The
remaining typed exceptions land in their respective plans:

- Plan 13-04 (apply): ``WorkspacePendingGitUpdateError``,
  ``ReconcilerWrapError``.
- Plan 13-05 (pull): ``PullTargetNotEmptyError``,
  ``PullDefinitionFetchError``.
- Plan 13-06 (diff / notifications): drift- and sink-specific errors.

All exceptions inherit from :class:`SyncEngineError` so callers can catch
the whole ``sigantry_core.sync`` surface with a single ``except`` clause.
The structured ``violations`` payload carried by
:class:`ManifestValidationError` lets downstream renderers (CLI, log
sinks) emit actionable messages without re-parsing pydantic
``ValidationError`` text.
"""

from __future__ import annotations


class SyncEngineError(RuntimeError):
    """Base class for every ``sigantry_core.sync`` exception (D-32)."""


class ManifestValidationError(SyncEngineError):
    """Raised when ``sync.yml`` fails pydantic / cross-field validation.

    Each violation is a ``dict`` carrying at minimum:

    - ``field`` (``str``): dotted path within the manifest (e.g.
      ``items.0.target_folder``).
    - ``decision_id`` (``str``): the locked CONTEXT.md / SPEC.md
      reference (``"D-05"``, ``"D-06"``, ``"D-05/Council-D-3"``,
      ``"D-05/Council-D-4"``, ``"D-07/SYNC-06"`` etc.).
    - ``severity`` (``str``): ``"error"`` or ``"warning"``. The
      folder-less item type override surfaces as a warning entry on the
      validation log -- it does not raise; only ``"error"`` entries
      cause :func:`load_manifest` to raise.
    """

    def __init__(self, message: str, *, violations: list[dict] | None = None) -> None:
        super().__init__(message)
        self.violations: list[dict] = violations or []


class WorkspacePendingGitUpdateError(SyncEngineError):
    """Workspace has pending Git Sync updates; ``sync apply`` refuses (D-18).

    Carries ``sync_state`` so callers can render the actual workspace
    status in the user-facing error message (e.g. "Pending",
    "ConflictingChanges"). Raised by the apply pre-flight when
    ``Get Workspace`` reports a ``gitConnection.sync_state`` other than
    ``"Synced"`` or absent (no Git binding -> safe to apply).
    """

    def __init__(self, message: str, *, sync_state: str | None = None) -> None:
        super().__init__(message)
        self.sync_state = sync_state


class ReconcilerWrapError(SyncEngineError):
    """The wrapped Phase 3 reconciler failed; preserves the underlying cause.

    Raised by :func:`sigantry_core.sync.apply.apply_sync` when the
    nested ``reconcile_folders_from_repo`` call raises any exception.
    The original exception is preserved via Python's ``__cause__`` so
    callers and the audit ledger can still inspect the root failure
    while presenting one stable typed surface to operators.
    """


class SyncPublishError(SyncEngineError):
    """Raised when fabric-cicd publish fails inside the sync apply --with-publish path.

    Phase 17 / SYNC-PUBLISH typed wrapper around fabric-cicd exceptions
    raised inside :func:`sigantry_core.deploy.sync_publish.publish_absent_items`.
    The wrapped ``str(exc)`` is bounded -- it never includes the raw
    fabric-cicd traceback, ``parameters.yml`` content, or any kwarg value
    of the wrapped exception (T-17-05 mitigation). The original exception
    is chained via ``raise SyncPublishError(...) from exc`` so the
    traceback survives in stderr but not in the wrapped message itself.

    Inherits from :class:`SyncEngineError` so the existing CLI exit-code
    mapping (1 for SyncEngineError) holds without widening the exit-code
    surface (Decision D-17-06 / runbook §1.3).
    """


class PullTargetNotEmptyError(SyncEngineError):
    """``sync pull --into <dir>`` refused: target exists and is non-empty (D-21).

    Carries ``target`` so the CLI / programmatic caller can render the
    exact path it tried to write into. Operators must either point
    ``--into`` at a fresh directory or pass ``--force`` to acknowledge
    they accept the clobber semantics.
    """

    def __init__(self, message: str, *, target: str | None = None) -> None:
        super().__init__(message)
        self.target = target


class PullDefinitionFetchError(SyncEngineError):
    """REST ``Get<Item>Definition`` failed for one item (D-32).

    Raised by :func:`sigantry_core.sync.pull.pull_workspace` when the
    nested ``client.send`` (or its 202 LRO unwrap inside
    ``BaseRestClient.send``) raises during the per-item definition
    fetch loop. The original exception is preserved via Python's
    ``__cause__`` so the underlying HTTP / auth failure stays
    inspectable while the caller can catch one stable typed surface.
    """


class PullDefinitionPathTraversalError(PullDefinitionFetchError):
    """Server-supplied part path escapes ``source_dir`` (CR-01 fix).

    Raised by :func:`sigantry_core.sync.pull._write_definition_parts`
    when a Fabric ``Get<Item>Definition`` response returns a part whose
    ``path`` resolves OUTSIDE the per-item source directory (path
    traversal -- ``..`` segments, absolute paths on POSIX, drive
    letters on Windows). The error halts the pull mid-flight; the
    operator must investigate the workspace before retrying.

    Inherits from :class:`PullDefinitionFetchError` so existing
    ``except PullDefinitionFetchError`` callers continue to catch this
    case while gaining access to the more-specific subclass for
    targeted handling.
    """


class PullDefinitionDecodeError(PullDefinitionFetchError):
    """Server-supplied base64 payload failed to decode (WR-02 fix).

    Raised by :func:`sigantry_core.sync.pull._write_definition_parts`
    when ``base64.b64decode(payload, validate=True)`` raises
    ``binascii.Error`` / ``ValueError`` for one part. Carries the
    offending item's ``display_name``, ``logical_id``/``id``, and the
    part's ``path`` so the operator can pinpoint the bad data without
    re-running with verbose tracing.

    Inherits from :class:`PullDefinitionFetchError` so existing
    ``except PullDefinitionFetchError`` callers continue to catch this
    case.
    """


__all__ = (
    "ManifestValidationError",
    "PullDefinitionDecodeError",
    "PullDefinitionFetchError",
    "PullDefinitionPathTraversalError",
    "PullTargetNotEmptyError",
    "ReconcilerWrapError",
    "SyncEngineError",
    "SyncPublishError",
    "WorkspacePendingGitUpdateError",
)
