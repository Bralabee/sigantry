"""Audit-2026-05-07 W4.2 -- falsifiability tests for the
``apply_sync`` decomposition.

Pre-W4.2 the body of ``apply_sync`` was 415 LOC (lines 306-721 of
``sigantry_core/sync/apply.py``). All five phases of the workflow --
manifest load, git-sync precheck, item packaging, reconciliation,
publish + emit -- coexisted in a single function with shared local
state. Hard to read, hard to test in isolation, hard to extend
without re-introducing inter-phase coupling.

W4.2 extracted five phase helpers:

- ``_pack_phase`` -- the packager loop.
- ``_snapshot_for_publish_phase`` -- pre-reconcile snapshot + parameters
  substitution for ``with_publish=True``.
- ``_reconcile_phase`` -- thin wrapper around ``reconcile_fn`` with
  the typed-exception conversion.
- ``_emit_combined_publish_record_phase`` -- absent/moved-items
  computation + publish + combined DeployRecord.
- ``_emit_default_record_phase`` -- v3.0 default-path DeployRecord.
- ``_emit_failure_record_phase`` -- W6-guarded failure-record emission.

The orchestrator's executable body shrank from 415 LOC to ~170 LOC
(-59%); the remaining lines are argument validation, client setup,
the try/except/finally control flow, and the phase calls themselves.

Tests below pin:

- All six phase helpers exist as top-level functions on the module.
- Each phase helper has a short, focused docstring (not a
  copy-paste of the apply_sync docstring).
- The orchestrator's body shrunk substantially.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from sigantry_core.sync import apply as apply_mod
from sigantry_core.sync.apply import apply_sync

REPO_ROOT = Path(__file__).resolve().parents[3]
APPLY_PATH = REPO_ROOT / "sigantry_core" / "sync" / "apply.py"


@pytest.mark.parametrize(
    "phase_name",
    [
        "_pack_phase",
        "_snapshot_for_publish_phase",
        "_reconcile_phase",
        "_emit_combined_publish_record_phase",
        "_emit_default_record_phase",
        "_emit_failure_record_phase",
    ],
)
def test_phase_helper_exists(phase_name: str) -> None:
    """Every documented phase helper is a top-level function on apply.py."""
    assert hasattr(apply_mod, phase_name), (
        f"sigantry_core.sync.apply has no phase helper named {phase_name!r}. "
        "The W4.2 decomposition required all six helpers."
    )
    fn = getattr(apply_mod, phase_name)
    assert callable(fn), f"{phase_name!r} is not callable."
    assert fn.__doc__ is not None and "W4.2" in fn.__doc__, (
        f"{phase_name!r} docstring does not record its W4.2 lineage; "
        "future readers will not know it was extracted as part of the "
        "audit-2026-05-07 decomposition."
    )


def test_apply_sync_orchestrator_calls_each_phase_at_least_once() -> None:
    """The orchestrator delegates to every phase helper.

    AST-walk: the body of ``apply_sync`` must contain at least one
    call to each of the six phase helpers. Falsifiability: a future
    refactor that re-inlines a phase trips this gate.
    """
    src = inspect.getsource(apply_sync)
    tree = ast.parse(src)
    fn = tree.body[0]
    called_names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    expected = {
        "_pack_phase",
        "_snapshot_for_publish_phase",
        "_reconcile_phase",
        "_emit_combined_publish_record_phase",
        "_emit_default_record_phase",
        "_emit_failure_record_phase",
    }
    missing = expected - called_names
    assert missing == set(), (
        f"apply_sync no longer calls phase helpers: {sorted(missing)}. "
        "The W4.2 decomposition relies on the orchestrator delegating; "
        "re-inlining a phase makes the orchestrator harder to read again."
    )


def test_apply_sync_body_is_below_decomposition_target() -> None:
    """The orchestrator's executable body (post-docstring) is < 200 LOC.

    Pre-W4.2: 415 LOC. Post-W4.2 target: ~80 LOC of pure orchestration
    (audit synthesis prediction). Achieved: ~170 LOC. The remaining
    delta is bookkeeping (argument coercion + client/tempdir setup +
    try/except/finally control flow that cannot move into a phase
    helper without losing the staging-tempdir lifecycle invariant).
    A regression beyond 200 LOC means a phase was re-inlined or new
    cross-phase logic crept in.
    """
    src = inspect.getsource(apply_sync)
    tree = ast.parse(src)
    fn = tree.body[0]
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    total_stmt_lines = sum((n.end_lineno - n.lineno + 1) for n in body)
    assert total_stmt_lines < 200, (
        f"apply_sync orchestration body is {total_stmt_lines} lines; "
        "W4.2 expected < 200 (down from 415 pre-fix). Either a phase "
        "was re-inlined or new orchestration logic was added."
    )


def test_phase_helpers_have_distinct_docstrings() -> None:
    """No phase helper copy-pastes another's docstring.

    Spot-check: a future refactor that copies a phase helper to a
    sibling without updating its docstring would leak through if
    docstrings are identical.
    """
    phase_names = [
        "_pack_phase",
        "_snapshot_for_publish_phase",
        "_reconcile_phase",
        "_emit_combined_publish_record_phase",
        "_emit_default_record_phase",
        "_emit_failure_record_phase",
    ]
    docs = {name: getattr(apply_mod, name).__doc__ for name in phase_names}
    # First lines must be unique.
    first_lines = [d.split("\n")[0].strip() for d in docs.values()]
    assert len(set(first_lines)) == len(first_lines), (
        "Phase helper docstring first lines collide -- a refactor "
        "may have copy-pasted without updating the docstring.\n"
        + "\n".join(f"  {n}: {fl!r}" for n, fl in zip(phase_names, first_lines, strict=True))
    )
