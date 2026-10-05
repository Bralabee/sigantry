"""Assert the sigantry_core package tree matches the Phase 1 plan exactly.

Every submodule listed below MUST be importable and MUST have a py.typed marker
at the package root. This test is the structural contract between Phase 1 and
every downstream phase.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

_VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+[^"]*)"$', re.MULTILINE)

EXPECTED_SUBMODULES = [
    "sigantry_core.auth",
    "sigantry_core.capacity",
    "sigantry_core.client",
    "sigantry_core.deploy",
    "sigantry_core.governance",
    "sigantry_core.monitor",
    "sigantry_core.pipelines",
    "sigantry_core.preflight",
    "sigantry_core.purview",
    "sigantry_core.utils",
    "sigantry_core.workspace",
]


@pytest.mark.parametrize("module_name", EXPECTED_SUBMODULES)
def test_submodule_is_importable(module_name: str) -> None:
    importlib.import_module(module_name)


def test_top_level_exports_version(package_root: pathlib.Path) -> None:
    """The package exports the version ``_version.py`` declares.

    Read from the file, never restated: a literal here is a second copy of
    the version, which goes red on a correct release bump.
    """
    import sigantry_core

    match = _VERSION_RE.search((package_root / "_version.py").read_text(encoding="utf-8"))
    assert match is not None, "_version.py must declare __version__ as a quoted SemVer string"
    assert isinstance(sigantry_core.__version__, str)
    assert sigantry_core.__version__ == match.group(1)


def test_py_typed_marker_exists(package_root: pathlib.Path) -> None:
    assert (package_root / "py.typed").is_file()


def test_cli_module_exposes_typer_app() -> None:
    import typer

    from sigantry_core.cli import app

    assert isinstance(app, typer.Typer)
