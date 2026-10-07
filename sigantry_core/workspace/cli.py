"""`fabric-dataops workspace` Typer subcommand."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from sigantry_core._cli_tenant import TENANT_ID_HELP, resolve_tenant_id, stops_on_tenant_error
from sigantry_core.client import FabricRestClient
from sigantry_core.workspace.bootstrap import (
    BootstrapValidationError,
    bootstrap_workspace,
    load_and_validate,
)
from sigantry_core.workspace.capacity import assign_to_capacity
from sigantry_core.workspace.core import (
    create_workspace,
    delete_workspace,
    get_workspace,
    list_workspaces,
)
from sigantry_core.workspace.items import list_items

workspace_app = typer.Typer(
    help="Fabric workspace CRUD + items + capacity assignment.",
    no_args_is_help=True,
)
_console = Console()


def _client_factory(tenant_id: str | None) -> FabricRestClient:
    """A Fabric client pinned to ``--tenant-id``, else to ``core.tenant_id``."""
    return FabricRestClient.from_defaults(tenant_id=resolve_tenant_id(tenant_id))


def _print_warning(message: str) -> None:
    """Write a bootstrap warning to stderr as one plain line, as it happens."""
    typer.echo(f"sigantry: warning: {message}", err=True)


@workspace_app.command("list")
@stops_on_tenant_error(exit_code=1)
def list_cmd(
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
    roles: str = typer.Option(None, "--roles", help="Filter by caller role (e.g. Admin)."),
    output: str = typer.Option("table", "--output", "-o", help="table|json"),
) -> None:
    with _client_factory(tenant_id) as client:
        rows = [
            {
                "id": w.id,
                "displayName": w.display_name,
                "type": w.type,
                "capacityId": w.capacity_id,
                "domainId": w.domain_id,
            }
            for w in list_workspaces(client, roles=roles)
        ]
    if output == "json":
        _console.print_json(data=rows)
    else:
        table = Table(title="Workspaces")
        for col in ("id", "displayName", "type", "capacityId", "domainId"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                *(
                    str(r.get(c) or "")
                    for c in ("id", "displayName", "type", "capacityId", "domainId")
                )
            )
        _console.print(table)


@workspace_app.command("get")
@stops_on_tenant_error(exit_code=1)
def get_cmd(
    workspace_id: str = typer.Argument(..., help="Workspace id (UUID)."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
) -> None:
    with _client_factory(tenant_id) as client:
        ws = get_workspace(client, workspace_id)
    _console.print_json(
        data={
            "id": ws.id,
            "displayName": ws.display_name,
            "type": ws.type,
            "capacityId": ws.capacity_id,
            "domainId": ws.domain_id,
            "description": ws.description,
        }
    )


@workspace_app.command("create")
@stops_on_tenant_error(exit_code=1)
def create_cmd(
    name: str = typer.Option(..., "--name", help="Display name."),
    capacity_id: str = typer.Option(None, "--capacity-id"),
    description: str = typer.Option(None, "--description"),
    domain_id: str = typer.Option(None, "--domain-id"),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
) -> None:
    with _client_factory(tenant_id) as client:
        ws = create_workspace(
            client,
            display_name=name,
            capacity_id=capacity_id,
            description=description,
            domain_id=domain_id,
        )
    _console.print_json(data={"id": ws.id, "displayName": ws.display_name})


@workspace_app.command("delete")
@stops_on_tenant_error(exit_code=1)
def delete_cmd(
    workspace_id: str = typer.Argument(..., help="Workspace id."),
    force: bool = typer.Option(False, "--force", help="REQUIRED: acknowledges destruction."),
    runbook_id: str = typer.Option(None, "--runbook-id", help="Optional incident id."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
) -> None:
    with _client_factory(tenant_id) as client:
        delete_workspace(
            client,
            workspace_id,
            force=force,
            runbook_id=runbook_id,
            resource_id=workspace_id,
        )
    _console.print(f"workspace {workspace_id} deleted")


@workspace_app.command("assign-capacity")
@stops_on_tenant_error(exit_code=1)
def assign_capacity_cmd(
    workspace_id: str = typer.Argument(...),
    capacity_id: str = typer.Argument(...),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
) -> None:
    with _client_factory(tenant_id) as client:
        assign_to_capacity(client, workspace_id, capacity_id)
    _console.print(f"workspace {workspace_id} assigned to capacity {capacity_id}")


@workspace_app.command("list-items")
@stops_on_tenant_error(exit_code=1)
def list_items_cmd(
    workspace_id: str = typer.Argument(...),
    item_type: str = typer.Option(None, "--type", help="Filter by item type."),
    output: str = typer.Option("table", "--output", "-o"),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
) -> None:
    with _client_factory(tenant_id) as client:
        rows = [
            {
                "id": it.id,
                "displayName": it.display_name,
                "type": it.type,
                "sensitivityLabelId": it.sensitivity_label_id,
            }
            for it in list_items(client, workspace_id, item_type=item_type)
        ]
    if output == "json":
        _console.print_json(data=rows)
    else:
        table = Table(title=f"Items in {workspace_id}")
        for col in ("id", "displayName", "type", "sensitivityLabelId"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                *(str(r.get(c) or "") for c in ("id", "displayName", "type", "sensitivityLabelId"))
            )
        _console.print(table)


@workspace_app.command("bootstrap")
@stops_on_tenant_error(exit_code=1)
def bootstrap_cmd(
    config_path: str = typer.Argument(
        ..., help="Path to workspace.yml manifest (BOOTSTRAP-XX, Phase 13.5)."
    ),
    tenant_id: str | None = typer.Option(None, "--tenant-id", help=TENANT_ID_HELP),
    audit_dir: str = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/ when writing the BootstrapRecord.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Probe current state + report which steps WOULD fire; do not POST anything.",
    ),
    operator: str = typer.Option(None, "--operator", help="Identity recorded on the audit record."),
) -> None:
    """Greenfield workspace materialiser.

    Reads ``workspace.yml``, runs the 5-call probe-before-act sequence
    (create workspace -> bind capacity -> create folders -> connect Git
    -> initialize), and emits a ``BootstrapRecord`` to
    ``~/.sigantry/audit/bootstraps.jsonl``. Idempotent: re-running on a
    fully-bootstrapped workspace is a no-op (every step reports
    ``already-converged``).

    When the chosen blueprint will create folders at the top level of a
    workspace that already has top-level folders with other names,
    bootstrap creates them beside those folders and warns: a
    ``sigantry: warning:`` line on stderr and a ``warnings`` list in the
    JSON report, in a dry run too. A run without a warning prints no
    ``warnings`` key.

    Exit codes:
      - 0 success (or dry-run reported successfully)
      - 1 runtime error from the underlying primitives (network, auth, etc.)
      - 2 schema validation error in workspace.yml
    """
    try:
        config = load_and_validate(config_path)
    except FileNotFoundError as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except BootstrapValidationError as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        _console.print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=2) from exc

    with _client_factory(tenant_id) as client:
        result = bootstrap_workspace(
            config,
            client=client,
            operator=operator,
            audit_dir=audit_dir,
            dry_run=dry_run,
            on_warning=_print_warning,
        )

    report: dict[str, object] = {
        "workspace_id": result.workspace.id,
        "workspace_name": result.workspace.display_name,
        "capacity_id": result.workspace.capacity_id,
        "stage": config.stage,
        "blueprint": config.blueprint or "explicit",
        "folders_present": [f.display_name for f in result.folders],
        "step_outcomes": result.step_outcomes,
        "audit_hash": result.record.audit_hash,
        "dry_run": result.dry_run,
    }
    # Only a run that warned carries the key, so every other run prints the
    # report 1.0.0 printed.
    if result.warnings:
        report["warnings"] = list(result.warnings)
    _console.print_json(data=report)
