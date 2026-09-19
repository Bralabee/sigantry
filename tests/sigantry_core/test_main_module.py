"""Smoke tests for ``python -m sigantry_core`` runpath.

Regression guard: the package shipped without ``__main__.py``, so consumers
in container images or isolated venvs who preferred ``python -m`` over the
console script hit ``No module named sigantry_core.__main__``.
"""

from __future__ import annotations

import subprocess
import sys
from importlib import util


def test_main_module_exists() -> None:
    """``sigantry_core.__main__`` resolves via the import system."""
    spec = util.find_spec("sigantry_core.__main__")
    assert spec is not None, "Package must ship __main__.py so `python -m sigantry_core` works."


def test_python_m_runs_cli_help() -> None:
    """`python -m sigantry_core --help` exits 0 and prints the Typer banner."""
    result = subprocess.run(
        [sys.executable, "-m", "sigantry_core", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"python -m sigantry_core --help failed with stderr:\n{result.stderr}"
    )
    # Typer banner contains the subcommands; spot-check a couple.
    assert "doctor" in result.stdout
    assert "deploy" in result.stdout
