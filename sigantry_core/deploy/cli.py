"""``fabric-dataops deploy`` + ``fabric-item`` + ``git`` + ``variable-library`` + ``env`` Typer subapps.

Plan 04-01 shipped the ``deploy run`` subcommand (DEPLOY-01/03/04 surface)
that wraps ``sigantry_core.deploy.core.deploy_workspace`` and returns
non-zero on any item-publish failure (Success Criterion 1).

Plan 04-02 appended the ``fabric-item copy`` subcommand (DEPLOY-02) that
wraps ``sigantry_core.deploy.item_copy.copy_item`` — Fabric item folder
duplicator with ``logicalId`` regeneration + LF enforcement.

Plan 04-03 appends three more subapps, all additive:
  - ``git`` — 7 Fabric Core Git REST subcommands (connect / init / update /
    commit / status / connection / disconnect). ``disconnect`` is gated
    by ``@destructive_op`` and requires ``--force`` (T-3 / DEPLOY-05).
  - ``variable-library`` — full CRUD (create / list / get / update / delete).
    ``delete`` is gated by ``@destructive_op`` and requires ``--force``
    (DEPLOY-06).
  - ``env`` — Fabric Environment wheel upload (``sync`` — Pitfall 6).

Plan 04-01's ``deploy_app`` and Plan 04-02's ``fabric_item_app`` are
preserved byte-for-byte; the new apps are APPENDED at the bottom.
"""

from __future__ import annotations

import os
import subprocess
from typing import Annotated
from xml.sax.saxutils import escape

import typer
from rich.console import Console

from sigantry_core.client import FabricRestClient
from sigantry_core.deploy.core import deploy_workspace
from sigantry_core.deploy.dependency import DependencyCycleError, validate_order
from sigantry_core.deploy.environments_manifest import (
    EnvironmentsManifestError,
    load_environments_manifest,
)
from sigantry_core.deploy.environments_sync import sync_environments
from sigantry_core.deploy.item_copy import ItemCopyError, copy_item
from sigantry_core.deploy.notebook_binding import NotebookBindingError, set_notebook_binding
from sigantry_core.deploy.parameters import HardcodedGuidError, load_and_validate

deploy_app = typer.Typer(
    help="Deploy Fabric items from a Git working tree via fabric-cicd.",
    no_args_is_help=True,
)
_console = Console()


