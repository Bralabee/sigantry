"""Assert the sigantry_core package tree matches the Phase 1 plan exactly.

Every submodule listed below MUST be importable and MUST have a py.typed marker
at the package root. This test is the structural contract between Phase 1 and
every downstream phase.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

EXPECTED_SUBMODULES = [
    "sigantry_core.auth",
    "sigantry_core.capacity",
    "sigantry_core.client",
    "sigantry_core.deploy",
    "sigantry_core.governance",
    "sigantry_core.monitor",
    "sigantry_core.pipelines",
    "sigantry_core.purview",
    "sigantry_core.utils",
    "sigantry_core.workspace",
]


@pytest.mark.parametrize("module_name", EXPECTED_SUBMODULES)
def test_submodule_is_importable(module_name: str) -> None:
    importlib.import_module(module_name)


def test_top_level_exports_version() -> None:
    import sigantry_core

    assert isinstance(sigantry_core.__version__, str)
    assert sigantry_core.__version__ == "1.0.0"


def test_py_typed_marker_exists(package_root: pathlib.Path) -> None:
    assert (package_root / "py.typed").is_file()


def test_cli_module_exposes_typer_app() -> None:
    import typer

    from sigantry_core.cli import app

    assert isinstance(app, typer.Typer)
