"""Byte-extension invariant (CONTEXT D-02 / RESEARCH §Pattern 1).

Plan 15-01 ships templates/demo/ content; this file flips the Wave 0
xfails to real assertions over the byte-extension invariant: every
file in templates/starter/ appears byte-equal in templates/demo/
EXCEPT for the DEMO_DIVERGENCE_ALLOWED set (README.md, parameters.yml,
docs/QUICKSTART.md). The demo tree may add files (under fabric_items/
and the new sync.yml) but may not omit any starter file.

Local-import idiom matches tests/starter/test_starter_content.py:20
(no top-level tests/__init__.py -- see tests/demo/conftest.py docstring).
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_STARTER_DIR = _REPO / "templates" / "starter"
_DEMO_DIR = _REPO / "templates" / "demo"

# Mirrored from tests/demo/conftest.py DEMO_DIVERGENCE_ALLOWED so this
# file can be read in isolation. Keep in sync with conftest.
_DEMO_DIVERGENCE_ALLOWED = frozenset(
    {
        "README.md",
        "parameters.yml",
        "docs/QUICKSTART.md",
    }
)

# Files that may appear in templates/demo/ without a starter counterpart.
# Plan 15-01 demo-only additions: sync.yml at top level + fabric_items/.
# Plan 15-03 demo-only additions: the demo CI workflow YAML pair --
# .github/workflows/sigantry-demo-ci.yml (GHA half) + .azuredevops/
# sigantry-demo-ci.yml (ADO half) -- which have no starter counterpart
# because the starter only ships .github/workflows/pr-bot.yml +
# .azuredevops/jobs/pr-bot.yml. The demo CI pair is the entire
# DEMO-02 CI surface (CONTEXT D-04 + D-05); see tests/ci/
# test_demo_dual_ci.py for parity coverage.
_DEMO_ONLY_TOP_LEVEL = frozenset(
    {
        "sync.yml",
        ".github/workflows/sigantry-demo-ci.yml",
        ".azuredevops/sigantry-demo-ci.yml",
    }
)
_DEMO_ONLY_TOP_DIRS = frozenset({"fabric_items"})


def _iter_starter_files() -> list[Path]:
    return [p for p in _STARTER_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]


def _iter_demo_files() -> list[Path]:
    return [p for p in _DEMO_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]


def test_every_starter_file_byte_equal_in_demo() -> None:
    """Every starter file appears byte-equal in demo modulo DEMO_DIVERGENCE_ALLOWED."""
    for src in _iter_starter_files():
        rel = src.relative_to(_STARTER_DIR)
        if str(rel) in _DEMO_DIVERGENCE_ALLOWED:
            continue
        dst = _DEMO_DIR / rel
        assert dst.is_file(), f"demo missing {rel} (must extend starter)"
        assert src.read_bytes() == dst.read_bytes(), f"{rel}: byte drift between starter and demo"


def test_demo_has_no_files_outside_starter_skeleton_apart_from_fabric_items() -> None:
    """Every demo file either has a starter counterpart, lives under fabric_items/, or is sync.yml."""
    starter_rel = {p.relative_to(_STARTER_DIR) for p in _iter_starter_files()}
    for dst in _iter_demo_files():
        rel = dst.relative_to(_DEMO_DIR)
        if rel in starter_rel:
            continue
        # Demo-only additions: fabric_items/** + sync.yml at top level.
        if rel.parts and rel.parts[0] in _DEMO_ONLY_TOP_DIRS:
            continue
        if str(rel) in _DEMO_ONLY_TOP_LEVEL:
            continue
        raise AssertionError(f"demo carries unexpected file with no starter counterpart: {rel}")


def test_divergent_files_actually_differ() -> None:
    """README.md / parameters.yml / docs/QUICKSTART.md must NOT be byte-equal -- otherwise demo is just a copy."""
    for rel_str in _DEMO_DIVERGENCE_ALLOWED:
        rel = Path(rel_str)
        src = _STARTER_DIR / rel
        dst = _DEMO_DIR / rel
        assert src.is_file(), f"starter missing divergence-allowed file: {rel}"
        assert dst.is_file(), f"demo missing divergence-allowed file: {rel}"
        assert src.read_bytes() != dst.read_bytes(), (
            f"{rel}: starter and demo content is byte-equal -- "
            f"divergence required for demo to be substantive"
        )
