"""``sync apply`` -- push engine wrapping ``reconcile_folders_from_repo`` (Phase 13 / SYNC-04).

Execution flow (D-17, W9-resolved):

1. Load manifest via :func:`load_manifest`; call ``.normalised()`` to
   apply the SYNC-06 folder-less-type override.
2. Build / borrow a :class:`FabricRestClient`.
3. Pre-flight: check ``Get Workspace``'s ``gitConnection.sync_state`` --
   refuse if pending (D-18). NO separate snapshot pre-fetch (W9 -- the
   reconciler does its own ``list_folders`` / ``list_items`` internally;
   an extra snapshot here would issue redundant REST calls without
   consuming the result).
4. Build a tempdir staging tree; resolve a packager per manifest item via
   :data:`PACKAGER_REGISTRY`; pack each item. Packagers run BEFORE the
   ``--dry-run`` early-return so logical_ids ARE persisted to the sidecar
   regardless of mode (W5 / D-11).
5. Call :func:`reconcile_folders_from_repo(client, workspace_id,
   repository_directory=staging, apply=not dry_run)`.
6. Build a :class:`DeployRecord` with ``release_id = f"sync-{utc_iso}"``,
   ``provider = "sync-engine"``,
   ``fabric_items_changed = [f"{item.display_name}.{item.type}" for item
   in manifest.items]`` and call :func:`emit_deploy_record`.
7. On success: clean up tempdir.
   On failure: PRESERVE tempdir, print path to stderr, emit DeployRecord
   with ``test_evidence['sync_engine_outcome'] = "failed"``.

WR-03 (Phase 12 ledger forward-compat): we cannot add ``outcome`` /
``failure_reason`` as new top-level fields without bumping the
:class:`DeployRecord` schema; we encode them inside ``test_evidence`` as
``{"sync_engine_outcome": "...", "sync_engine_failure": "..."}`` until
v3.x widens the schema.

Plan-checker resolution cross-references:

* W1 -- single canonical access pattern for :class:`ReconcileReport`.
  Both dry-run and success branches read ``report.plan.create_folders``
  + ``report.plan.move_items``. Per the canonical
  :class:`sigantry_core.workspace.reconciler.ReconcileReport` dataclass,
  ``report.plan.*`` carries the PLANNED operations regardless of whether
  ``apply=True`` ran; ``report.folders_created`` / ``report.items_moved``
  are populated only when ``apply=True``. Reading both metrics off
  ``report.plan.*`` keeps the access pattern consistent across the two
  branches.
* W5 -- packager loop runs BEFORE the ``if dry_run:`` early-return so
  logical_ids ARE persisted to the sidecar even on ``--dry-run``. Per
  D-11 the sidecar must be initialised the same way regardless of mode
  so that the subsequent real apply uses the same logical_ids
  deterministically.
* W6 -- ``manifest`` is pre-bound to ``None`` BEFORE the try/except so
  the outer ``except`` block can guard ``if manifest is not None``
  before emitting a DeployRecord.
* W9 -- redundant ``snapshot_workspace`` call removed. The
  reconciler does its own ``list_folders`` / ``list_items`` internally.
  The snapshot helper IS imported by ``sigantry_core.sync.diff`` and
  ``sigantry_core.sync.pull`` (later plans); apply does not need it.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.deploy.parameters import (
    load_and_validate,
    write_substituted_parameters,
)
from sigantry_core.deploy.sync_publish import (
    publish_absent_items,
)
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord
from sigantry_core.sync.errors import (
    ReconcilerWrapError,
    SyncPublishError,
    WorkspacePendingGitUpdateError,
)
from sigantry_core.sync.manifest import SyncItem, SyncManifest, load_manifest
from sigantry_core.sync.packagers import PACKAGER_REGISTRY
from sigantry_core.sync.snapshot import snapshot_workspace
from sigantry_core.workspace.reconciler import (
    ReconcileReport,
    reconcile_folders_from_repo,
)

logger = logging.getLogger("sigantry_core.sync.apply")


@dataclass(frozen=True, slots=True)
class SyncApplyReport:
    """Operator-facing summary of one ``apply_sync`` invocation.

    ``staging_dir`` is ``None`` after a clean success (cleaned up) or
    after a clean dry-run; on failure it carries the absolute Path of
    the preserved tempdir (D-17).
    """

    workspace_id: str
    manifest_path: str
    items_packaged: int
    folders_created: int
    items_moved: int
    deploy_record_release_id: str
    outcome: str  # "succeeded" | "failed" | "dry_run"
    failure_reason: str | None
    staging_dir: Path | None
    # S-T1a: the publish-side outcome, carried alongside the (already
    # committed) reconcile ``outcome``. ``None`` on every non-publish path
    # -- the default reconcile-only apply, ``--dry-run``, and any failure
    # raised before the publish call. On the ``--with-publish`` path it
    # mirrors :class:`PublishResult.outcome`
    # (``"succeeded"`` | ``"partial-failure"`` | ``"failed"``).
    # PUBLISH-05 keeps the folder reconcile committed even when the publish
    # fails, so ``outcome`` stays ``"succeeded"``; ``publish_outcome`` is
    # the field the CLI inspects to decide the process exit code, so a
    # failed publish can no longer read as a green ``sync apply``.
    publish_outcome: str | None = None
    failed_item: str | None = None


def _utc_iso_with_z() -> str:
    """Deterministic D-14 timestamp pattern: ``<YYYY-MM-DDTHH-MM-SSZ>``.

    Used to build ``release_id = f"sync-{_utc_iso_with_z()}"``. The
    pattern uses dashes between hours/minutes/seconds so the resulting
    release_id stays filename-safe across all platforms.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _pack_phase(manifest, manifest_dir: Path, staging_dir: Path) -> None:
    """W4.2 phase: pack every manifest item into the staging tree.

    Walks the registered packagers from ``PACKAGER_REGISTRY`` and
    invokes each one against the manifest's items. Resolves
    ``item.local_path`` relative to ``manifest_dir`` (D-22 round-trip:
    operator-authored manifests carry paths relative to the manifest
    file itself, not the CWD).

    Audit-2026-05-07 W4.2 extraction: this loop previously lived inline
    in ``apply_sync`` Step 5. Extracted so the outer orchestrator
    shrinks to ~80 LOC and the packaging concern is independently
    testable.

    Raises ``ReconcilerWrapError`` when an item type has no registered
    packager (catches manifest authors that drift ahead of the toolkit's
    type registry).
    """
    # Audit-2026-05-08 review follow-up (BL-03): containment check
    # is the load-bearing security boundary for the path-traversal
    # vector. The ``..``-segment guard in ``SyncItem._validate_local_path``
    # catches the syntactic case; this check catches absolute paths
    # pointing outside the manifest dir AND symlinks within
    # manifest_dir whose target lives outside. ``Path.resolve()``
    # follows symlinks, then ``relative_to`` asserts the real path
    # stays inside ``manifest_dir.resolve()``.
    manifest_dir_resolved = manifest_dir.resolve()
    for item in manifest.items:
        packager = PACKAGER_REGISTRY.get(item.type)
        if packager is None:
            raise ReconcilerWrapError(
                f"No packager registered for type {item.type!r}; "
                f"available: {sorted(PACKAGER_REGISTRY)}"
            )
        local_path = Path(item.local_path)
        candidate = local_path if local_path.is_absolute() else manifest_dir / local_path
        source = candidate.resolve()
        try:
            source.relative_to(manifest_dir_resolved)
        except ValueError as exc:
            raise ReconcilerWrapError(
                f"local_path {item.local_path!r} resolves to {source} which "
                f"escapes the manifest directory {manifest_dir_resolved} "
                f"(BL-03 containment check)"
            ) from exc
        packager.pack(
            source=source,
            display_name=item.display_name,
            target_folder=item.target_folder,
            logical_id=item.logical_id,
            staging_dir=staging_dir,
        )


