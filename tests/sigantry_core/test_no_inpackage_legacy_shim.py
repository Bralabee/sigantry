"""Audit-2026-05-07 W2.7 -- falsifiability tests for the dropped
in-package legacy ``fabric_dataops_toolkits`` meta-path finder.

The pre-W2.7 ``sigantry_core/__init__.py`` installed a
``_LegacyShimFinder`` on ``sys.meta_path`` that re-resolved
``fabric_dataops_toolkits.X`` imports to ``sigantry_core.X``. This
duplicated the dist-level shim wheel at ``shim/fabric-dataops-toolkits/``
which already provides the same redirect with proper
``DeprecationWarning`` emission. W2.7 drops the in-package finder; the
dist shim becomes the sole legacy path.

Tests below pin:

- ``sigantry_core/__init__.py`` does not define ``_install_legacy_shim``
  or ``_LegacyShimFinder``.
- No ``MetaPathFinder`` named ``_LegacyShimFinder`` is installed on
  ``sys.meta_path`` after importing ``sigantry_core``.
- The dist shim subprocess tests in
  ``tests/shim/test_python_shim_reexport.py`` keep passing -- the
  legacy import path still works, just via the wheel rather than the
  in-package finder.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PATH = REPO_ROOT / "sigantry_core" / "__init__.py"


def test_inpackage_legacy_shim_function_is_dropped() -> None:
    """``_install_legacy_shim`` no longer exists in sigantry_core/__init__.py."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "_install_legacy_shim" not in function_names, (
        "W2.7 dropped the in-package _install_legacy_shim function. "
        "Re-introducing it duplicates the dist shim wheel and bypasses "
        "the DeprecationWarning the dist shim emits."
    )


def test_inpackage_legacy_shim_finder_class_is_dropped() -> None:
    """``_LegacyShimFinder`` is not defined anywhere in sigantry_core/__init__.py."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "_LegacyShimFinder" not in class_names
    assert "_AliasLoader" not in class_names


def test_no_legacy_shim_finder_on_meta_path() -> None:
    """No ``_LegacyShimFinder`` instance lives on ``sys.meta_path`` post-import.

    Importing ``sigantry_core`` (already done by pytest collection)
    must not install any meta-path finder named ``_LegacyShimFinder``
    -- that would mean the W2.7 drop regressed.
    """
    finder_names = [type(f).__name__ for f in sys.meta_path]
    assert "_LegacyShimFinder" not in finder_names, (
        "_LegacyShimFinder was reinstalled on sys.meta_path. The W2.7 "
        f"drop has regressed. Current finders: {finder_names}"
    )


def test_init_module_remains_a_thin_reexport_ladder() -> None:
    """``sigantry_core/__init__.py`` carries no module-load code beyond imports.

    Pre-W2.7: 168 LOC, including a 90-LOC legacy-shim machine
    (function + 2 classes + a ``_install_legacy_shim()`` call at
    module body). Post-W2.7: docstring + imports + ``__all__`` only.

    AST-based contract (more robust than a raw LOC ratchet, which
    Wave 2 re-audit Tests-F3 flagged as 1-line-headroom fragile):

    - No ``ast.FunctionDef`` / ``ast.AsyncFunctionDef`` at module scope.
    - No ``ast.ClassDef`` at module scope.
    - Only ``ast.Import`` / ``ast.ImportFrom`` / ``ast.Assign`` (the
      ``__all__`` list) / ``ast.Expr`` (the docstring) at module body.

    A complementary loose LOC ratchet (< 100) catches the shape of
    "someone shoved 50 lines of inline machinery in" while the AST
    check catches the precise "no module-load code" intent.
    """
    src = INIT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # AST shape check: every top-level node is one of the allowed kinds.
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr)
    offenders: list[str] = []
    for node in tree.body:
        if not isinstance(node, allowed):
            offenders.append(f"line {node.lineno}: {type(node).__name__}")
    assert offenders == [], (
        "sigantry_core/__init__.py has module-load code beyond the "
        "imports + docstring + __all__ ladder. Move new code to a "
        "submodule; __init__.py stays a thin re-export.\n  - " + "\n  - ".join(offenders)
    )

    # Loose LOC ratchet -- shape signal, not the primary contract.
    # Pre-W2.7 baseline: 168 LOC (~90 LOC of legacy-shim machinery).
    # Post-W2.7 + Wave-2 re-audit fix-up (F1: add Phase 11/14/16 seams
    # to ``__all__``): ~110 LOC. Threshold leaves headroom for new
    # Protocol re-exports without becoming a module-load-code escape
    # hatch -- the AST-shape check above is the load-bearing contract.
    loc = src.count("\n") + 1
    assert loc < 130, (
        f"sigantry_core/__init__.py is {loc} lines; W2.7 expects "
        "< 130. The AST-shape check above pins the structural intent; "
        "this LOC ratchet is the secondary signal."
    )
