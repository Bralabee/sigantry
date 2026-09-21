"""Enable ``python -m sigantry_core [subcommand]``.

Mirrors the ``sigantry`` console script so consumers that
do not have the script on ``PATH`` (e.g. isolated venvs, containers using
``python -m``) still reach the same Typer app.
"""

from sigantry_core.cli import app

if __name__ == "__main__":
    app()