def _snapshot_for_publish_phase(
    workspace_id: str,
    client_obj: FabricRestClient,
    params_path: str | Path,
    *,
    snapshot_workspace_fn,
) -> tuple[set[tuple[str, str]], Path, tempfile.TemporaryDirectory]:
    """W4.2 phase: pre-reconcile snapshot + parameters substitution for
    ``with_publish=True``.

    Returns the ``existing_set`` (display_name + type tuples already in
    the workspace), the substituted-parameters tempfile path, and the
    ``TemporaryDirectory`` handle the caller MUST keep alive until
    after the publish call (D-17-07).

    Audit-2026-05-07 W4.2 extraction. Pre-W4.2 these three returns were
    bound inline in ``apply_sync`` Step 3.5; extracting them keeps the
    outer orchestrator's compose-mode branch a single line.
    """
    snapshot = snapshot_workspace_fn(workspace_id, client=client_obj)
    existing_set = {(it.display_name, it.type) for it in snapshot.items_by_id.values()}
    params_config = load_and_validate(Path(params_path))
    publish_params_tmpdir = tempfile.TemporaryDirectory(prefix="sigantry-sync-publish-params-")
    substituted_params_path = write_substituted_parameters(
        params_config,
        Path(publish_params_tmpdir.name) / "parameters.yml",
    )
    return existing_set, substituted_params_path, publish_params_tmpdir


