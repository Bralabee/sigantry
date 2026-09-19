"""``sigantry-core dq gate`` Typer subapp (PROD-06).

Delegates to the :func:`sigantry_core.dq.dispatcher.run_gate`
dispatcher. No DQ framework is imported here — the named gate plugin owns
all framework-specific concerns.

Exit codes:

- ``0`` — gate returned ``success=True``.
- ``1`` — gate returned ``success=False`` (violation).
- ``2`` — registry resolution failure (``KeyError`` / ``ValueError``).
"""

from __future__ import annotations

import json

import typer
from rich.console import Console

from sigantry_core.dq.dispatcher import run_gate
from sigantry_core.protocols import DataRef

dq_app = typer.Typer(
    help="Run a registered DQ gate plugin against a dataset (PROD-06).",
    no_args_is_help=True,
)
_console = Console()


@dq_app.command("gate")
def gate_cmd(
    suite: str = typer.Option(..., "--suite", help="Suite identifier passed to the gate."),
    dataset: str = typer.Option(..., "--dataset", help="Dataset path / qualified name."),
    gate_name: str = typer.Option(
        None,
        "--gate",
        help=(
            "Name the gate plugin is registered under. Defaults to settings.dq.gate when available."
        ),
    ),
    dataset_name: str = typer.Option(
        None,
        "--dataset-name",
        help="Optional logical dataset name for the DataRef; defaults to dataset.",
    ),
) -> None:
    """Run a registered DQ gate against a dataset. 0 clean / 1 violation / 2 config."""
    data_ref = DataRef(name=dataset_name or dataset, path=dataset)
    try:
        result = run_gate(suite, data_ref, gate_name=gate_name)
    except (KeyError, ValueError) as exc:
        _console.print(f"[red]DQ gate config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    payload = {
        "suite": result.suite,
        "dataset": data_ref.path,
        "success": result.success,
        "violations": result.violations,
        "evaluated": result.evaluated,
        "run_id": result.run_id,
    }
    _console.print_json(json.dumps(payload))
    if not result.success:
        raise typer.Exit(code=1)
