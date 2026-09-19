"""`fabric-dataops capacity` Typer subcommand (Plan 03-02 Task 2).

Three subcommands:
- ``list``    - Fabric Core ``/v1/capacities`` via FabricRestClient (WKSP-04).
- ``pause``   - ARM suspend via FabricArmRestClient + destructive_op gate (WKSP-05).
- ``resume``  - ARM resume via FabricArmRestClient + destructive_op gate (WKSP-05).

Destructive subcommands (``pause`` / ``resume``) require BOTH ``--force`` and
``--runbook-id`` (Pitfall 11); omitting either raises :class:`DestructiveOpError`
which Typer surfaces as a non-zero exit code without hitting the HTTP client.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from sigantry_core.capacity.core import list_capacities
from sigantry_core.capacity.lifecycle import resume_capacity, suspend_capacity
from sigantry_core.client import FabricArmRestClient, FabricRestClient

capacity_app = typer.Typer(
    help="Fabric capacity inspection + lifecycle (pause/resume via ARM).",
    no_args_is_help=True,
)
_console = Console()


def _fabric_client_factory(tenant_id: str | None) -> FabricRestClient:
    return FabricRestClient.from_defaults(tenant_id=tenant_id)


def _arm_client_factory(tenant_id: str | None) -> FabricArmRestClient:
    return FabricArmRestClient.from_defaults(tenant_id=tenant_id)


@capacity_app.command("list")
def list_cmd(
    tenant_id: str = typer.Option(None, "--tenant-id", help="Optional AAD tenant id."),
    output: str = typer.Option("table", "--output", "-o", help="table|json"),
) -> None:
    """List every capacity visible to the caller (GET /v1/capacities)."""
    with _fabric_client_factory(tenant_id) as client:
        rows = [
            {
                "id": c.id,
                "displayName": c.display_name,
                "skuName": c.sku_name,
                "skuTier": c.sku_tier,
                "region": c.region,
                "state": c.state,
            }
            for c in list_capacities(client)
        ]
    if output == "json":
        _console.print_json(data=rows)
    else:
        table = Table(title="Capacities")
        for col in ("id", "displayName", "skuName", "skuTier", "region", "state"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                *(
                    str(r.get(c) or "")
                    for c in (
                        "id",
                        "displayName",
                        "skuName",
                        "skuTier",
                        "region",
                        "state",
                    )
                )
            )
        _console.print(table)


@capacity_app.command("pause")
def pause_cmd(
    subscription_id: str = typer.Argument(..., help="Azure subscription id."),
    resource_group: str = typer.Argument(..., help="Resource group name."),
    capacity_name: str = typer.Argument(..., help="Microsoft.Fabric/capacities name."),
    force: bool = typer.Option(False, "--force", help="REQUIRED: acknowledges destruction."),
    runbook_id: str = typer.Option(
        None,
        "--runbook-id",
        help="REQUIRED incident reference (e.g. INC-1234).",
    ),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Suspend a Fabric capacity (ARM 202 LRO, destructive_op gated)."""
    with _arm_client_factory(tenant_id) as client:
        suspend_capacity(
            client,
            subscription_id,
            resource_group,
            capacity_name,
            force=force,
            runbook_id=runbook_id,
        )
    _console.print(f"capacity {capacity_name} suspended (runbook={runbook_id})")


@capacity_app.command("resume")
def resume_cmd(
    subscription_id: str = typer.Argument(..., help="Azure subscription id."),
    resource_group: str = typer.Argument(..., help="Resource group name."),
    capacity_name: str = typer.Argument(..., help="Microsoft.Fabric/capacities name."),
    force: bool = typer.Option(False, "--force", help="REQUIRED: acknowledges destruction."),
    runbook_id: str = typer.Option(
        None,
        "--runbook-id",
        help="REQUIRED incident reference (e.g. INC-2000).",
    ),
    tenant_id: str = typer.Option(None, "--tenant-id"),
) -> None:
    """Resume a suspended Fabric capacity (ARM 202 LRO, destructive_op gated)."""
    with _arm_client_factory(tenant_id) as client:
        resume_capacity(
            client,
            subscription_id,
            resource_group,
            capacity_name,
            force=force,
            runbook_id=runbook_id,
        )
    _console.print(f"capacity {capacity_name} resumed (runbook={runbook_id})")