def _reconcile_phase(
    client_obj: FabricRestClient,
    workspace_id: str,
    *,
    staging_dir: Path,
    apply: bool,
    unpublish_orphans: bool,
    token_provider: TokenProvider | None,
    manifest,
    reconcile_fn,
) -> ReconcileReport:
    """W4.2 phase: drive the reconciler, wrap its raw exception type.

    Pre-W4.2 the call + try/except lived inline in ``apply_sync``
    Step 6. Extracted to keep the outer orchestrator on one
    abstraction level.
    """
    try:
        return reconcile_fn(
            client_obj,
            workspace_id,
            repository_directory=staging_dir,
            apply=apply,
            unpublish_orphans=unpublish_orphans,
            force=unpublish_orphans,
            token_provider=token_provider,
            preserve_paths=manifest.folders if manifest else None,
        )
    except Exception as exc:
        raise ReconcilerWrapError(f"Reconciler failed for workspace {workspace_id}: {exc}") from exc


def _emit_combined_publish_record_phase(
    *,
    workspace_id: str,
    manifest_path_p: Path,
    manifest,
    staging_dir: Path,
    items_packaged: int,
    report: ReconcileReport,
    existing_set: set[tuple[str, str]],
    substituted_params_path: Path,
    environment: str | None,
    audit_dir: Path | None,
    token_provider: TokenProvider | None,
    publish_fn,
    republish_existing: bool = False,
    bulk: bool = False,
) -> SyncApplyReport:
    """W4.2 phase: drive ``publish_fn`` + emit the ONE combined DeployRecord.

    Phase 17 / SYNC-PUBLISH success branch -- runs only when
    ``with_publish=True`` AND ``not dry_run``. Computes the absent-
    items set against the pre-reconcile snapshot, the moved-items
    set against the reconciler's actual move plan (D-17-05 audit-
    record correctness fix from 2026-05-06), drives publish, and
    builds the combined record per PUBLISH-02 / PUBLISH-03's "1
    record" requirement.

    ``republish_existing`` (D-17-10): when ``True`` the publish set is
    the FULL manifest, not just the absent subset. fabric-cicd matches a
    repo item to a workspace item by ``(display_name, type)`` and issues
    ``updateDefinition`` for the ones already present (and ``create`` for
    the absent ones), so existing items get their CONTENT refreshed.
    Default ``False`` keeps the SemVer-safe first-time-publish-only
    semantics (existing items are reparented by the folder reconciler but
    their bodies are untouched).
    """
    absent_items = [it for it in manifest.items if (it.display_name, it.type) not in existing_set]
    # D-17-10: choose the publish set. Default = absent-only (first-time
    # publish). republish_existing = full manifest (refresh present items'
    # content via fabric-cicd updateDefinition; see helper docstring).
    items_to_publish = list(manifest.items) if republish_existing else absent_items
    # Audit-record correctness: moved_items must reflect ACTUAL
    # reconciler move operations, not "manifest items already in the
    # workspace" (2026-05-06 fix). The truth source is
    # ``report.plan.move_items``.
    actually_moved_keys = {(m.display_name, m.item_type) for m in report.plan.move_items}
    moved_items = [it for it in manifest.items if (it.display_name, it.type) in actually_moved_keys]
    item_type_in_scope = sorted({it.type for it in items_to_publish}) or sorted(
        {it.type for it in manifest.items}
    )
    publish_kwargs = {
        "workspace_id": workspace_id,
        "environment": environment,
        "staging_dir": staging_dir,
        "absent_items": items_to_publish,
        "item_type_in_scope": item_type_in_scope,
        "parameters_path": substituted_params_path,
        "token_provider": token_provider or TokenProvider.from_defaults(),
    }
    if bulk:
        try:
            publish_result = publish_fn(**publish_kwargs, bulk=True)
        except TypeError:
            publish_result = publish_fn(**publish_kwargs)
    else:
        publish_result = publish_fn(**publish_kwargs)
    record = _build_combined_record(
        workspace_id=workspace_id,
        manifest=manifest,
        moved_items=moved_items,
        published_items=publish_result.published_items,
        outcome=publish_result.outcome,
        failed_item=publish_result.failed_item,
    )
    emit_deploy_record(record, audit_dir=audit_dir)
    return SyncApplyReport(
        workspace_id=workspace_id,
        manifest_path=str(manifest_path_p),
        items_packaged=items_packaged,
        folders_created=len(report.plan.create_folders),
        items_moved=len(report.plan.move_items),
        deploy_record_release_id=record.release_id,
        outcome="succeeded",  # PUBLISH-05: reconcile committed
        failure_reason=None,
        staging_dir=None,
        # S-T1a: carry the publish-side outcome so the CLI can exit
        # non-zero on a failed/partial publish. PUBLISH-05 keeps the
        # reconcile committed (``outcome="succeeded"``) but the publish
        # result -- which ``publish_absent_items`` RETURNS rather than
        # raises on failure -- must not be swallowed into a green exit.
        publish_outcome=publish_result.outcome,
        failed_item=publish_result.failed_item,
    )


