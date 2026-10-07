"""CLI subapp for preflight pre-deployment validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from sigantry_core._cli_tenant import resolve_tenant_id, stops_on_tenant_error
from sigantry_core.auth.token_provider import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.preflight.engine import PreflightEngine
from sigantry_core.preflight.models import ProbeStatus

preflight_app = typer.Typer(
    help="Run pre-deployment safety probes (ADR-0015).",
    no_args_is_help=False,
)

console = Console()


def _make_token_provider(tenant_id: str | None) -> TokenProvider:
    """Build the credential chain the probes acquire a token through.

    A module-level seam so tests can substitute a provider that never
    touches Azure. Construction is lazy: no token is requested here.
    """
    return TokenProvider.from_defaults(tenant_id=tenant_id)


@preflight_app.callback(invoke_without_command=True)
@stops_on_tenant_error(exit_code=1)
def main(
    ctx: typer.Context,
    manifest: Annotated[
        Path,
        typer.Option("--manifest", "-m", help="Path to manifest (sync.yml or workspace.yml)"),
    ] = Path("sync.yml"),
    params: Annotated[
        Path | None,
        typer.Option("--params", "-p", help="Path to deployment parameters.yml"),
    ] = None,
    environment: Annotated[
        str,
        typer.Option("--environment", "-e", help="Target deployment environment"),
    ] = "dev",
    workspace_id: Annotated[
        str | None,
        typer.Option(
            "--workspace-id",
            help="Target workspace; the capacity probe reads its capacity and is not checked without it",
        ),
    ] = None,
    tenant_id: Annotated[
        str | None,
        typer.Option(
            "--tenant-id",
            help=(
                "Entra tenant ID (GUID) to pin: a token from any other tenant is "
                "refused and the Entra probe fails. Default: core.tenant_id in the settings"
            ),
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Fail on a warning and on a probe that could not check (the CI gate)",
        ),
    ] = False,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit report in JSON format"),
    ] = False,
) -> None:
    """Execute pre-deployment safety probes against configuration and artifacts.

    Exit 1 on any failed probe. With --strict, also on any warning and on any
    probe that checked nothing (no credential, no --workspace-id). Exit 1 as
    well, with one line on stderr, for a tenant that is not a GUID or settings
    that cannot be loaded when no --tenant-id is given.
    """
    if ctx.invoked_subcommand is not None:
        return

    tenant_id = resolve_tenant_id(tenant_id)
    provider = _make_token_provider(tenant_id)
    engine = PreflightEngine()
    with FabricRestClient(token_provider=provider) as client:
        report = engine.run(
            manifest_path=manifest,
            environment=environment,
            params_path=params,
            client=client,
            token_provider=provider,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            strict=strict,
        )

    # A token refused as another tenant's is named on one stderr line too:
    # a table cell wraps the probe message, and stdout carries the JSON.
    for res in report.results:
        if res.details.get("tenant_refused"):
            typer.echo(f"sigantry: error: {res.name}: {' '.join(res.message.split())}", err=True)

    if output_json:
        typer.echo(json.dumps(report.model_dump(), indent=2))
        if not report.passed:
            raise typer.Exit(code=1)
        return

    # Rich table output. Cells are Text so a message holding ``[...]`` is
    # rendered, not read as markup.
    table = Table(title=f"Sigantry Preflight Probe Report ({environment})")
    table.add_column("Probe", style="bold cyan")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right", style="dim")
    table.add_column("Message")

    status_styles = {
        ProbeStatus.PASS: Text("PASS", style="bold green"),
        ProbeStatus.WARN: Text("WARN", style="bold yellow"),
        ProbeStatus.FAIL: Text("FAIL", style="bold red"),
        ProbeStatus.SKIP: Text("SKIP", style="dim"),
    }

    for res in report.results:
        table.add_row(
            Text(res.name),
            status_styles.get(res.status, Text(str(res.status))),
            Text(f"{res.duration_ms:.1f}ms"),
            Text(res.message),
        )

    console.print()
    console.print(table)
    console.print()

    skipped = report.skipped
    failed = report.failed
    checked = len(report.results) - len(skipped)
    elapsed = f"{report.total_duration_ms:.1f}ms"

    if report.passed:
        if skipped:
            console.print(
                Text(
                    f"⚠ Preflight passed on the {checked} probe(s) that checked; "
                    f"{len(skipped)} did not check anything: {', '.join(skipped)} "
                    f"(--strict fails on an unchecked probe) ({elapsed})",
                    style="bold yellow",
                )
            )
        elif report.has_warnings:
            console.print(
                Text(f"⚠ Preflight passed with warnings ({elapsed})", style="bold yellow")
            )
        else:
            console.print(
                Text(
                    f"✓ Preflight passed: all {checked} probes checked ({elapsed})",
                    style="bold green",
                )
            )
        return

    reasons: list[str] = []
    if failed:
        reasons.append(f"failed: {', '.join(failed)}")
    if strict and report.has_warnings:
        warned = [r.name for r in report.results if r.status == ProbeStatus.WARN]
        reasons.append(f"warned under --strict: {', '.join(warned)}")
    if strict and skipped:
        reasons.append(f"not checked under --strict: {', '.join(skipped)}")
    console.print(Text(f"✗ Preflight failed ({'; '.join(reasons)}) ({elapsed})", style="bold red"))
    raise typer.Exit(code=1)
