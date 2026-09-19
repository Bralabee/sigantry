"""Top-level ``sigantry diff`` Typer subapp (Phase 13 / DRIFT-01..02 / D-04).

Drift detection registers as the **15th** top-level subapp on the
``sigantry`` root CLI. It is its OWN top-level subapp (D-04), NOT
attached under ``sigantry sync``, mirroring the existing ``sigantry
release diff`` Phase 12 subcommand pattern.

Surface (single command via ``invoke_without_command=True`` callback):

* ``--environment`` (or ``-e``) -- environment label, informational;
  recorded in output for human readability + CI log scoping. Defaults
  to ``prod``. Operator-facing rationale: Typer's
  ``add_typer(...)`` + single-callback + positional ``Argument``
  pattern conflicts with Click's COMMAND slot when the operator types
  the positional BEFORE the options (the common ergonomic order). The
  plan's pseudo-code used a positional argument; reality required an
  option to keep the natural ``sigantry diff -e prod
  --workspace-id ...`` ergonomics. The flag is preserved on the
  surface for runbook + dashboard consumers.
* ``--workspace-id`` -- target Fabric workspace GUID.
* ``--manifest`` -- path to ``sync.yml``.
* ``--output {human|json}`` -- default ``human`` (Rich Table per D-25);
  ``json`` emits the SemVer-pinned wire contract via
  :meth:`DriftReport.to_json`.
* ``--fail-on-drift`` -- exit 1 on any detected drift (D-26).

Exit codes (D-26):

* ``0`` -- no drift, OR drift but ``--fail-on-drift`` not set.
* ``1`` -- drift detected AND ``--fail-on-drift`` set.
* ``2`` -- operational error (manifest validation, workspace not
  found, auth failure, pending Git Sync).

Human output (D-25): a Rich :class:`rich.table.Table` with columns
``Status | Type | Display Name | Folder | Detail`` and colorised
statuses (cyan ``+`` added, red ``-`` removed, yellow ``~`` modified,
green ``=`` unchanged).
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from sigantry_core.sync.diff import (
    DriftReport,
    diff_workspace_against_manifest,
)
from sigantry_core.sync.errors import (
    ManifestValidationError,
    WorkspacePendingGitUpdateError,
)

diff_app = typer.Typer(
    help=(
        "Drift detection between local sync.yml manifest and live Fabric workspace (DRIFT-01..02)."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)
_console = Console()


def _render_human_table(report: DriftReport, *, environment: str) -> None:
    """Print the Rich Table view (D-25).

    Status column carries colorised glyph + status text:
    cyan ``+`` for added, red ``-`` for removed, yellow ``~`` for
    modified, green ``=`` for unchanged. Each row's Detail column
    surfaces ``logical_id`` for added / removed / unchanged, and
    ``fields_changed`` for modified.
    """
    title = f"Sigantry drift report -- environment={environment!r}"
    table = Table(title=title)
    table.add_column("Status")
    table.add_column("Type")
    table.add_column("Display Name")
    table.add_column("Folder")
    table.add_column("Detail")

    for entry in report.added:
        table.add_row(
            "[cyan]+[/cyan] added",
            str(entry.get("type", "")),
            str(entry.get("display_name", "")),
            str(entry.get("folder_path", "")),
            str(entry.get("logical_id", "")),
        )
    for entry in report.removed:
        table.add_row(
            "[red]-[/red] removed",
            str(entry.get("type", "")),
            str(entry.get("display_name", "")),
            str(entry.get("folder_path", "")),
            str(entry.get("logical_id", "")),
        )
    for entry in report.modified:
        fields_changed = entry.get("fields_changed", [])
        if isinstance(fields_changed, list):
            detail = ", ".join(str(f) for f in fields_changed)
        else:
            detail = str(fields_changed)
        table.add_row(
            "[yellow]~[/yellow] modified",
            "",
            "",
            "",
            f"{entry.get('logical_id', '')}: {detail}",
        )
    for entry in report.unchanged:
        table.add_row(
            "[green]=[/green] unchanged",
            "",
            "",
            "",
            str(entry.get("logical_id", "")),
        )

    _console.print(table)
    if not report.has_drift():
        _console.print("[green]no drift[/green]")
    else:
        _console.print(
            f"[bold]drift summary[/bold] "
            f"+{len(report.added)} -{len(report.removed)} "
            f"~{len(report.modified)} ={len(report.unchanged)}"
        )


@diff_app.callback(invoke_without_command=True)
def diff_cmd(
    environment: str = typer.Option(
        "prod",
        "--environment",
        "-e",
        help=(
            "Environment label (informational; recorded in output for "
            "human readability + CI log scoping). Default: 'prod'."
        ),
    ),
    workspace_id: str = typer.Option(
        ...,
        "--workspace-id",
        help="Target Fabric workspace GUID.",
    ),
    manifest: str = typer.Option(
        ...,
        "--manifest",
        help="Path to sync.yml.",
    ),
    output: str = typer.Option(
        "human",
        "--output",
        help="Output format: 'human' (default) or 'json' (SemVer-pinned wire contract).",
    ),
    fail_on_drift: bool = typer.Option(
        False,
        "--fail-on-drift",
        help="Exit 1 on any detected drift (D-26).",
    ),
    no_hint: bool = typer.Option(
        False,
        "--no-hint",
        help="Suppress operator hint trailer (CI-friendly).",
    ),
) -> None:
    """Compute drift between ``--manifest`` and ``--workspace-id``.

    On clean state: exit 0; print "no drift" line in human mode or the
    canonical empty-buckets JSON in json mode.

    On drift WITHOUT ``--fail-on-drift``: exit 0; surface drift in the
    selected output format (operators wanting CI gating must set
    ``--fail-on-drift``).

    On drift WITH ``--fail-on-drift``: exit 1.

    On operational error (manifest validation,
    :class:`WorkspacePendingGitUpdateError`, REST / auth failure):
    exit 2 with a red error message.
    """
    if output not in {"human", "json"}:
        _console.print(f"[red]Invalid --output {output!r}; must be 'human' or 'json'.[/red]")
        raise typer.Exit(code=2)

    try:
        report = diff_workspace_against_manifest(manifest, workspace_id)
    except ManifestValidationError as exc:
        _console.print(f"[red]Manifest validation failed:[/red] {exc}")
        for v in exc.violations:
            _console.print(f"  [yellow]-[/yellow] {v}")
        raise typer.Exit(code=2) from exc
    except WorkspacePendingGitUpdateError as exc:
        _console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        # Operational error -- workspace not found, auth chain failure,
        # network issue, or any unexpected SyncEngineError subclass. Exit
        # 2 (D-26) so CI runners can branch the same way they do for
        # WorkspacePendingGitUpdateError.
        _console.print(f"[red]sigantry diff failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if output == "json":
        _console.print_json(data=report.to_json())
    else:
        _render_human_table(report, environment=environment)

    # D-19-05 / DOCS-H-06: surface the snapshot-not-remediation reminder
    # when drift is detected. Suppressed under --output json because that
    # mode is captured by scheduled drift pipelines into drift.json
    # (templates/schedules/drift-check.yml + .github/workflows/drift-check.yml);
    # a trailer on stdout under JSON would corrupt the wire contract
    # (Pitfall 1 LOAD-BEARING). Suppressed by --no-hint for CI-friendly
    # operation. The gate is `output == "human"` (positive whitelist) NOT
    # `output != "json"` -- defensive against future --output yaml/csv
    # modes. Fires BEFORE the fail-on-drift exit so it is visible even
    # when exit_code=1 (Pitfall 5). See ADR-0012 +
    # docs/runbooks/drift-detection/scheduled-drift.md section 0.
    if output == "human" and not no_hint and report.has_drift():
        drift_total = len(report.added) + len(report.removed) + len(report.modified)
        _console.print(
            f"[yellow]note:[/yellow] {drift_total} item(s) drifted. "
            f"This is a snapshot, NOT auto-remediated. Run "
            f"[cyan]sigantry sync apply[/cyan] to reconcile, or "
            f"[cyan]sigantry deploy run[/cyan] to publish first-time items. "
            f"See docs/runbooks/drift-detection/scheduled-drift.md section 0."
        )

    if report.has_drift() and fail_on_drift:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


__all__ = ("diff_app",)
