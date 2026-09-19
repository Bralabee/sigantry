"""CLI subapp for preflight pre-deployment validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from sigantry_core.preflight.engine import PreflightEngine
from sigantry_core.preflight.models import ProbeStatus

preflight_app = typer.Typer(
    help="Run pre-deployment simulation and safety probes (ADR-0015).",
    no_args_is_help=False,
)

console = Console()


@preflight_app.callback(invoke_without_command=True)
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
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Treat warnings as failures (strict mode)"),
    ] = False,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit report in JSON format"),
    ] = False,
) -> None:
    """Execute pre-deployment safety probes against configuration and artifacts."""
    if ctx.invoked_subcommand is not None:
        return

    engine = PreflightEngine()
    report = engine.run(
        manifest_path=manifest,
        environment=environment,
        params_path=params,
        strict=strict,
    )

    if output_json:
        typer.echo(json.dumps(report.model_dump(), indent=2))
        if not report.passed:
            raise typer.Exit(code=1)
        return

    # Rich table output
    table = Table(title=f"Sigantry Preflight Probe Report ({environment})")
    table.add_column("Probe", style="bold cyan")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right", style="dim")
    table.add_column("Message")

    status_styles = {
        ProbeStatus.PASS: "[bold green]PASS[/bold green]",
        ProbeStatus.WARN: "[bold yellow]WARN[/bold yellow]",
        ProbeStatus.FAIL: "[bold red]FAIL[/bold red]",
        ProbeStatus.SKIP: "[dim]SKIP[/dim]",
    }

    for res in report.results:
        table.add_row(
            res.name,
            status_styles.get(res.status, str(res.status)),
            f"{res.duration_ms:.1f}ms",
            res.message,
        )

    console.print()
    console.print(table)
    console.print()

    if report.passed:
        if report.has_warnings:
            console.print(
                f"[bold yellow]⚠ Preflight passed with warnings ({report.total_duration_ms:.1f}ms)[/bold yellow]"
            )
        else:
            console.print(
                f"[bold green]✓ Preflight simulation successful ({report.total_duration_ms:.1f}ms)[/bold green]"
            )
    else:
        console.print(
            f"[bold red]✗ Preflight simulation failed ({report.total_duration_ms:.1f}ms)[/bold red]"
        )
        raise typer.Exit(code=1)