def _emit_default_record_phase(
    *,
    workspace_id: str,
    manifest_path_p: Path,
    manifest,
    items_packaged: int,
    report: ReconcileReport,
    audit_dir: Path | None,
) -> SyncApplyReport:
    """W4.2 phase: emit the v3.0 default-path DeployRecord (with_publish=False).

    Byte-identical to the pre-Phase-17 behaviour (PUBLISH-01 SemVer
    invariant). Builds a ``release_id = "sync-<TS>"`` record and
    returns the success ``SyncApplyReport``.
    """
    record = _build_record(
        workspace_id=workspace_id,
        manifest=manifest,
        outcome="succeeded",
        items_packaged=items_packaged,
        failure_reason=None,
    )
    emit_deploy_record(record, audit_dir=audit_dir)
    return SyncApplyReport(
        workspace_id=workspace_id,
        manifest_path=str(manifest_path_p),
        items_packaged=items_packaged,
        folders_created=len(report.plan.create_folders),
        items_moved=len(report.plan.move_items),
        deploy_record_release_id=record.release_id,
        outcome="succeeded",
        failure_reason=None,
        staging_dir=None,
    )


def _emit_failure_record_phase(
    *,
    workspace_id: str,
    manifest,
    items_packaged: int,
    failure_reason: str,
    audit_dir: Path | None,
    exc: BaseException,
) -> None:
    """W4.2 phase: emit a failure DeployRecord (W6 guard).

    If ``manifest`` is ``None`` (``load_manifest`` raised before the
    binding completed), the function logs and skips the emission --
    no manifest items list means no meaningful audit record. Inner
    emission errors are caught and warning-logged so the original
    exception remains the propagated one.
    """
    if manifest is None:
        logger.warning(
            "sync_apply_failure_pre_manifest err=%s; skipping DeployRecord emission",
            exc,
        )
        return
    try:
        record = _build_record(
            workspace_id=workspace_id,
            manifest=manifest,
            outcome="failed",
            items_packaged=items_packaged,
            failure_reason=failure_reason,
        )
        emit_deploy_record(record, audit_dir=audit_dir)
    except Exception as inner:
        logger.warning("sync_apply_failure_record_emit_failed err=%s", inner)


def _check_git_sync_state(client: FabricRestClient, workspace_id: str) -> None:
    """Refuse to apply if the workspace has pending Git Sync updates (D-18).

    The check fires AFTER manifest load (so ``manifest`` is bound and
    the failure DeployRecord can carry the manifest items list per
    D-16) and BEFORE the packager loop (so packaging never runs against
    a workspace whose state is mid-sync).

    A workspace with no ``gitConnection`` (i.e. not Git-bound) is safe
    to apply -- the function returns silently. A ``sync_state`` of
    ``"Synced"`` is also the green-light path. Anything else raises
    :class:`WorkspacePendingGitUpdateError` with the locked error
    message; the caller (typically the CLI) maps that to exit code 2.

    WR-03 (REVIEW.md): the Microsoft Fabric REST surface returns
    ``syncState`` (camelCase) per Microsoft Learn; the snake-case
    ``sync_state`` is kept as a defensive fallback because some
    upstream wrappers / fabric-cicd builds historically transformed
    field names. We read camelCase first so production Fabric
    responses hit the canonical branch; the snake_case fallback is a
    forward-compat safety net.

    WR-06 (REVIEW.md): when a workspace has a ``gitConnection`` block
    but neither ``syncState`` nor ``sync_state`` is present, we
    "fail closed" -- the absence of the field is a malformed response
    or mid-flight transition, not a green light. We raise
    :class:`WorkspacePendingGitUpdateError` with ``sync_state=None`` so
    the operator must commit / discard via the Fabric UI (or
    investigate the workspace) before retrying.
    """
    resp = client.send("GET", f"/v1/workspaces/{workspace_id}")
    body = resp.json_body
    git_conn = body.get("gitConnection") if isinstance(body, dict) else None
    if not git_conn:
        # No Git binding -> apply is safe.
        return
    # WR-03: read canonical Fabric REST shape (camelCase) first; fall
    # back to snake_case for forward-compat with upstream wrappers.
    sync_state = git_conn.get("syncState")
    if sync_state is None:
        sync_state = git_conn.get("sync_state")
    if sync_state == "Synced":
        return
    if sync_state is None:
        # WR-06: fail closed. A Git-bound workspace MUST report its
        # sync state per the Fabric API contract; absence is a
        # malformed response or mid-transition state, NOT a green
        # light. Refuse rather than apply against an indeterminate
        # state.
        logger.warning(
            "git_sync_state_missing workspace=%s gitConnection=%r",
            workspace_id,
            git_conn,
        )
        raise WorkspacePendingGitUpdateError(
            f"Workspace {workspace_id} has a gitConnection but no "
            f"syncState/sync_state field; refusing to apply against an "
            f"indeterminate Git Sync state. Commit or sync via the "
            f"Fabric UI before retrying.",
            sync_state=None,
        )
    raise WorkspacePendingGitUpdateError(
        f"Workspace has pending Git Sync updates. Commit or discard via "
        f"Fabric UI before running `sync apply`. Workspace status: "
        f"{sync_state}.",
        sync_state=str(sync_state),
    )