@deploy_app.command("run")
def deploy_cmd(
    source: str = typer.Option(
        ...,
        "--source",
        help="Repository directory containing Fabric items (.platform V2 schema).",
    ),
    workspace_id: str = typer.Option(..., "--workspace-id", help="Target Fabric workspace GUID."),
    environment: str = typer.Option(
        ...,
        "--environment",
        help="Parameter-file environment key (e.g. DEV, TEST, PROD).",
    ),
    params: str = typer.Option(
        None,
        "--params",
        help="parameters.yml path (default: <source>/parameters.yml).",
    ),
    item_types: str = typer.Option(
        "Lakehouse,Environment,Notebook,DataPipeline",
        "--item-types",
        help="Comma-separated item_type_in_scope list.",
    ),
    unpublish_orphans: bool = typer.Option(
        False,
        "--unpublish-orphans",
        help=(
            "Also delete items present in the workspace but absent from the "
            "tree. DESTRUCTIVE — requires --unpublish-force."
        ),
    ),
    unpublish_force: bool = typer.Option(
        False,
        "--unpublish-force",
        help="Required acknowledgement for --unpublish-orphans.",
    ),
    unpublish_runbook_id: str = typer.Option(
        None,
        "--unpublish-runbook-id",
        help="Optional runbook/incident id recorded in the audit log.",
    ),
    rollback: bool = typer.Option(
        False,
        "--rollback",
        help=(
            "Re-deploy a previously-recorded release set instead of "
            "forward-deploying. Requires --to-release AND --rollback-force."
        ),
    ),
    to_release: str | None = typer.Option(
        None,
        "--to-release",
        help="Release id to roll back to (required with --rollback).",
    ),
    rollback_force: bool = typer.Option(
        False,
        "--rollback-force",
        help=(
            "REQUIRED with --rollback: acknowledges that rollback supplants "
            "live workspace state with the recorded item set. Audit-2026-05-07 "
            "W1 follow-up: rollback now goes through @destructive_op."
        ),
    ),
    rollback_runbook_id: str | None = typer.Option(
        None,
        "--rollback-runbook-id",
        help="Optional runbook/incident id recorded in the rollback audit entry.",
    ),
    item_name_exclude_regex: str = typer.Option(
        None,
        "--item-name-exclude-regex",
        help="Skip items whose display name matches this regex (publish + unpublish).",
    ),
    folder_path_exclude_regex: str = typer.Option(
        None,
        "--folder-path-exclude-regex",
        help="Skip items under folder paths matching this regex (publish only).",
    ),
    folder_path_to_include: Annotated[
        list[str] | None,
        typer.Option(
            "--folder-path-to-include",
            help="Allow-list folder paths (publish only). Repeat to add multiple paths.",
        ),
    ] = None,
    items_to_include: Annotated[
        list[str] | None,
        typer.Option(
            "--items-to-include",
            help="Allow-list of item names (publish + unpublish). Repeat to add multiple items.",
        ),
    ] = None,
    shortcut_exclude_regex: str = typer.Option(
        None,
        "--shortcut-exclude-regex",
        help="Skip shortcuts whose name matches this regex (publish only).",
    ),
    tenant_id: str = typer.Option(
        None,
        "--tenant-id",
        help="Explicit Azure tenant id (overrides az-login / WIF default).",
    ),
    audit_dir: str | None = typer.Option(
        None,
        "--audit-dir",
        help=(
            "Override ~/.sigantry/audit/ when looking up the recorded "
            "release (--rollback) and writing the rollback DeployRecord. "
            "Used in tests + hermetic CI runners."
        ),
    ),
    bulk: bool = typer.Option(
        False,
        "--bulk",
        help="Enable concurrent bulk publish acceleration for multi-item publish.",
    ),
) -> None:
    """Deploy a Fabric item tree. Non-zero exit on any item-publish failure."""
    # NOTE: rollback branch must execute BEFORE the existing
    # --unpublish-force / required-flag validators below so that
    # `--rollback` without `--to-release` surfaces a `--to-release`
    # BadParameter rather than an unrelated forward-path validation error.
    if rollback:
        if not to_release:
            raise typer.BadParameter("--rollback requires --to-release <release_id>.")
        if unpublish_orphans:
            raise typer.BadParameter(
                "--rollback cannot be combined with --unpublish-orphans. "
                "Rollback re-applies a known-good recorded state; orphan "
                "unpublish is the destructive forward-deploy path."
            )
        # Lazy import keeps the forward-deploy path untouched by the
        # module-import-time append_feature_flag side effects (Pitfall 1).
        from pathlib import Path as _Path

        from sigantry_core.deploy.rollback import rollback_to_release

        audit_dir_path = _Path(audit_dir) if audit_dir else None

        types = [t.strip() for t in item_types.split(",") if t.strip()]
        if not rollback_force:
            _console.print(
                "[red]rollback refused[/red]: --rollback requires --rollback-force "
                "(acknowledges the destructive replay)."
            )
            raise typer.Exit(code=2)
        try:
            result = rollback_to_release(
                release_id=to_release,
                workspace=workspace_id,
                repository_directory=source,
                environment=environment,
                item_type_in_scope=types,
                parameters_path=params,
                audit_dir=audit_dir_path,
                force=rollback_force,
                runbook_id=rollback_runbook_id,
            )
        except ValueError as exc:
            _console.print(f"[red]rollback failed[/red]: {exc}")
            raise typer.Exit(code=1) from exc

        # Open Q3 / PATTERNS.md: emit a NEW DeployRecord for the rollback
        # action so `sigantry release list` shows the audit trail. Recorded
        # from the CLI wrapper (NOT inside rollback_to_release) per the
        # documented trade-off -- programmatic library callers manage their
        # own audit trail.
        from datetime import UTC, datetime

        from sigantry_core.governance.audit import emit_deploy_record
        from sigantry_core.release.ledger import find_by_release_id
        from sigantry_core.release.record import DeployRecord

        original = find_by_release_id(to_release, audit_dir=audit_dir_path)
        if original is not None:
            # CR-01 (review fix): capture the timestamp ONCE so the
            # release_id suffix and the persisted created_at agree. The
            # SHA-256 audit_hash is computed over the canonical payload
            # (which includes created_at), so two separate
            # ``datetime.now(UTC)`` calls could straddle a second boundary
            # and persist a release_id whose embedded timestamp never
            # appears in the record body -- looking like ledger tampering
            # to incident-triage operators.
            now = datetime.now(UTC)
            rollback_record = DeployRecord(
                workspace=workspace_id,
                release_id=(f"rollback-of-{to_release}-{now.isoformat(timespec='seconds')}"),
                work_items=[],
                fabric_items_changed=list(original.fabric_items_changed),
                test_evidence={"rollback_of": to_release},
                approver="cli@sigantry",
                audit_hash="",
                created_at=now,
            ).with_hash()
            # CR-01 (review fix): honour --audit-dir so hermetic CI
            # runners do not silently leak rollback records to
            # ~/.sigantry/audit/deploys.jsonl on the runner.
            emit_deploy_record(rollback_record, audit_dir=audit_dir_path)

        _console.print_json(
            data={
                "workspaceId": result.workspace_id,
                "environment": result.environment,
                "itemsPublished": result.items_published,
                "itemsFailed": result.items_failed,
                "orphansUnpublished": result.orphans_unpublished,
                "dotGraphPath": result.dot_graph_path,
                "rollbackOfRelease": to_release,
            }
        )
        return

    from sigantry_core.auth import TokenProvider

    tp = (
        TokenProvider.from_defaults()
        if tenant_id is None
        else TokenProvider.from_defaults(tenant_id=tenant_id)
    )
    types = [t.strip() for t in item_types.split(",") if t.strip()]
    # Typer passes an empty list when the flag isn't supplied; normalise to None
    # so fabric-cicd sees "no allow-list" rather than "empty allow-list".
    folder_paths = folder_path_to_include or None
    items_include = items_to_include or None
    try:
        result = deploy_workspace(
            workspace_id=workspace_id,
            repository_directory=source,
            environment=environment,
            item_type_in_scope=types,
            parameters_path=params,
            token_provider=tp,
            unpublish_orphans=unpublish_orphans,
            unpublish_force=unpublish_force,
            unpublish_runbook_id=unpublish_runbook_id,
            item_name_exclude_regex=item_name_exclude_regex,
            folder_path_exclude_regex=folder_path_exclude_regex,
            folder_path_to_include=folder_paths,
            items_to_include=items_include,
            shortcut_exclude_regex=shortcut_exclude_regex,
            bulk=bulk,
        )
    except Exception as exc:  # CLI boundary: surface anything to the user.
        _console.print(f"[red]deploy failed[/red]: {exc}")
        raise typer.Exit(code=1) from exc
    _console.print_json(
        data={
            "workspaceId": result.workspace_id,
            "environment": result.environment,
            "itemsPublished": result.items_published,
            "itemsFailed": result.items_failed,
            "orphansUnpublished": result.orphans_unpublished,
            "dotGraphPath": result.dot_graph_path,
        }
    )


