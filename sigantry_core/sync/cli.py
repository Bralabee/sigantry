"""Typer subapp ``sigantry sync`` (Phase 13 / D-04).

Three commands:

* ``apply`` (SYNC-04) -- push manifest items into a workspace via
  :func:`sigantry_core.sync.apply.apply_sync`.
* ``snapshot`` (INTROSPECT-01) -- emit a JSON workspace topology
  snapshot via :func:`sigantry_core.sync.snapshot.snapshot_workspace`.
* ``pull`` (SYNC-05) -- workspace -> local IaC-fication via
  :func:`sigantry_core.sync.pull.pull_workspace`.

Exit-code conventions:

* ``apply``:
    - ``0`` on success or dry-run.
    - ``1`` on :class:`ManifestValidationError` (manifest bad shape) and
      on any other :class:`SyncEngineError` subclass.
    - ``2`` on :class:`WorkspacePendingGitUpdateError` (Git Sync state
      not yet ``Synced`` -- D-18). Distinguishable from validation
      errors so CI runners can branch on the exit code.
* ``snapshot``:
    - ``0`` on success.
    - Bubbles SyncEngineError as exit ``1`` via Typer.
* ``pull``:
    - ``0`` on success.
    - ``1`` on :class:`PullTargetNotEmptyError` (non-empty ``--into``
      without ``--force`` -- D-21) and on any other
      :class:`SyncEngineError` subclass (e.g.
      :class:`PullDefinitionFetchError`).

The subapp registers as the 14th top-level subapp on the root
``sigantry`` Typer app via :data:`sigantry_core.cli.app.add_typer`.
"""

from __future__ import annotations

import json as _json
import logging
import sys
import tomllib
from pathlib import Path

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError
from rich.console import Console

from sigantry_core.config import ToolkitSettings, load_settings
from sigantry_core.deploy.parameters import (
    HardcodedGuidError,
    load_and_validate,
)
from sigantry_core.sync.apply import apply_sync
from sigantry_core.sync.errors import (
    ManifestValidationError,
    PullTargetNotEmptyError,
    SyncEngineError,
    WorkspacePendingGitUpdateError,
)
from sigantry_core.sync.pull import pull_workspace
from sigantry_core.sync.snapshot import snapshot_workspace

logger = logging.getLogger("sigantry_core.sync.cli")

# Process-scoped sentinel for the one-time Preview-API warning
# (Plan 13-07 / W2 plan-checker resolution / SPEC §Constraints #1).
# A set is the right shape: a future plan that adds a second one-time
# warning (e.g. for a different Preview-API surface) reuses the same
# guard with its own sentinel string.
_PREVIEW_WARNING_EMITTED: set[str] = set()
_PREVIEW_WARNING_SENTINEL = "preview-folders-rest-endpoint"


