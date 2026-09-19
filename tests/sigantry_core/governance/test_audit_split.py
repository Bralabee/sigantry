"""Audit-2026-05-07 W2.6 -- falsifiability tests for the
``sigantry_core.governance.audit`` god-module split.

The pre-W2.6 module held three concerns in 378 LOC: the
``destructive_op`` decorator + ``DestructiveOpError`` exception
(116 LOC), the file-I/O helpers (15 LOC), and the three near-identical
``emit_*_record`` functions (~200 LOC of duplicated body). W2.6 carves
the decorator into ``governance/destructive.py`` and the file-I/O
helpers into ``governance/audit_io.py``, leaving ``governance/audit.py``
as the public re-export surface + the three thin emit_* functions
that delegate the file write to a shared helper.

Tests below pin:

- The split files exist and own the documented symbols.
- ``governance/audit.py`` continues to re-export the public surface
  (DestructiveOpError, destructive_op, emit_*_record, _audit_file_opener,
  _DEFAULT_AUDIT_DIR, _AUDIT_FILE_MODE) -- 30+ existing imports must
  keep working without code changes.
- The structured-logger NAME is unchanged (``sigantry_core.governance.audit``)
  so JSONL log consumers / journald filters continue to match.
- The three ``emit_*_record`` bodies are now thin (W2.6 dedupe);
  ``test_emit_bodies_are_thin`` fails the moment a future change
  re-inlines the file-I/O bookkeeping.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


# --- Module structure --------------------------------------------------------


def test_destructive_module_exists_with_documented_symbols() -> None:
    """``governance/destructive.py`` owns DestructiveOpError + destructive_op."""
    mod = importlib.import_module("sigantry_core.governance.destructive")
    for name in ("DestructiveOpError", "destructive_op"):
        assert hasattr(mod, name), f"sigantry_core.governance.destructive missing {name!r}"
    # ``logger`` is exposed for back-compat re-export from audit.py.
    assert hasattr(mod, "logger")
    assert mod.logger.name == "sigantry_core.governance.audit", (
        "Logger name moved across the split. JSONL log consumers / "
        "journald filters keyed on the old name will silently miss events."
    )


def test_audit_io_module_exists_with_documented_symbols() -> None:
    """``governance/audit_io.py`` owns the file-I/O helpers + write_audit_record."""
    mod = importlib.import_module("sigantry_core.governance.audit_io")
    for name in (
        "_audit_file_opener",
        "_DEFAULT_AUDIT_DIR",
        "_AUDIT_FILE_MODE",
        "write_audit_record",
    ):
        assert hasattr(mod, name), f"sigantry_core.governance.audit_io missing {name!r}"
    assert mod._AUDIT_FILE_MODE == 0o600, "File mode must remain 0o600 (MD-01)."


# --- Backwards-compat surface ------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "DestructiveOpError",
        "destructive_op",
        "emit_deploy_record",
        "emit_approval_record",
        "emit_secret_change_record",
        "logger",
    ],
)
def test_audit_module_preserves_public_surface(name: str) -> None:
    """Every PUBLIC name imported by callers across the toolkit + tests still resolves.

    Audit-2026-05-08 review follow-up (IN-02): the W2.6 split previously
    re-exported three private-prefixed file-I/O primitives
    (``_AUDIT_FILE_MODE``, ``_DEFAULT_AUDIT_DIR``,
    ``_audit_file_opener``) from this module. Listing
    underscore-prefixed names in a public-surface gate sent a mixed
    signal -- ``__all__`` documents *public* surface, and the
    underscore prefix signals private. Consumers needing those
    primitives now import from :mod:`sigantry_core.governance.audit_io`
    directly (``release/ledger.py`` was the only in-tree consumer; it
    now imports from audit_io). The gate's parametrize list is the
    public-only surface.
    """
    mod = importlib.import_module("sigantry_core.governance.audit")
    assert hasattr(mod, name), (
        f"sigantry_core.governance.audit no longer exposes {name!r}; "
        "the W2.6 split must preserve every name in the public surface."
    )


def test_destructive_op_alias_is_canonical_object() -> None:
    """The re-exported ``destructive_op`` is the same callable as in the carved-out module.

    Note: ``sigantry_core.governance`` re-exports a ``rbac.audit``
    function under the name ``audit`` which would shadow the submodule
    if we imported as ``from sigantry_core.governance import audit``;
    we use ``importlib.import_module`` to address the SUBMODULE rather
    than the rebound name.
    """
    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    destructive_mod = importlib.import_module("sigantry_core.governance.destructive")

    assert audit_mod.destructive_op is destructive_mod.destructive_op
    assert audit_mod.DestructiveOpError is destructive_mod.DestructiveOpError


def test_audit_io_canonical_module_owns_file_io_primitives() -> None:
    """The file-I/O primitives live in ``audit_io`` and are reachable directly.

    Audit-2026-05-08 review follow-up (IN-02): pre-fix the audit module
    re-exported these private-prefixed names; now consumers reach them
    via the canonical owner module (``audit_io``). This test pins the
    canonical home so a future refactor that drops them entirely from
    ``audit_io`` would fail the gate.
    """
    audit_io_mod = importlib.import_module("sigantry_core.governance.audit_io")
    assert callable(audit_io_mod._audit_file_opener)
    assert audit_io_mod._DEFAULT_AUDIT_DIR is not None
    assert audit_io_mod._AUDIT_FILE_MODE == 0o600


# --- Dedupe contract ---------------------------------------------------------


def _function_loc(path: Path, name: str) -> int:
    """Return the body-line count of the named module-scope function."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.end_lineno - node.lineno  # type: ignore[operator]
    raise AssertionError(f"Function {name!r} not found in {path}")


@pytest.mark.parametrize(
    "fn_name",
    ["emit_deploy_record", "emit_approval_record", "emit_secret_change_record"],
)
def test_emit_bodies_are_thin(fn_name: str) -> None:
    """Each ``emit_*_record`` function is now a thin wrapper.

    Pre-W2.6 each body inlined ~30 LOC of file-I/O bookkeeping
    (`mkdir(0o700)`, JSON canonicalise, `open(opener=...)`, fsync).
    Post-W2.6 each body delegates to ``write_audit_record`` and is
    well under 80 lines INCLUDING the docstring. This test fails the
    moment a future change re-inlines the duplication.
    """
    path = REPO_ROOT / "sigantry_core" / "governance" / "audit.py"
    loc = _function_loc(path, fn_name)
    assert loc < 80, (
        f"{fn_name} grew to {loc} body lines. The W2.6 dedupe expected "
        "every emit_*_record body to stay thin (delegate the file write "
        "to write_audit_record). Did the file-I/O bookkeeping creep "
        "back inline?"
    )


def test_audit_module_locked_to_target_size() -> None:
    """The audit.py file must be substantially smaller than the pre-W2.6 baseline.

    Pre-W2.6: 378 LOC monolithic. Post-W2.6: 209 LOC. Audit-2026-05-07
    W3.1 added ``emit_destructive_op_record`` (the Wave 1 re-audit
    follow-up parity writer); current target: < 280 LOC, leaves
    headroom for one more thin emit function. A regression beyond 280
    means the dedupe regressed or a new concern crept inline.
    """
    path = REPO_ROOT / "sigantry_core" / "governance" / "audit.py"
    loc = path.read_text(encoding="utf-8").count("\n") + 1
    assert loc < 280, (
        f"sigantry_core/governance/audit.py is {loc} lines; W2.6 + W3.1 "
        "expected < 280. Either the dedupe regressed or a new concern "
        "was added inline that should live in destructive.py / audit_io.py."
    )