# ---------------------------------------------------------------------------
# Plan 05-02: deploy validate subcommand (ADOPIPE-05 Python surface)
# ---------------------------------------------------------------------------


@deploy_app.command("validate")
def validate_cmd(
    source: str = typer.Option(
        ...,
        "--source",
        help="Fabric items directory to validate (the .platform tree).",
    ),
    params: str = typer.Option(..., "--params", help="parameters.yml path."),
    item_types: str = typer.Option(
        "Lakehouse,Environment,Notebook,DataPipeline",
        "--item-types",
        help="Comma-separated item_type_in_scope list.",
    ),
    junit_xml: str = typer.Option(None, "--junit-xml", help="Optional JUnit XML output path."),
    dot_output: str = typer.Option(
        None, "--dot-output", help="Optional DOT graph output directory."
    ),
    skip_pre_commit: bool = typer.Option(
        False,
        "--skip-pre-commit",
        help="Skip the pre-commit fabric-item consistency backstop.",
    ),
) -> None:
    """Validate Fabric items WITHOUT deploying (ADOPIPE-05).

    Composes three Phase 4 primitives:
      1. load_and_validate(params) - parameters.yml schema + no-hardcoded-GUID
         + $ENV:<VAR> reachability.
      2. validate_order(source, item_types) - Kahn cycle detection + DOT emit.
      3. pre-commit run fabric-check-logical-id fabric-check-crlf --all-files -
         CRLF + logicalId uniqueness backstop.

    Exit codes:
      - 0  all green.
      - 1  any check failed.
      - 2  params file missing (surfaced as FileNotFoundError at step 1).
    """
    errors: list[tuple[str, str]] = []
    types = [t.strip() for t in item_types.split(",") if t.strip()]

    # (1) parameters.yml - FileNotFoundError is an exit-2 signal per the
    # RESEARCH section 14 Example 5 contract. HardcodedGuidError and the
    # $ENV: RuntimeError land in `errors` (exit 1).
    try:
        load_and_validate(params)
        _console.print(f"[green]OK[/green]  parameters: {params}")
    except FileNotFoundError as e:
        _console.print(f"[red]FAIL[/red] parameters missing: {e}")
        if junit_xml:
            _emit_junit(junit_xml, [("parameters", str(e))])
        raise typer.Exit(code=2) from e
    except (HardcodedGuidError, ValueError, RuntimeError) as e:
        errors.append(("parameters", f"{type(e).__name__}: {e}"))
        _console.print(f"[red]FAIL[/red] parameters: {e}")

    # (2) dependency graph - validate_order handles DOT emission itself.
    # Missing source is FAIL (T-5-10); cycle is FAIL.
    dot_path: str | None = None
    try:
        dot_kwargs: dict[str, object] = {
            "repository_directory": source,
            "item_type_in_scope": types,
        }
        if dot_output:
            dot_kwargs["dot_output_dir"] = dot_output
        dot_path = validate_order(**dot_kwargs)  # type: ignore[arg-type]
        _console.print(f"[green]OK[/green]  dependency graph ({dot_path})")
    except FileNotFoundError as e:
        errors.append(("dependency", f"source missing: {e}"))
        _console.print(f"[red]FAIL[/red] source: {e}")
    except DependencyCycleError as e:
        errors.append(("dependency", f"cycle: {e}"))
        _console.print(f"[red]FAIL[/red] dependency cycle: {e}")

    # (3) pre-commit hooks - A3 assumption mitigation: absence is WARN.
    if not skip_pre_commit:
        try:
            subprocess.run(
                [
                    "pre-commit",
                    "run",
                    "fabric-check-logical-id",
                    "fabric-check-crlf",
                    "--all-files",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            _console.print("[green]OK[/green]  pre-commit hooks (fabric-check-*)")
        except FileNotFoundError:
            _console.print("[yellow]WARN[/yellow] pre-commit not installed; skipping")
        except subprocess.CalledProcessError as e:
            errors.append(("pre_commit", f"stdout={e.stdout!r} stderr={e.stderr!r}"))
            _console.print("[red]FAIL[/red] pre-commit hooks failed")

    if junit_xml:
        _emit_junit(junit_xml, errors)

    if errors:
        _console.print(f"[red]{len(errors)} check(s) failed[/red]")
        raise typer.Exit(code=1)
    _console.print("[green]All validation checks passed[/green]")


def _emit_junit(path: str, errors: list[tuple[str, str]]) -> None:
    """Write a minimal JUnit XML with three testcases: parameters / dependency / pre_commit.

    Failed checks get a ``<failure>`` child; succeeded checks get an empty
    testcase. Parent dir is created if absent. The XML is self-contained and
    parses in every JUnit-aware consumer (ADO PublishTestResults@2, Jenkins).
    """
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    err_map = dict(errors)
    checks = ("parameters", "dependency", "pre_commit")
    cases: list[str] = []
    for name in checks:
        if name in err_map:
            cases.append(
                f'    <testcase classname="fabric-dataops.deploy.validate" '
                f'name="{name}"><failure>{escape(err_map[name])}</failure></testcase>'
            )
        else:
            cases.append(
                f'    <testcase classname="fabric-dataops.deploy.validate" name="{name}"/>'
            )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="fabric-dataops deploy validate" '
        f'tests="{len(checks)}" failures="{len(err_map)}">\n'
        + "\n".join(cases)
        + "\n</testsuite>\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


# ---------------------------------------------------------------------------
# Plan 04-02: fabric-item subapp (DEPLOY-02)
# ---------------------------------------------------------------------------

fabric_item_app = typer.Typer(
    help="Fabric item folder operations (copy with logicalId regeneration).",
    no_args_is_help=True,
)


@fabric_item_app.command("copy")
def copy_cmd(
    src: str = typer.Argument(..., help="Source item folder (must contain a .platform file)."),
    dst: str = typer.Argument(..., help="Destination item folder (must NOT already exist)."),
    new_display_name: str = typer.Option(
        ...,
        "--new-display-name",
        help="displayName for the copied item; must be unique within the repo.",
    ),
    new_description: str = typer.Option(
        None,
        "--new-description",
        help="Optional description override; source's description is preserved when omitted.",
    ),
) -> None:
    """Duplicate a Fabric item folder with a fresh logicalId and displayName."""
    try:
        new_id = copy_item(
            src,
            dst,
            new_display_name=new_display_name,
            new_description=new_description,
        )
    except ItemCopyError as exc:
        _console.print(f"[red]fabric-item copy failed[/red]: {exc}")
        raise typer.Exit(code=1) from exc
    _console.print(f"Copied {src} -> {dst} (new logicalId={new_id})")


# ---------------------------------------------------------------------------
# Plan 04-03: shared client factory for Git / Variable Library / Env subapps
# ---------------------------------------------------------------------------


def _client(tenant_id: str | None) -> FabricRestClient:
    """Construct a FabricRestClient honouring ``--tenant-id`` if supplied."""
    if tenant_id is None:
        return FabricRestClient.from_defaults()
    return FabricRestClient.from_defaults(tenant_id=tenant_id)


@fabric_item_app.command("set-binding")
def set_binding_cmd(
    workspace_id: str = typer.Option(
        ..., "--workspace-id", help="Workspace GUID holding the notebook."
    ),
    item_id: str = typer.Option(..., "--item-id", help="Notebook item GUID."),
    environment_id: str = typer.Option(
        None, "--environment-id", help="Environment GUID to attach."
    ),
    environment_workspace_id: str = typer.Option(
        None,
        "--environment-workspace-id",
        help="Workspace GUID that owns the environment (envs may attach cross-workspace).",
    ),
    lakehouse_id: str = typer.Option(None, "--lakehouse-id", help="Default lakehouse GUID."),
    lakehouse_name: str = typer.Option(None, "--lakehouse-name", help="Default lakehouse name."),
    lakehouse_workspace_id: str = typer.Option(
        None, "--lakehouse-workspace-id", help="Workspace GUID that owns the lakehouse (optional)."
    ),
    tenant_id: str = typer.Option(None, "--tenant-id", help="Override tenant for auth."),
) -> None:
    """Attach an Environment and/or default Lakehouse to a notebook.

    The binding lives inside the notebook's ipynb (``metadata.dependencies``)
    and is therefore WIPED whenever the definition is replaced
    (``sync apply --republish-existing``, raw ``updateDefinition``,
    git-sync). Run this AFTER such a deploy to re-apply it. Source-controlled
    notebooks stay GUID-free; the binding is applied per target workspace.
    Idempotent.
    """
    try:
        with _client(tenant_id) as client:
            result = set_notebook_binding(
                client,
                workspace_id=workspace_id,
                item_id=item_id,
                environment_id=environment_id,
                environment_workspace_id=environment_workspace_id,
                lakehouse_id=lakehouse_id,
                lakehouse_name=lakehouse_name,
                lakehouse_workspace_id=lakehouse_workspace_id,
            )
    except NotebookBindingError as exc:
        _console.print(f"[red]fabric-item set-binding failed[/red]: {exc}")
        raise typer.Exit(code=1) from exc

    if result.changed:
        _console.print(
            f"[green]bound[/green] item {item_id}: "
            f"environment={result.environment} lakehouse={result.lakehouse}"
        )
    else:
        _console.print(f"[cyan]no change[/cyan] item {item_id} already had the requested binding.")


# ---------------------------------------------------------------------------
# Plan 04-03: git subapp (DEPLOY-05)
# ---------------------------------------------------------------------------

from sigantry_core.deploy.git_integration import (  # noqa: E402
    commit_to_git,
    connect_azdo,
    disconnect,
    get_connection,
    get_status,
    initialize_connection,
    update_from_git,
)

git_app = typer.Typer(
    help="Fabric workspace <-> ADO Git integration (7-endpoint surface).",
    no_args_is_help=True,
)


@git_app.command("connect")
def git_connect_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    organization_name: str = typer.Option(..., "--ado-organization"),
    project_name: str = typer.Option(..., "--ado-project"),
    repository_name: str = typer.Option(..., "--ado-repository"),
    branch_name: str = typer.Option(..., "--branch"),
    directory_name: str = typer.Option(..., "--directory"),
    git_connection_id: str = typer.Option(
        ...,
        "--git-connection-id",
        help=(
            "Pre-provisioned Fabric Connection id for the ADO PAT "
            "(SP-compatible ConfiguredConnection path; T-4-06 / Pitfall 4C)."
        ),
    ),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Attach a Fabric workspace to an ADO repo via ConfiguredConnection."""
    with _client(tenant_id) as c:
        connect_azdo(
            c,
            workspace_id,
            organization_name=organization_name,
            project_name=project_name,
            repository_name=repository_name,
            branch_name=branch_name,
            directory_name=directory_name,
            git_connection_id=git_connection_id,
        )
    _console.print(
        f"connected workspace {workspace_id} to "
        f"{organization_name}/{project_name}/{repository_name}#{branch_name}"
    )


@git_app.command("init")
def git_init_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    strategy: str = typer.Option("PreferRemote", "--strategy"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Initialise the connection between workspace and remote Git."""
    with _client(tenant_id) as c:
        body = initialize_connection(c, workspace_id, strategy=strategy)  # type: ignore[arg-type]
    _console.print_json(data=body or {})


@git_app.command("update")
def git_update_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    workspace_head: str = typer.Option(..., "--workspace-head"),
    remote_commit_hash: str = typer.Option(..., "--remote-commit"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Pull remote commits into the workspace."""
    with _client(tenant_id) as c:
        update_from_git(
            c,
            workspace_id,
            workspace_head=workspace_head,
            remote_commit_hash=remote_commit_hash,
        )
    _console.print(f"workspace {workspace_id} updated from remote {remote_commit_hash}")


@git_app.command("commit")
def git_commit_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    workspace_head: str = typer.Option(..., "--workspace-head"),
    comment: str = typer.Option(..., "--comment"),
    mode: str = typer.Option("All", "--mode"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Commit workspace changes back to the connected Git branch."""
    with _client(tenant_id) as c:
        commit_to_git(
            c,
            workspace_id,
            workspace_head=workspace_head,
            comment=comment,
            mode=mode,  # type: ignore[arg-type]
        )
    _console.print(f"committed workspace {workspace_id} (mode={mode})")


@git_app.command("status")
def git_status_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Dump the current Git status for the workspace."""
    with _client(tenant_id) as c:
        body = get_status(c, workspace_id)
    _console.print_json(data=body)


@git_app.command("connection")
def git_connection_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Describe the workspace's Git connection (state + provider details)."""
    with _client(tenant_id) as c:
        gc = get_connection(c, workspace_id)
    _console.print_json(
        data={
            "state": gc.state,
            "providerType": gc.provider_type,
            "organization": gc.organization_name,
            "project": gc.project_name,
            "repository": gc.repository_name,
            "branch": gc.branch_name,
            "directory": gc.directory_name,
        }
    )


@git_app.command("disconnect")
def git_disconnect_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    force: bool = typer.Option(False, "--force", help="REQUIRED: acknowledges destruction."),
    runbook_id: str = typer.Option(None, "--runbook-id"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Disconnect the workspace from Git (destructive; requires --force)."""
    with _client(tenant_id) as c:
        disconnect(
            c,
            workspace_id,
            force=force,
            runbook_id=runbook_id,
            resource_id=workspace_id,
        )
    _console.print(f"workspace {workspace_id} disconnected from Git")


# ---------------------------------------------------------------------------
# Plan 04-03: variable-library subapp (DEPLOY-06)
# ---------------------------------------------------------------------------

from sigantry_core.deploy.variable_library import (  # noqa: E402
    create_variable_library,
    delete_variable_library,
    get_variable_library,
    list_variable_libraries,
    update_variable_library,
)

variable_library_app = typer.Typer(
    help="Fabric Variable Library CRUD (DEPLOY-06).",
    no_args_is_help=True,
)


@variable_library_app.command("create")
def vl_create_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    display_name: str = typer.Option(..., "--name"),
    description: str = typer.Option(None, "--description"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Create a Variable Library (display-name only; no bundled definition)."""
    with _client(tenant_id) as c:
        vl = create_variable_library(
            c,
            workspace_id,
            display_name=display_name,
            description=description,
        )
    _console.print_json(
        data={
            "id": vl.id,
            "displayName": vl.display_name,
            "activeValueSet": vl.active_value_set,
        }
    )


@variable_library_app.command("list")
def vl_list_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """List all Variable Libraries in a workspace."""
    with _client(tenant_id) as c:
        rows = [
            {
                "id": vl.id,
                "displayName": vl.display_name,
                "activeValueSet": vl.active_value_set,
            }
            for vl in list_variable_libraries(c, workspace_id)
        ]
    _console.print_json(data=rows)


@variable_library_app.command("get")
def vl_get_cmd(
    variable_library_id: str = typer.Argument(...),
    workspace_id: str = typer.Option(..., "--workspace-id"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Fetch a single Variable Library by id."""
    with _client(tenant_id) as c:
        vl = get_variable_library(c, workspace_id, variable_library_id)
    _console.print_json(
        data={
            "id": vl.id,
            "displayName": vl.display_name,
            "description": vl.description,
            "activeValueSet": vl.active_value_set,
        }
    )


@variable_library_app.command("update")
def vl_update_cmd(
    variable_library_id: str = typer.Argument(...),
    workspace_id: str = typer.Option(..., "--workspace-id"),
    display_name: str = typer.Option(None, "--name"),
    description: str = typer.Option(None, "--description"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Patch a Variable Library's display-name / description."""
    with _client(tenant_id) as c:
        vl = update_variable_library(
            c,
            workspace_id,
            variable_library_id,
            display_name=display_name,
            description=description,
        )
    _console.print_json(data={"id": vl.id, "displayName": vl.display_name})


@variable_library_app.command("delete")
def vl_delete_cmd(
    variable_library_id: str = typer.Argument(...),
    workspace_id: str = typer.Option(..., "--workspace-id"),
    force: bool = typer.Option(False, "--force", help="REQUIRED: acknowledges destruction."),
    runbook_id: str = typer.Option(None, "--runbook-id"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Delete a Variable Library (destructive; requires --force)."""
    with _client(tenant_id) as c:
        delete_variable_library(
            c,
            workspace_id,
            variable_library_id,
            force=force,
            runbook_id=runbook_id,
            resource_id=variable_library_id,
        )
    _console.print(f"variableLibrary {variable_library_id} deleted")


# ---------------------------------------------------------------------------
# Plan 04-03: env subapp (Pitfall 6 primitive)
# ---------------------------------------------------------------------------

from sigantry_core.deploy.environment import reconcile_wheels, sync_wheel  # noqa: E402

env_app = typer.Typer(
    help="Fabric Environment wheel upload (Pitfall 6 primitive).",
    no_args_is_help=True,
)


@env_app.command("sync")
def env_sync_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    environment_id: str = typer.Option(..., "--environment-id"),
    wheel: str = typer.Option(..., "--wheel", help="Path to the .whl file."),
    expected_sha256: str = typer.Option(None, "--expected-sha256"),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Upload + publish a wheel to a Fabric Environment."""
    with _client(tenant_id) as c:
        result = sync_wheel(
            c,
            workspace_id,
            environment_id,
            wheel,
            expected_sha256=expected_sha256,
        )
    _console.print_json(
        data={
            "workspaceId": result.workspace_id,
            "environmentId": result.environment_id,
            "wheelName": result.wheel_name,
            "stagingUploadStatus": result.staging_upload_status,
            "publishLroStatus": result.publish_lro_status,
            "installedLibraryName": result.installed_library_name,
        }
    )


@env_app.command("sync-all")
def env_sync_all_cmd(
    manifest: str = typer.Option(..., "--manifest", help="Path to the environments.yml manifest."),
    include_gated: bool = typer.Option(
        False,
        "--include-gated",
        help="Also act on gated targets (e.g. PROD). Off by default so an "
        "on-release trigger never touches gated environments unattended.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-publish even when the wheel is already installed (skips the "
        "idempotency check and forces a Spark image rebuild).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve + plan only; performs no tenant calls and mutates nothing.",
    ),
    fail_fast: bool = typer.Option(
        False,
        "--fail-fast",
        help="Stop at the first failure (default: isolate and continue).",
    ),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Fan a wheel set out across many Fabric Environments from a config manifest.

    Exit code is non-zero if any target/wheel failed (after isolating the rest).
    """
    try:
        env_manifest = load_environments_manifest(manifest)
    except EnvironmentsManifestError as exc:
        _console.print(f"[red]environments manifest validation failed:[/red] {exc}")
        for violation in exc.violations:
            _console.print(f"  [yellow]-[/yellow] {violation}")
        raise typer.Exit(code=1) from exc

    if dry_run:
        report = sync_environments(None, env_manifest, include_gated=include_gated, dry_run=True)
    else:
        with _client(tenant_id) as client:
            report = sync_environments(
                client,
                env_manifest,
                include_gated=include_gated,
                force=force,
                fail_fast=fail_fast,
            )

    _console.print_json(data=report.to_dict())
    if not report.ok:
        raise typer.Exit(code=1)


@env_app.command("reconcile")
def env_reconcile_cmd(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    environment_id: str = typer.Option(..., "--environment-id"),
    wheel: list[str] = typer.Option(  # noqa: B008 -- typer convention: Option() lives in the default
        ..., "--wheel", help="Path to a desired .whl (repeatable; one per package)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; no delete/upload/publish."),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Reconcile an Environment's custom libraries to the desired wheel versions.

    Unlike ``env sync`` (add-only), this REMOVES superseded versions of each named
    package before publishing, so version *upgrades* don't fail on duplicate
    packages. Publishes once and blocks until the build finishes. Idempotent.
    """
    with _client(tenant_id) as c:
        result = reconcile_wheels(c, workspace_id, environment_id, list(wheel), dry_run=dry_run)
    _console.print_json(
        data={
            "workspaceId": result.workspace_id,
            "environmentId": result.environment_id,
            "desired": result.desired,
            "deleted": result.deleted,
            "uploaded": result.uploaded,
            "published": result.published,
            "publishState": result.publish_state,
            "dryRun": result.dry_run,
        }
    )
    if (
        not result.dry_run
        and result.publish_state
        and result.publish_state
        not in (
            "Success",
            "Succeeded",
        )
    ):
        raise typer.Exit(code=1)