def _emit_preview_warning_once(settings: ToolkitSettings | None = None) -> None:
    """One-time WARNING emission for the Preview Folders REST dependency.

    Plan 13-07 / SPEC §Constraints #1 / Council D #1 -- Sigantry depends
    on the Preview Microsoft Fabric Folders REST endpoint family in v3.0.
    The runtime gate ``WorkflowSettings.preview_apis_acknowledged`` is the
    operator's explicit acknowledgement; until it is set, this helper
    emits one WARNING per process on the first ``sigantry sync apply``
    or ``sigantry sync pull`` invocation.

    The function is intentionally tolerant of a missing or malformed
    config file -- a config-load failure must NOT break the sync command.
    It falls back to a fresh ``ToolkitSettings()`` (which carries the False
    default), but it *logs* that it did so: a governance tool that silently
    swallows an unreadable config runs the operator's whole session on
    defaults with no signal that their settings were never applied. The
    handler names the ways loading can legitimately fail rather than catching
    ``Exception``, so a genuine defect inside the loader still surfaces instead
    of being absorbed as "bad config". ``UnicodeDecodeError`` is one of them:
    ``tomllib.load`` decodes the file itself, so a config with a non-UTF-8 byte
    raises it rather than ``TOMLDecodeError``, and it is a ``ValueError``
    sibling that neither of the other two covers.

    ``snapshot_cmd`` does NOT call this helper: snapshot is read-only and
    operator-explicit; we do not want to interrupt the operator's
    grep-and-jq diagnostic loop with a Preview-API warning.
    """
    if settings is None:
        try:
            settings = load_settings()
        except (
            OSError,
            UnicodeDecodeError,
            tomllib.TOMLDecodeError,
            ValidationError,
            SettingsError,
        ) as exc:
            logger.warning(
                "Could not load Sigantry settings (%s: %s); continuing with "
                "defaults, so no operator configuration is in effect for this "
                "command.",
                type(exc).__name__,
                exc,
            )
            settings = ToolkitSettings()
    if settings.workflow.preview_apis_acknowledged:
        return
    if _PREVIEW_WARNING_SENTINEL in _PREVIEW_WARNING_EMITTED:
        return
    _PREVIEW_WARNING_EMITTED.add(_PREVIEW_WARNING_SENTINEL)
    msg = (
        "Sigantry depends on the Preview Microsoft Fabric Folders REST "
        "endpoint (Council D #1). Set `workflow.preview_apis_acknowledged "
        "= true` in .sigantry.toml (or "
        "SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED=true) to acknowledge "
        "and suppress this warning."
    )
    logger.warning(msg)
    _console.print(f"[yellow]preview-API warning:[/yellow] {msg}")


def _split_csv(value: str | None) -> list[str]:
    """Split a comma-separated string, dropping empty fragments + trimming whitespace.

    Mirrors :func:`sigantry_core.release.cli._split_csv` (Phase 11) so
    operators get the same parsing behaviour across ``sigantry release``
    and ``sigantry sync`` flag surfaces.
    """
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


sync_app = typer.Typer(
    help="Local <-> Fabric folder-aware sync engine (Phase 13).",
    no_args_is_help=True,
)
_console = Console()


