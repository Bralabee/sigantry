"""Assert there is exactly ONE source of truth for the package version.

Mitigates Pitfall P1-4 (pyproject.toml and _version.py drift). Hatchling reads
_version.py; installed metadata must match; sigantry_core.__version__ must
match installed metadata.
"""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

_VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+[^"]*)"$', re.MULTILINE)


def test_fabric_dataops_version_matches_installed_metadata() -> None:
    import sigantry_core

    # importlib.metadata reads from the installed distribution (editable or wheel).
    installed = metadata.version("sigantry")
    assert sigantry_core.__version__ == installed


def test_pyproject_uses_dynamic_version(repo_root: Path) -> None:
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    project = pyproject["project"]
    # Hatchling pattern: pyproject declares dynamic=["version"], and
    # [tool.hatch.version].path points at sigantry_core/_version.py.
    assert "version" in project.get("dynamic", [])
    assert "version" not in project, (
        "Static 'version' in [project] drifts from _version.py - use dynamic only"
    )
    hatch_version = pyproject["tool"]["hatch"]["version"]
    assert hatch_version["path"] == "sigantry_core/_version.py"


def test_version_file_is_the_single_source(repo_root: Path) -> None:
    """_version.py declares the version, and the package reports that value.

    Derived, never restated. A guard that hardcodes the version it claims is
    single-sourced becomes a SECOND copy of it: it then fails on a legitimate
    release bump, which is a guard firing on a correct state rather than on a
    defect.
    """
    import sigantry_core

    version_file = (repo_root / "sigantry_core" / "_version.py").read_text()
    match = _VERSION_RE.search(version_file)
    assert match is not None, (
        "_version.py must declare __version__ as a quoted SemVer string at module scope"
    )
    declared = match.group(1)
    assert sigantry_core.__version__ == declared, (
        f"sigantry_core.__version__ ({sigantry_core.__version__!r}) does not match "
        f"_version.py ({declared!r}) - the single source is not the source"
    )
