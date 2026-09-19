"""Typer app exposed as the `diagnose-auth` console script.

Invocation paths:
- `diagnose-auth` (post `pip install`)
- `python -m sigantry_core.auth.cli` (any env where scripts aren't on PATH)
- `typer.testing.CliRunner` in tests

Exit codes:
- 0 = healthy
- 2 = degraded (token works but tenant toggle missing OR not in sg-fabric-automation)
- 3 = broken (no credential returned a token)
- 4 = config/CLI error (malformed args, unknown scope, etc.)

Never logs the raw token. JSON output schema does not include a `token` key.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from sigantry_core.auth.audiences import (
    AZURE_DEVOPS_SCOPE,
    AZURE_RM_SCOPE,
    FABRIC_SCOPE,
    GRAPH_SCOPE,
    POWERBI_SCOPE,
    PURVIEW_SCOPE,
)
from sigantry_core.auth.diagnose import (
    build_report,
    check_entra_group,
    check_tenant_toggles,
    decode_token_claims,
)
from sigantry_core.auth.errors import TokenAcquisitionError
from sigantry_core.auth.token_provider import TokenProvider

app = typer.Typer(
    help="Diagnose Fabric authentication from the current runtime. "
    "Read-only. Never logs raw tokens."
)

console = Console()

_SCOPE_ALIASES = {
    "fabric": FABRIC_SCOPE,
    "powerbi": POWERBI_SCOPE,
    "graph": GRAPH_SCOPE,
    "purview": PURVIEW_SCOPE,
    "azurerm": AZURE_RM_SCOPE,
    "azuredevops": AZURE_DEVOPS_SCOPE,
}


def _resolve_scope(raw: str) -> str:
    low = raw.lower()
    if low in _SCOPE_ALIASES:
        return _SCOPE_ALIASES[low]
    if raw.endswith("/.default"):
        return raw
    raise typer.BadParameter(
        f"unknown scope {raw!r}. Use one of: {', '.join(_SCOPE_ALIASES)} "
        f"or a full scope ending in '/.default'."
    )


@app.command()
def diagnose(
    scope: Annotated[str, typer.Option(help="Scope alias or full /.default scope")] = "fabric",
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Expected tenant id (warns on mismatch)"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan without hitting Azure")
    ] = False,
    output: Annotated[str, typer.Option("--output", help="table or json")] = "table",
    principal_id: Annotated[
        str | None,
        typer.Option(
            "--principal-id",
            help="Object id of the SP to look up in Graph (omit for /me path)",
        ),
    ] = None,
) -> None:
    """Diagnose token acquisition, tenant-setting visibility, and Entra group membership."""
    if output not in ("table", "json"):
        console.print(f"[red]--output must be 'table' or 'json' (got {output!r})[/red]")
        raise typer.Exit(code=4)

    resolved_scope = _resolve_scope(scope)

    if dry_run:
        plan = {
            "mode": "dry-run",
            "scope": resolved_scope,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "output": output,
            "will_acquire_token": False,
            "will_probe_fabric_admin": False,
            "will_probe_graph": False,
        }
        if output == "json":
            console.print(json.dumps(plan, indent=2))
        else:
            tbl = Table(title="diagnose-auth (dry-run)")
            tbl.add_column("key")
            tbl.add_column("value")
            for k, v in plan.items():
                tbl.add_row(k, str(v))
            console.print(tbl)
        raise typer.Exit(code=0)

    # --- Live mode ---
    provider = TokenProvider(tenant_id=tenant_id)
    try:
        token = provider.get_token(resolved_scope)
    except TokenAcquisitionError as e:
        report = build_report(
            scope=resolved_scope,
            credential_used=e.credential_used,
            token=None,
            tenant_toggles=None,
            entra_groups=None,
        )
        _emit(output, report, error=str(e))
        raise typer.Exit(code=3) from e

    credential_used = provider.last_credential_class(resolved_scope)

    # Only probe Fabric admin + Graph when scope is Fabric.
    tenant_toggles = None
    entra_groups = None
    if resolved_scope == FABRIC_SCOPE:
        tenant_toggles = check_tenant_toggles(token)
        entra_groups = check_entra_group(token, principal_id=principal_id)

    # Tenant sanity check (Pitfall P1-6)
    claims = decode_token_claims(token)
    if tenant_id and claims.get("tid") and claims["tid"] != tenant_id:
        console.print(
            f"[yellow]WARNING[/yellow]: token tid {claims['tid']!r} "
            f"does not match expected {tenant_id!r}"
        )

    report = build_report(
        scope=resolved_scope,
        credential_used=credential_used,
        token=token,
        tenant_toggles=tenant_toggles,
        entra_groups=entra_groups,
    )
    _emit(output, report)
    raise typer.Exit(code=report["exit_code"])


def _emit(output: str, report: dict, *, error: str | None = None) -> None:
    """Render the report. NEVER include the raw token in output."""
    # Defensive: strip any 'token' key that might have leaked into the dict.
    report.pop("token", None)

    if output == "json":
        payload = dict(report)
        if error:
            payload["error"] = error
        console.print(json.dumps(payload, indent=2))
        return

    tbl = Table(title="diagnose-auth")
    tbl.add_column("Check")
    tbl.add_column("Status")
    tbl.add_column("Detail")

    tbl.add_row("credential_used", "-", str(report.get("credential_used") or "unknown"))
    tbl.add_row("scope", "-", str(report["scope"]))
    claims = report.get("token_claims") or {}
    for key in ("aud", "tid", "oid", "appid", "exp"):
        if key in claims:
            tbl.add_row(f"claim.{key}", "-", str(claims[key]))

    toggles = report.get("tenant_toggles")
    if toggles:
        tbl.add_row("tenant_toggles", toggles.get("status", "?"), toggles.get("detail", ""))
    groups = report.get("entra_groups")
    if groups:
        tbl.add_row("entra_groups", groups.get("status", "?"), groups.get("detail", ""))
    if error:
        tbl.add_row("error", "broken", error)
    tbl.add_row("exit_code", str(report["exit_code"]), "")

    console.print(tbl)


if __name__ == "__main__":  # pragma: no cover
    app()