@sync_app.command("apply")
def apply_cmd(
    manifest: str = typer.Option(
        ...,
        "--manifest",
        help="Path to sync.yml.",
    ),
    workspace_id: str = typer.Option(
        ...,
        "--workspace-id",
        help="Target Fabric workspace GUID.",
    ),
    environment: str = typer.Option(
        None,
        "--environment",
        help="Optional parameters.yml environment label (reserved).",
    ),
    audit_dir: str = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/ (used in tests + CI).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the would-be reconciler plan and exit 0 without applying.",
    ),
    with_publish: bool = typer.Option(
        False,
        "--with-publish",
        help=(
            "Compose folder reconcile with first-time fabric-cicd publish "
            "(ADR-0012 Option C; runbook section 1.3). Requires --params. "
            "Default off preserves SemVer-safe v3.0 behaviour."
        ),
    ),
    republish_existing: bool = typer.Option(
        False,
        "--republish-existing",
        help=(
            "Modifier on --with-publish: also refresh the CONTENT of "
            "manifest items that already exist in the workspace "
            "(fabric-cicd updateDefinition, matched by display name + "
            "type), not just first-time-publish the absent ones. Use this "
            "to push notebook code changes to a workspace that already "
            "holds those notebooks. Requires --with-publish. Default off."
        ),
    ),
    params: str = typer.Option(
        None,
        "--params",
        help=(
            "Path to parameters.yml; required when --with-publish is set. "
            "See ADR-0013 for the resolution rule."
        ),
    ),
    bulk: bool = typer.Option(
        False,
        "--bulk",
        help="Enable concurrent bulk publish acceleration for multi-item publish.",
    ),
    unpublish_orphans: bool = typer.Option(
        False,
        "--unpublish-orphans",
        help=(
            "Delete workspace folders + items absent from the manifest. "
            "Folders listed in the manifest's `folders[]` preservation set "
            "(plus their ancestors) are excluded from deletion. Default "
            "off preserves SemVer-safe additive behaviour. See "
            "docs/runbooks/sync/folder-preservation.md."
        ),
    ),
) -> None:
    """Push manifest items into ``--workspace-id``.

    Wraps :func:`sigantry_core.sync.apply.apply_sync`. Failure modes
    map to operator-friendly exit codes (see module docstring).

    Emits the one-time Preview-API warning before any other work
    (Plan 13-07 / SPEC §Constraints #1).

    Phase 17 / SYNC-PUBLISH cross-reference: when ``--with-publish`` is
    set, the engine emits a combined :class:`DeployRecord` with
    ``provider="sync-engine-publish"`` and
    ``release_id="sync-publish-<TS>"`` (D-17-04). Validation gates
    (D-17-01 / D-17-02) live in this function so the error message
    points at the flag the operator typed; the engine helper itself
    is in :func:`sigantry_core.sync.apply.apply_sync`. See
    docs/decisions/ADR-0013-sync-publish-parameters-resolution.md and
    docs/runbooks/sync/apply.md section 1.3.
    """
    # Phase 17 / SYNC-PUBLISH pre-flight (D-17-01 + D-17-02): runs BEFORE
    # _emit_preview_warning_once() so a bad invocation fails fast without
    # cluttering the operator's error output with the Preview-API
    # warning. Mirrors the rollback-gate pattern from
    # sigantry_core/deploy/cli.py:152-158.
    if republish_existing and not with_publish:
        # D-17-10 gate: --republish-existing is a modifier on the publish
        # path; alone it would silently no-op. Attribute the error to the
        # flag the operator typed.
        raise typer.BadParameter(
            "--republish-existing requires --with-publish. It refreshes "
            "the content of items already in the workspace as part of the "
            "publish; on its own there is no publish to modify. "
            "See docs/runbooks/sync/apply.md section 1.3."
        )
    if with_publish and params is None:
        # D-17-01 hard-fail. The error must NAME --params and point at
        # the runbook + ADR so the operator can self-serve.
        raise typer.BadParameter(
            "--with-publish requires --params <parameters.yml>. "
            "See docs/runbooks/sync/apply.md section 1.3 and "
            "docs/decisions/ADR-0013-sync-publish-parameters-resolution.md."
        )
    if with_publish and params is not None:
        # D-17-01 secondary validation -- catch bad parameters.yml shapes
        # at the CLI layer so the error attributes to --params (the flag
        # the operator typed) rather than bubbling up an obscure engine
        # exception. The bounded f-string (str(exc)) avoids leaking raw
        # tracebacks per T-17-cli-leak.
        try:
            params_config = load_and_validate(Path(params))
        except (HardcodedGuidError, FileNotFoundError, ValueError) as exc:
            raise typer.BadParameter(f"--params validation failed: {exc}") from exc
        # D-17-02: when parameters.yml references any environment beyond
        # the _ALL_ wildcard, --environment becomes REQUIRED. Use getattr
        # for forward-compat in case the ParametersConfig dataclass shape
        # evolves in a future deploy-module refactor.
        envs_seen: set[str] | frozenset[str] = getattr(
            params_config, "environments_seen", frozenset()
        )
        if envs_seen and environment is None:
            available = ", ".join(sorted(envs_seen))
            raise typer.BadParameter(
                "--with-publish against a multi-env parameters.yml requires "
                f"--environment <name>. Available: {available}."
            )

    _emit_preview_warning_once()
    try:
        report = apply_sync(
            manifest_path=manifest,
            workspace_id=workspace_id,
            environment=environment,
            audit_dir=Path(audit_dir) if audit_dir else None,
            dry_run=dry_run,
            with_publish=with_publish,
            republish_existing=republish_existing,
            bulk=bulk,
            params_path=params,
            unpublish_orphans=unpublish_orphans,
        )
    except ManifestValidationError as exc:
        _console.print(f"[red]Manifest validation failed:[/red] {exc}")
        for v in exc.violations:
            _console.print(f"  [yellow]-[/yellow] {v}")
        raise typer.Exit(code=1) from exc
    except WorkspacePendingGitUpdateError as exc:
        _console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except SyncEngineError as exc:
        _console.print(f"[red]sync apply failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if report.outcome == "dry_run":
        _console.print(
            f"[cyan]dry-run[/cyan] would create "
            f"{report.folders_created} folder(s) and move "
            f"{report.items_moved} item(s) for {report.items_packaged} "
            f"manifest item(s)."
        )
    else:
        _console.print(
            f"[green]sync apply succeeded[/green] "
            f"release_id={report.deploy_record_release_id} "
            f"items_packaged={report.items_packaged} "
            f"folders_created={report.folders_created} "
            f"items_moved={report.items_moved}"
        )
        # D-26-bis: surface the sync-apply-vs-deploy-run boundary at the
        # exact moment an operator is most likely to be confused by it
        # (first-time project setup -- new folders just created and no
        # existing items got moved). Suppressed on idempotent re-runs
        # (folders_created == 0). See ADR-0012 + apply.md section 1.1.
        #
        # D-17-08 (Phase 17 / SYNC-PUBLISH): the trailer is ALSO
        # suppressed under --with-publish because the publish DID
        # happen -- the "use sigantry deploy run" hint would mislead
        # the operator into double-publishing. See ADR-0012 amendment
        # + ADR-0013 + apply.md section 1.3.
        if (
            not with_publish
            and report.items_packaged > 0
            and report.folders_created > 0
            and report.items_moved == 0
        ):
            _console.print(
                f"[yellow]note:[/yellow] {report.items_packaged} manifest "
                f"item(s) were staged locally but `sync apply` does not "
                f"publish new items to the workspace. Run "
                f"[cyan]sigantry deploy run[/cyan] (or POST "
                f"`/v1/workspaces/{{id}}/items` directly) to create "
                f"first-time items. See docs/runbooks/sync/apply.md "
                f"section 1.1."
            )

        # S-T1a: the folder reconcile is committed (PUBLISH-05) but the
        # publish half can still fail -- ``publish_absent_items`` RETURNS a
        # "failed"/"partial-failure" PublishResult rather than raising, so
        # the committed reconcile is deliberately NOT rolled back. That
        # design must not translate into a green ``sync apply``: a failed
        # publish that exits 0 is the silent-success class this repo
        # forbids (success is derived from evidence, not "it did not
        # throw"). The reconcile summary above already printed, so the
        # operator sees exactly what DID commit; here we name the publish
        # outcome and exit non-zero so CI gates on it.
        if report.publish_outcome is not None and report.publish_outcome != "succeeded":
            failed_item_note = f" failed_item={report.failed_item}" if report.failed_item else ""
            _console.print(
                f"[red]publish {report.publish_outcome}[/red] "
                f"release_id={report.deploy_record_release_id} "
                f"(reconcile committed; publish did not fully succeed)"
                f"{failed_item_note}"
            )
            raise typer.Exit(code=1)


@sync_app.command("snapshot")
def snapshot_cmd(
    workspace_id: str = typer.Option(
        ...,
        "--workspace-id",
        help="Target Fabric workspace GUID.",
    ),
    output: str = typer.Option(
        None,
        "--output",
        help="Optional path; when omitted, prints JSON to stdout.",
    ),
) -> None:
    """Emit a workspace topology JSON snapshot (INTROSPECT-01).

    The serialised payload mirrors :class:`WorkspaceSnapshot` but
    explicitly re-renders ``Folder`` / ``Item`` (stdlib dataclasses) as
    plain dicts so the on-disk shape is stable -- pydantic's default
    representation of ``arbitrary_types_allowed=True`` types is the
    ``repr()`` string, which would leak implementation details.
    """
    snap = snapshot_workspace(workspace_id)
    payload = {
        "schema_version": snap.schema_version,
        "workspace_id": snap.workspace_id,
        "folder_path_index": dict(snap.folder_path_index),
        "item_to_folder": dict(snap.item_to_folder),
        "folders_by_id": {
            fid: {
                "id": f.id,
                "display_name": f.display_name,
                "parent_folder_id": f.parent_folder_id,
            }
            for fid, f in snap.folders_by_id.items()
        },
        "items_by_id": {
            iid: {
                "id": it.id,
                "display_name": it.display_name,
                "type": it.type,
                "folder_id": it.folder_id,
            }
            for iid, it in snap.items_by_id.items()
        },
    }
    text = _json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
        _console.print(
            f"[green]wrote snapshot[/green] {output} "
            f"folders={len(snap.folders_by_id)} "
            f"items={len(snap.items_by_id)}"
        )
    else:
        sys.stdout.write(text)


@sync_app.command("pull")
def pull_cmd(
    workspace_id: str = typer.Option(
        ...,
        "--workspace-id",
        help="Source Fabric workspace GUID.",
    ),
    into: str = typer.Option(
        ...,
        "--into",
        help="Target directory; must be empty or absent unless --force.",
    ),
    item_types: str = typer.Option(
        None,
        "--type",
        help=(
            "Comma-separated list of item types to pull "
            "(default: Notebook,DataPipeline,SemanticModel,Report,SparkJobDefinition)."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Allow --into to be a non-empty directory; existing files MAY be overwritten.",
    ),
    no_hint: bool = typer.Option(
        False,
        "--no-hint",
        help="Suppress operator hint trailer (CI-friendly).",
    ),
) -> None:
    """Pull workspace items into a local sync.yml + sources tree (SYNC-05).

    Wraps :func:`sigantry_core.sync.pull.pull_workspace`. The emitted
    ``sync.yml`` mirrors workspace topology and preserves the
    workspace's ``logical_id`` per item so a subsequent
    ``sigantry sync apply`` is a no-op round-trip (D-22).

    Failure modes:

    * :class:`PullTargetNotEmptyError` -> exit 1 with a clear message
      that ``--force`` is required to clobber.
    * Other :class:`SyncEngineError` subclasses (e.g.
      :class:`PullDefinitionFetchError`) -> exit 1.

    Pull is read-only against the source workspace -- no
    :class:`DeployRecord` is emitted (D-15).

    Emits the one-time Preview-API warning before any other work
    (Plan 13-07 / SPEC §Constraints #1).
    """
    _emit_preview_warning_once()
    types_list = _split_csv(item_types)
    try:
        report = pull_workspace(
            workspace_id=workspace_id,
            into=into,
            item_types=types_list or None,
            force=force,
        )
    except PullTargetNotEmptyError as exc:
        _console.print(f"[red]sync pull refused:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except SyncEngineError as exc:
        _console.print(f"[red]sync pull failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _console.print(
        f"[green]sync pull succeeded[/green] "
        f"items_pulled={report.items_pulled} "
        f"sync_yml={report.sync_yml_path}"
    )
    # D-19-04: surface the commit reminder when items were actually
    # pulled. On a no-op pull (items_pulled == 0) the reminder would
    # be noise. --no-hint suppresses globally (operator-explicit /
    # CI-friendly). See ADR-0012 + docs/runbooks/sync/pull.md
    # section 1.2.
    if not no_hint and report.items_pulled > 0:
        _console.print(
            f"[yellow]note:[/yellow] {report.items_pulled} item(s) pulled. "
            f"Remember to commit `sync.yml` + sources before next session "
            f"to preserve `logical_id` idempotency. See "
            f"docs/runbooks/sync/pull.md section 1.2."
        )


__all__ = ("sync_app",)
