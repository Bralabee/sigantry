"""Audit-2026-05-07 W1.7 — meta-gate: every public destructive verb is decorated.

Falsifiability contract: this test FAILS against the pre-fix tree where
``sigantry_core.governance.rbac.delete_role_assignment`` was a public
exported verb that bypassed ``@destructive_op`` entirely — no
``force=True`` requirement, no audit emission, no runbook reference.
Adding the decorator alone is not enough; without a meta-gate the same
class of finding will recur.

The gate scans every ``sigantry_core/`` module for top-level functions
whose name matches a destructive prefix (``delete_*`` / ``pause_*`` /
``remove_*`` / ``unpublish_*`` / ``drop_*`` / ``purge_*`` /
``suspend_*``) and asserts that EITHER:

1. The function carries the ``@destructive_op`` decoration directly
   (the function's ``__wrapped__`` attribute exists, set by
   ``functools.wraps``), OR
2. The function is a public wrapper that delegates to a decorated
   private helper (e.g. ``suspend_capacity`` -> ``_suspend_decorated``).
   For these the source must contain a call to a private (underscore-
   prefixed) name AND that private name must be decorated. The gate
   verifies wrapper coverage by AST.

Allowlist mechanism: explicitly-named functions can be marked
``# destructive-op-allowlist: <reason>`` on the ``def`` line; the gate
records the reason but otherwise accepts. New entries SHOULD link an
ADR.

This is a one-way ratchet — adding a new ``delete_X`` to base without
the decorator (or the wrapper-with-private-decorated-target pattern)
will turn this test red.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGANTRY_CORE = REPO_ROOT / "sigantry_core"

DESTRUCTIVE_PREFIX_PATTERN = re.compile(
    # ``rollback_`` added in the W1 security re-audit follow-up: rollback
    # supplants live workspace state with a previously-recorded item set,
    # which is destructive in the same class as ``delete_workspace``.
    r"^(delete|pause|remove|unpublish|drop|purge|suspend|rollback)_[a-z0-9_]+$"
)

ALLOWLIST_MARKER = "destructive-op-allowlist:"


class FunctionFinding(NamedTuple):
    path: Path
    function_name: str
    line: int
    decorator_names: tuple[str, ...]
    is_wrapper_to_private: bool
    private_target: str | None


def _module_files() -> list[Path]:
    return sorted(SIGANTRY_CORE.rglob("*.py"))


def _decorator_to_name(decorator: ast.expr) -> str:
    """Best-effort extraction of a decorator's printable name."""
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_to_name(decorator.func)
    return "<unknown>"


def _is_destructive_op_decoration(decorators: list[ast.expr]) -> bool:
    return any(_decorator_to_name(d) == "destructive_op" for d in decorators)


def _calls_private_helper(node: ast.FunctionDef) -> tuple[bool, str | None]:
    """Detect public-wrapper-calls-private-decorated pattern."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            target = sub.func
            target_name: str | None = None
            if isinstance(target, ast.Name):
                target_name = target.id
            elif isinstance(target, ast.Attribute):
                target_name = target.attr
            if (
                target_name
                and target_name.startswith("_")
                and ("decorated" in target_name.lower() or "guarded" in target_name.lower())
            ):
                return True, target_name
    return False, None


CLI_COMMAND_DECORATORS: frozenset[str] = frozenset({"command", "callback"})


def _scan_module(path: Path) -> list[FunctionFinding]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    findings: list[FunctionFinding] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not DESTRUCTIVE_PREFIX_PATTERN.match(node.name):
            continue
        # Allowlist marker on the def-line
        def_line = (
            src.splitlines()[node.lineno - 1] if node.lineno - 1 < len(src.splitlines()) else ""
        )
        if ALLOWLIST_MARKER in def_line:
            continue
        decorator_names = tuple(_decorator_to_name(d) for d in node.decorator_list)
        # CLI command handlers (Typer / click ``@app.command()``) are
        # operator-facing wrappers that thread ``force`` through to a
        # decorated underlying op. They are excluded from the gate so
        # the gate stays focused on the destructive-op trust boundary.
        if any(name in CLI_COMMAND_DECORATORS for name in decorator_names):
            continue
        is_wrapper, private_target = _calls_private_helper(node)
        findings.append(
            FunctionFinding(
                path=path,
                function_name=node.name,
                line=node.lineno,
                decorator_names=decorator_names,
                is_wrapper_to_private=is_wrapper,
                private_target=private_target,
            )
        )
    return findings


def _scan_module_for_decorated_private_helpers(path: Path) -> set[str]:
    """Names of `_*_decorated` private functions that ARE decorated."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return set()
    decorated: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_"):  # noqa: SIM102
            if _is_destructive_op_decoration(node.decorator_list):
                decorated.add(node.name)
    return decorated


def _all_decorated_private_helpers() -> set[str]:
    """Every `_*` function in sigantry_core/ that carries @destructive_op."""
    out: set[str] = set()
    for module in _module_files():
        out |= _scan_module_for_decorated_private_helpers(module)
    return out


def test_every_public_destructive_verb_is_decorated_or_wraps_decorated_private() -> None:
    decorated_private = _all_decorated_private_helpers()
    violations: list[str] = []
    inspected = 0
    for module in _module_files():
        for finding in _scan_module(module):
            inspected += 1
            if "destructive_op" in finding.decorator_names:
                continue
            if finding.is_wrapper_to_private:  # noqa: SIM102
                # Verify the named private target IS decorated
                if (
                    finding.private_target is not None
                    and finding.private_target in decorated_private
                ):
                    continue
            rel = finding.path.relative_to(REPO_ROOT)
            violations.append(
                f"{rel}:{finding.line}  {finding.function_name}  "
                f"decorators={finding.decorator_names}  "
                f"wraps_private={finding.is_wrapper_to_private}  "
                f"private_target={finding.private_target}"
            )
    assert inspected > 0, "AST scan found no destructive-prefixed verbs at all"
    assert violations == [], (
        "Public destructive verbs in sigantry_core/ that bypass @destructive_op:\n  - "
        + "\n  - ".join(violations)
    )


def test_known_decorated_destructive_verbs_inventory() -> None:
    """Snapshot the known-decorated destructive verbs.

    Drift here is expected when new verbs land; the assertion locks the
    *minimum* set so accidentally removing the decoration from a known
    verb fails the gate even if a code-mover updates the prefix-pattern
    above.
    """
    minimum_decorated = {
        ("workspace/core.py", "delete_workspace"),
        ("workspace/folders.py", "delete_folder"),
        ("workspace/items.py", "delete_item"),
        ("governance/rbac.py", "delete_role_assignment"),
        ("deploy/variable_library.py", "delete_variable_library"),
        ("deploy/rollback.py", "rollback_to_release"),
        ("capacity/lifecycle.py", "_suspend_decorated"),
        ("capacity/lifecycle.py", "_resume_decorated"),
    }
    seen: set[tuple[str, str]] = set()
    for module in _module_files():
        src = module.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=str(module))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and _is_destructive_op_decoration(
                node.decorator_list
            ):
                rel = module.relative_to(SIGANTRY_CORE).as_posix()
                seen.add((rel, node.name))
    missing = minimum_decorated - seen
    assert missing == set(), (
        f"These verbs MUST stay @destructive_op-decorated; the gate could not find them: {missing}"
    )