def _build_record(
    *,
    workspace_id: str,
    manifest: SyncManifest,
    outcome: str,
    items_packaged: int,
    failure_reason: str | None,
) -> DeployRecord:
    """Build a hashed :class:`DeployRecord` for one apply invocation.

    ``test_evidence`` carries the apply-specific outcome / failure
    information per WR-03 (DeployRecord schema is frozen with
    ``extra="forbid"``; we cannot add new top-level fields without
    bumping the audit schema). ``sync_engine_outcome`` is one of
    ``"succeeded"`` / ``"failed"`` / ``"dry_run"``;
    ``sync_engine_failure`` carries the typed exception class name when
    ``outcome == "failed"``.
    """
    utc_iso = _utc_iso_with_z()
    evidence: dict[str, str] = {
        "sync_engine_outcome": outcome,
        "items_packaged": str(items_packaged),
    }
    if failure_reason:
        evidence["sync_engine_failure"] = failure_reason
    return DeployRecord(
        workspace=workspace_id,
        release_id=f"sync-{utc_iso}",
        work_items=[],
        fabric_items_changed=[f"{item.display_name}.{item.type}" for item in manifest.items],
        test_evidence=evidence,
        approver="sync-engine",
        audit_hash="",
        created_at=datetime.now(UTC).replace(microsecond=0),
    ).with_hash()


def _build_combined_record(
    *,
    workspace_id: str,
    manifest: SyncManifest,
    moved_items: list[SyncItem],
    published_items: list[str],
    outcome: str,
    failed_item: str | None,
) -> DeployRecord:
    """Build a hashed combined :class:`DeployRecord` for ``apply_sync(with_publish=True)``.

    Phase 17 / SYNC-PUBLISH carries BOTH the folder-reconcile-side
    ``moved_items`` AND the publish-side ``published_items`` inside ONE
    record (per PUBLISH-02 / PUBLISH-03's "1 record" requirement).

    The encoding is constrained by D-17-05 -- :class:`DeployRecord` is
    ``extra="forbid"`` + ``frozen=True``, so we cannot add new top-level
    fields without breaking verify-without-trust on every existing
    record. Both lists go inside ``test_evidence: dict[str, str]`` as
    JSON-stringified arrays. Consumers ``json.loads()`` the field --
    runbook §1.3 documents the encoding contract.

    The ``release_id`` prefix is ``"sync-publish-<TS>"`` (D-17-04) so
    audit readers can bucket compose records distinctly from v3.0
    ``"sync-<TS>"`` records and ``R-...`` deploy records.

    Parameters
    ----------
    workspace_id:
        Target Fabric workspace GUID (matches the apply target).
    manifest:
        Validated :class:`SyncManifest` -- used only for the lifecycle
        information (caller has already split into moved + published).
    moved_items:
        :class:`SyncItem` instances that were already present in the
        workspace (folder reconcile reparented them).
    published_items:
        ``"<display_name>.<type>"`` strings of items published by
        :func:`publish_absent_items` -- length may be less than the
        absent-items count on partial-failure outcomes.
    outcome:
        One of ``"succeeded"`` / ``"partial-failure"`` / ``"failed"``.
        Mirrors the publish helper's :class:`PublishResult.outcome`.
    failed_item:
        Best-effort ``"<display_name>.<type>"`` of the failing item on
        partial / full failure, or ``None``. Encoded into evidence
        ONLY when non-None (PUBLISH-05).
    """
    del manifest  # currently unused -- reserved for forward compat
    utc_iso = _utc_iso_with_z()
    evidence: dict[str, str] = {
        "provider": "sync-engine-publish",
        "outcome": outcome,
        "moved_items": json.dumps([f"{it.display_name}.{it.type}" for it in moved_items]),
        "published_items": json.dumps(published_items),
    }
    if failed_item:
        evidence["failed_item"] = failed_item
    fabric_items_changed = sorted(
        {f"{it.display_name}.{it.type}" for it in moved_items} | set(published_items)
    )
    return DeployRecord(
        workspace=workspace_id,
        release_id=f"sync-publish-{utc_iso}",  # D-17-04
        work_items=[],
        fabric_items_changed=fabric_items_changed,
        test_evidence=evidence,
        approver="sync-engine",
        audit_hash="",
        created_at=datetime.now(UTC).replace(microsecond=0),
    ).with_hash()


