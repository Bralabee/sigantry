"""Audit-2026-05-07 W2.1 — meta-gate: every entry-point group reference goes
through the named ``sigantry_core.registry.GROUP_*`` constants.

Falsifiability contract: this test FAILS against the pre-fix tree where
``sigantry_core/{deploy/orchestrator,dq/dispatcher,monitor/dispatcher}.py``
each defined a private ``_*_GROUP`` literal pointing at the legacy
``fabric_dataops_toolkits.<seam>`` group, while ``sigantry_core/api.py``
referenced the canonical ``sigantry.<seam>`` literals directly. Both
patterns worked through runtime aliasing in the registry, but the drift
was a v3.1 shim-drop hazard (legacy literals stop resolving when the
shim's dual-read goes away).

The gate forbids the strings:

  - ``"sigantry.deploy_profiles"`` and the ten sister canonical names
  - ``"fabric_dataops_toolkits.<any seam>"`` legacy names

from appearing as STRING LITERALS anywhere in ``sigantry_core/`` outside
``sigantry_core/registry.py`` (the single source of truth).

Doc-strings are excluded — they are allowed to mention the group names
explanatorily. The test scans only Python AST string literals, so
narrative comments / docstrings / RST references are unaffected.

Future-proofing: when a new seam is added, add the canonical group
constant to ``sigantry_core/registry.py`` first, then reference
``GROUP_<NAME>`` from the new dispatcher / facade. The gate will then
allow it automatically.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGANTRY_CORE = REPO_ROOT / "sigantry_core"
REGISTRY_PATH = SIGANTRY_CORE / "registry.py"

CANONICAL_PREFIX = "sigantry."
LEGACY_PREFIX = "fabric_dataops_toolkits."

CANONICAL_SEAMS = frozenset(
    [
        "deploy_profiles",
        "dq_gates",
        "telemetry_sinks",
        "auth_providers",
        "runbook_registries",
        "capacity_policies",
        "work_item_providers",
        "notification_sinks",
        "secret_stores",
        "approval_gates",
        "pr_review_bots",
    ]
)


def _string_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield ``(lineno, string_value)`` for every string literal in ``tree``,
    EXCLUDING strings that are sole expressions at the top of a module /
    function / class body (i.e. docstrings).
    """
    docstring_node_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_node_ids.add(id(body[0].value))

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_node_ids:
                continue
            out.append((node.lineno, node.value))
    return out


def _is_known_group_literal(value: str) -> bool:
    if value.startswith(CANONICAL_PREFIX):
        seam = value[len(CANONICAL_PREFIX) :]
        return seam in CANONICAL_SEAMS
    if value.startswith(LEGACY_PREFIX):
        seam = value[len(LEGACY_PREFIX) :]
        return seam in CANONICAL_SEAMS
    return False


def _scan(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    return [
        (lineno, value)
        for lineno, value in _string_literals(tree)
        if _is_known_group_literal(value)
    ]


def test_no_group_literals_outside_registry() -> None:
    offenders: list[str] = []
    for path in sorted(SIGANTRY_CORE.rglob("*.py")):
        if path.resolve() == REGISTRY_PATH.resolve():
            continue
        for lineno, value in _scan(path):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}:{lineno}  {value!r}")
    assert offenders == [], (
        "Entry-point group string literals found outside "
        "sigantry_core/registry.py. Use sigantry_core.registry.GROUP_* "
        "constants instead.\n  - " + "\n  - ".join(offenders)
    )


def test_registry_exposes_all_canonical_constants() -> None:
    """Lock the public surface so a future re-organisation can't drop a constant."""
    from sigantry_core import registry as r

    expected_attrs = {f"GROUP_{seam.upper()}" for seam in CANONICAL_SEAMS}
    actual = {attr for attr in dir(r) if attr.startswith("GROUP_")}
    missing = expected_attrs - actual
    assert missing == set(), f"sigantry_core.registry is missing GROUP_* constants: {missing}"

    # Sanity-check each constant resolves to its canonical literal.
    for seam in CANONICAL_SEAMS:
        constant_name = f"GROUP_{seam.upper()}"
        assert getattr(r, constant_name) == f"{CANONICAL_PREFIX}{seam}"
