"""Phase 7 banned-API invariant tests (Plan 07-01 Task 3).

Enforces the cross-repo dependency-direction rule from CLAUDE.md:
  * AIMS + DQ depend on sigantry_core, NEVER the reverse.
  * No module-scope `import dq_framework` / `from dq_framework import ...`
    anywhere in `sigantry_core/`.
  * No module-scope `import aims_data_platform` / `from aims_data_platform
    import ...` anywhere in `sigantry_core/`.
  * Function-body late-imports (with leading whitespace) are allowed.

Also enforces the Phase 1 carry-forward banned-api invariant for the two
Phase-7-new subpackages:
  * Zero `import httpx` / `from httpx` in `sigantry_core/dq/`.
  * Zero `import httpx` / `from httpx` in `sigantry_core/deploy/profiles/`.

These tests are pure file-walk + regex checks - no imports, no mocks, no
network. They run in <100ms.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FABRIC_DATAOPS = _REPO_ROOT / "sigantry_core"


def _iter_py_files(root: Path):
    """Yield every .py file under `root` (recursive)."""
    for path in root.rglob("*.py"):
        if path.is_file():
            yield path


def _module_scope_import(pattern_name: str, path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, line) where `pattern_name` is imported at module scope.

    Module-scope = line has NO leading whitespace. Function-body imports
    (which DO have leading whitespace) are allowed by the dep-direction
    invariant.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # `^` is anchored to line start; module-scope import lines have no indent.
    pattern = re.compile(
        rf"^(?:from|import)\s+{re.escape(pattern_name)}(?:\s|\.|$)",
        flags=re.MULTILINE,
    )
    hits: list[tuple[int, str]] = []
    for match in pattern.finditer(text):
        # Compute 1-based line number from match offset.
        lineno = text.count("\n", 0, match.start()) + 1
        line = text.splitlines()[lineno - 1] if lineno - 1 < len(text.splitlines()) else ""
        hits.append((lineno, line))
    return hits


def test_no_module_scope_dq_framework_import_in_fabric_dataops() -> None:
    """CLAUDE.md dep-direction rule: dq_framework may only be late-imported (D-02)."""
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in _module_scope_import("dq_framework", path):
            offenders.append((path, lineno, line))
    assert offenders == [], (
        "Module-scope `dq_framework` imports violate the cross-repo dep-direction "
        f"invariant (CLAUDE.md): {offenders}. Use a function-body late-import "
        "inside `run_gate` (see sigantry_core.dq.gate.run_gate)."
    )


def test_no_module_scope_aims_data_platform_import_in_fabric_dataops() -> None:
    """CLAUDE.md dep-direction rule: aims_data_platform must NEVER be imported here."""
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in _module_scope_import("aims_data_platform", path):
            offenders.append((path, lineno, line))
    assert offenders == [], (
        "Module-scope `aims_data_platform` imports violate the cross-repo "
        f"dep-direction invariant (CLAUDE.md): {offenders}. AIMS depends on "
        "sigantry_core, never the reverse."
    )


def test_no_httpx_import_in_dq_subpackage() -> None:
    """Phase 1 carry-forward banned-api: only sigantry_core.client imports httpx."""
    dq_dir = _FABRIC_DATAOPS / "dq"
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(dq_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("import httpx") or stripped.startswith("from httpx"):
                offenders.append((path, lineno, line))
    assert offenders == [], (
        "Direct httpx imports are banned outside sigantry_core.client. "
        f"Offenders in sigantry_core/dq/: {offenders}."
    )


def test_no_httpx_import_in_deploy_profiles() -> None:
    """Phase 1 carry-forward banned-api: deploy.profiles must not import httpx."""
    profiles_dir = _FABRIC_DATAOPS / "deploy" / "profiles"
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(profiles_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("import httpx") or stripped.startswith("from httpx"):
                offenders.append((path, lineno, line))
    assert offenders == [], (
        "Direct httpx imports are banned outside sigantry_core.client. "
        f"Offenders in sigantry_core/deploy/profiles/: {offenders}."
    )