def apply_sync(
    manifest_path: str | Path,
    workspace_id: str,
    *,
    environment: str | None = None,
    audit_dir: Path | None = None,
    dry_run: bool = False,
    with_publish: bool = False,
    republish_existing: bool = False,
    bulk: bool = False,
    params_path: str | Path | None = None,
    unpublish_orphans: bool = False,
    client: FabricRestClient | None = None,
    token_provider: TokenProvider | None = None,
    # Audit-2026-05-07 W4.4: constructor-injectable seams for the
    # three internal collaborators (snapshot, reconcile, publish).
    # Defaults are the real implementations; tests pass fakes here
    # instead of monkey-patching the module-level names. Underscore-
    # prefixed because they are a test seam, not a public knob.
    _snapshot_fn=None,
    _reconcile_fn=None,
    _publish_fn=None,
) -> SyncApplyReport:
    """Push manifest items into ``workspace_id``.

    See module docstring for the execution flow (D-17, W9-resolved). On
    success the staging tempdir is cleaned up and the returned
    :class:`SyncApplyReport` carries ``staging_dir = None``. On failure
    the staging tempdir is preserved at ``/tmp/sigantry-sync-*`` and the
    path is printed to stderr; the failure DeployRecord (per D-16) is
    emitted unless ``load_manifest`` itself raised before ``manifest``
    was bound (W6 guard).

    Parameters
    ----------
    manifest_path:
        Path to ``sync.yml``; passed through :func:`load_manifest` and
        normalised via :meth:`SyncManifest.normalised`.
    workspace_id:
        Target Fabric workspace GUID.
    environment:
        ``parameters.yml`` environment label. Required when
        ``with_publish=True`` AND ``parameters.yml`` references any
        environment beyond the ``_ALL_`` wildcard (D-17-02 -- enforced
        at the CLI layer in Plan 17-02).
    audit_dir:
        Override ``~/.sigantry/audit/`` for hermetic CI runs. Threaded
        into :func:`emit_deploy_record`.
    dry_run:
        If ``True``, packagers run (so logical_ids persist to the
        sidecar -- D-11 invariant) but the reconciler is invoked with
        ``apply=False`` and no DeployRecord is emitted (D-15 / D-19
        symmetry). When ``dry_run=True`` AND ``with_publish=True`` the
        publish step is ALSO skipped and no combined record is emitted.
    with_publish:
        Phase 17 / SYNC-PUBLISH opt-in flag. When ``True`` AND
        ``not dry_run``, the function performs the v3.0 folder
        reconcile AND drives :func:`fabric_cicd.publish_all_items`
        for any manifest item NOT already present in the workspace.
        ONE combined :class:`DeployRecord` is emitted carrying both
        ``moved_items`` and ``published_items`` inside
        ``test_evidence`` (per D-17-05 encoding rule).

        Default ``False`` preserves SemVer -- the v3.0 reconcile-only
        path is byte-identical to the legacy behaviour.
    republish_existing:
        D-17-10 modifier on ``with_publish``. When ``True`` the publish
        set is the FULL manifest rather than only the absent subset, so
        manifest items ALREADY present in the workspace get their content
        refreshed via fabric-cicd ``updateDefinition`` (matched by
        ``(display_name, type)``). Requires ``with_publish=True`` --
        passing it alone raises :class:`SyncPublishError`. Default
        ``False`` keeps the first-time-publish-only semantics (existing
        items are reparented but their bodies are untouched). Use this to
        push code changes to notebooks that already exist in the target
        workspace -- the v3.x folder-reconcile + first-time-publish paths
        never update an existing item's body.
    params_path:
        Path to ``parameters.yml`` -- REQUIRED when ``with_publish=True``
        (D-17-01). The YAML is loaded + validated via
        :func:`sigantry_core.deploy.parameters.load_and_validate`
        (catches ``HardcodedGuidError`` + unset ``$ENV:`` early), then
        ``$ENV:`` references are substituted into a tempfile copy that
        lives for the lifetime of the publish call (PR #54).
    client:
        Optional :class:`FabricRestClient`; when ``None`` the function
        constructs one via :meth:`FabricRestClient.from_defaults` and
        closes it on exit.
    token_provider:
        Optional :class:`TokenProvider` forwarded to the reconciler's
        destructive-op gate (used only when orphan cleanup fires --
        ``apply_sync`` does NOT enable orphan cleanup).

    Compose-mode notes (Phase 17 / SYNC-PUBLISH)
    --------------------------------------------
    When ``with_publish=True``:

    1. The function captures a snapshot of the workspace BEFORE the
       reconcile so it can compute the "absent items" set
       (``manifest_items \\ existing_items`` joined on
       ``(display_name, type)``).
    2. ``parameters.yml`` is validated + ``$ENV:``-substituted into a
       nested tempfile. Both the staging tempdir AND the substituted-
       parameters tempdir live until after the publish call returns
       or raises (Decision D-17-07).
    3. After the reconcile, the absent items are passed to
       :func:`sigantry_core.deploy.sync_publish.publish_absent_items`
       which appends the two required fabric-cicd feature flags and
       drives ``publish_all_items`` with an ``items_to_include``
       filter.
    4. ONE combined :class:`DeployRecord` is emitted with
       ``release_id = "sync-publish-<TS>"`` (D-17-04). The default
       v3.0 ``_build_record`` path is bypassed.
    5. ``partial-failure`` / ``failed`` outcomes from the publish
       helper do NOT raise out of ``apply_sync`` -- the folder
       reconcile remains committed (PUBLISH-05 invariant) and the
       :class:`SyncApplyReport.outcome` field stays ``"succeeded"``
       because the reconcile-side outcome IS succeeded; the
       publish-side outcome is captured in the audit ledger.

    See ``docs/decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md``
    + ``docs/decisions/ADR-0013-sync-publish-parameters-resolution.md``
    + ``docs/runbooks/sync/apply.md`` §1.3.
    """
    manifest_path_p = Path(manifest_path)

    # W6: pre-bind ``manifest`` and ``items_packaged`` so the outer
    # ``except`` block can reference them safely even if
    # ``load_manifest()`` itself raises (in which case the failure
    # record emission is skipped via the ``if manifest is not None``
    # guard and the typed exception propagates to the CLI layer).
    manifest: SyncManifest | None = None
    items_packaged: int = 0

    # Build (or borrow) a client. When we own it, we must close it in
    # the ``finally`` block; when the caller passed one in, we leave it
    # alone.
    owned_client_cm: FabricRestClient | None = (
        FabricRestClient.from_defaults() if client is None else None
    )
    client_obj = owned_client_cm if owned_client_cm is not None else client
    assert client_obj is not None  # narrows for mypy; one of the two must exist

    # W4.4 test-seam resolution: fall back to the real implementations
    # when the caller did not pass injection points. Production calls
    # never set these kwargs; tests do.
    snapshot_workspace_fn = _snapshot_fn or snapshot_workspace
    reconcile_fn = _reconcile_fn or reconcile_folders_from_repo
    publish_fn = _publish_fn or publish_absent_items

    # Tempdir is created up-front so ``staging_dir`` survives both the
    # success-cleanup and failure-preserve branches (D-17). On failure
    # we set ``cleanup_on_success = False`` and skip the rmtree below.
    staging_dir = Path(tempfile.mkdtemp(prefix="sigantry-sync-"))
    cleanup_on_success = True

    # Phase 17 / SYNC-PUBLISH: pre-bind compose-mode bookkeeping. These
    # remain harmlessly empty / None on the v3.0 default path
    # (with_publish=False), so the SemVer-safe byte-identical contract
    # is preserved (PUBLISH-01). The substituted-parameters tempdir is
    # cleaned in the outer ``finally`` regardless of outcome (D-17-07).
    existing_set: set[tuple[str, str]] = set()
    publish_params_tmpdir: tempfile.TemporaryDirectory | None = None
    substituted_params_path: Path | None = None

    try:
        # Step 1: load + normalise manifest (SYNC-06 folder-less override).
        manifest = load_manifest(manifest_path_p).normalised()
        items_packaged = len(manifest.items)

        # Step 3: pre-flight Git-Sync sanity (D-18).
        _check_git_sync_state(client_obj, workspace_id)

        # Step 3.5 (Phase 17 / SYNC-PUBLISH): when --with-publish is set
        # AND we are NOT in dry-run, snapshot the workspace BEFORE the
        # reconcile so we can compute the absent-items set; load +
        # validate + $ENV-substitute parameters.yml into a nested
        # tempfile whose lifetime extends through the publish call
        # (D-17-07). Skipped on the v3.0 default path.
        # D-17-10 defence in depth: ``--republish-existing`` is a modifier
        # on ``--with-publish`` (the CLI layer is the primary gate). A
        # programmatic caller passing it alone gets a typed error rather
        # than a silent no-op (the flag only takes effect on the publish
        # path).
        if republish_existing and not with_publish:
            raise SyncPublishError(
                "republish_existing=True requires with_publish=True. "
                "See docs/runbooks/sync/apply.md §1.3."
            )

        if with_publish and not dry_run:
            if params_path is None:
                # Defence in depth -- the CLI layer (Plan 17-02) is the
                # primary gate but a programmatic caller could still
                # forget the kwarg. Fail with a typed error rather than
                # cryptic AttributeError downstream.
                raise SyncPublishError(
                    "with_publish=True requires params_path=<parameters.yml>. "
                    "See docs/runbooks/sync/apply.md §1.3."
                )
            existing_set, substituted_params_path, publish_params_tmpdir = (
                _snapshot_for_publish_phase(
                    workspace_id,
                    client_obj,
                    params_path,
                    snapshot_workspace_fn=snapshot_workspace_fn,
                )
            )

        # Step 4: REMOVED redundant snapshot fetch (W9). The reconciler
        # does its own ``list_folders`` / ``list_items`` internally; an
        # extra snapshot fetch here would issue redundant REST calls
        # without consuming the result. ``diff`` (Plan 13-06) and
        # ``pull`` (Plan 13-05) DO genuinely need a snapshot and
        # import it explicitly.

        # Step 5 (W4.2 _pack_phase): pack manifest items into staging tree.
        # Packagers run BEFORE the dry-run branch (W5 invariant) so
        # logical_ids ARE persisted to the sidecar even on ``--dry-run``,
        # preserving the D-11 sidecar invariant. ``local_path`` resolves
        # relative to the manifest file (D-22 round-trip).
        manifest_dir = manifest_path_p.resolve().parent
        _pack_phase(manifest, manifest_dir, staging_dir)

        # Step 6 (W4.2 _reconcile_phase): drive the existing reconciler
        # against the staging tree. ``manifest.folders`` (Council D #5
        # preservation set) is threaded; only takes effect when
        # ``unpublish_orphans`` is set. The phase wrapper converts the
        # raw exception type to the typed ``ReconcilerWrapError``.
        report: ReconcileReport = _reconcile_phase(
            client_obj,
            workspace_id,
            staging_dir=staging_dir,
            apply=not dry_run,
            unpublish_orphans=unpublish_orphans,
            token_provider=token_provider,
            manifest=manifest,
            reconcile_fn=reconcile_fn,
        )

        # W1: Use ONE consistent ReconcileReport access pattern across
        # both the dry-run and success branches. Per the canonical
        # ``sigantry_core.workspace.reconciler.ReconcileReport``
        # dataclass, the ``plan`` attribute (``ReconcilePlan``) carries
        # the PLANNED operations regardless of whether ``apply=True``
        # ran; ``report.folders_created`` / ``report.items_moved`` are
        # populated only when ``apply=True``. Reading both metrics off
        # ``report.plan.*`` keeps the access pattern consistent across
        # the branches AND makes the load-bearing idempotency test
        # directly assert against the planned operations regardless of
        # dry-run mode.

        # Step 7: dry-run path -- no audit write, return early (D-15
        # symmetry: ``pull`` does not emit; ``--dry-run`` does not
        # either).
        if dry_run:
            cleanup_on_success = True  # dry-run cleans up too -- no leak
            return SyncApplyReport(
                workspace_id=workspace_id,
                manifest_path=str(manifest_path_p),
                items_packaged=items_packaged,
                folders_created=len(report.plan.create_folders),
                items_moved=len(report.plan.move_items),
                deploy_record_release_id="",
                outcome="dry_run",
                failure_reason=None,
                staging_dir=None,
            )

        # Step 8 (W4.2 _emit_*_phase): success -- emit DeployRecord + return.
        # Phase 17 / SYNC-PUBLISH branch when --with-publish is set:
        # drive fabric-cicd publish + ONE combined DeployRecord. Default
        # path (with_publish=False) emits the v3.0 byte-identical record.
        # Both branches return the SyncApplyReport with outcome="succeeded";
        # the folder-reconcile side is decoupled from the publish-side
        # outcome (PUBLISH-05 invariant).
        if with_publish and not dry_run:
            assert substituted_params_path is not None  # bound above
            return _emit_combined_publish_record_phase(
                workspace_id=workspace_id,
                manifest_path_p=manifest_path_p,
                manifest=manifest,
                staging_dir=staging_dir,
                items_packaged=items_packaged,
                report=report,
                existing_set=existing_set,
                substituted_params_path=substituted_params_path,
                environment=environment,
                audit_dir=audit_dir,
                token_provider=token_provider,
                publish_fn=publish_fn,
                republish_existing=republish_existing,
                bulk=bulk,
            )
        return _emit_default_record_phase(
            workspace_id=workspace_id,
            manifest_path_p=manifest_path_p,
            manifest=manifest,
            items_packaged=items_packaged,
            report=report,
            audit_dir=audit_dir,
        )

    except Exception as exc:
        cleanup_on_success = False
        print(
            f"sync apply failed; staging dir preserved at {staging_dir}",
            file=sys.stderr,
        )
        _emit_failure_record_phase(
            workspace_id=workspace_id,
            manifest=manifest,
            items_packaged=items_packaged,
            failure_reason=type(exc).__name__,
            audit_dir=audit_dir,
            exc=exc,
        )
        raise

    finally:
        if cleanup_on_success and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        # Phase 17 / SYNC-PUBLISH: clean the substituted-parameters
        # tempdir LAST -- after staging cleanup, after publish is done.
        # D-17-07 lifecycle: the tempdir survived the publish call;
        # now it can go.
        if publish_params_tmpdir is not None:
            try:
                publish_params_tmpdir.cleanup()
            except OSError as cleanup_exc:
                logger.warning(
                    "sync_publish_params_tmpdir_cleanup_failed err=%s",
                    cleanup_exc,
                )
        if owned_client_cm is not None:
            owned_client_cm.close()


__all__ = ("SyncApplyReport", "apply_sync")
