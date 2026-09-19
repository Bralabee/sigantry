"""sigantry config validate -- thin Typer wrapper over deploy.parameters.load_and_validate.

Phase 14 Plan 14-01 (D-16 / 14-RESEARCH section "Pattern 5").

This subcommand validates a fabric-cicd parameters.yml file -- the
declarative substitution data consumed by ``sigantry deploy run``. It is
NOT to be confused with toolkit-settings validation: ``.fabric-dataops.toml``
is validated automatically at config-load time by pydantic-settings and
surfaces in ``sigantry doctor`` (see 14-RESEARCH section "Pitfall 8").

Wraps :func:`sigantry_core.deploy.parameters.load_and_validate` (DEPLOY-03)
so the validator stays a single source of truth for the parameters.yml
shape; this subcommand only adds CLI ergonomics (exit codes, stdout/stderr).

Exit codes:

* ``0`` -- file is structurally valid; prints ``OK -- N environment(s) parsed: ...``.
* ``1`` -- ``HardcodedGuidError`` (raw GUID outside an allowed reference form),
  or unresolved ``$ENV:<VAR>`` reference.
* ``2`` -- file not found.
"""

from __future__ import annotations

from pathlib import Path

import typer

from sigantry_core.deploy.parameters import HardcodedGuidError, load_and_validate

config_app = typer.Typer(
    help="Validate Sigantry configuration files (parameters.yml).",
    no_args_is_help=True,
)


@config_app.command("validate")
def validate(
    file: Path = typer.Argument(  # noqa: B008 -- typer convention: Argument() lives in the default
        ...,
        exists=False,  # file's own existence is checked by load_and_validate
        help="Path to the parameters.yml file to validate.",
    ),
) -> None:
    """Validate a fabric-cicd parameters.yml file.

    Exit codes:
        0 -- file is structurally valid; prints
             ``OK -- N environment(s) parsed: <comma-separated>``.
        1 -- HardcodedGuidError or unresolved ``$ENV:<VAR>`` reference.
        2 -- file not found.
    """
    try:
        result = load_and_validate(file)
    except FileNotFoundError:
        typer.echo(f"error: {file} not found", err=True)
        raise typer.Exit(code=2) from None
    except HardcodedGuidError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    envs = sorted(result.environments_seen)
    typer.echo(f"OK -- {len(envs)} environment(s) parsed: {', '.join(envs)}")
