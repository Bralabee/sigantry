"""Plan 15-02 / DEMO-03 -- Node-side Remotion scaffold contract.

Wave 0 stamped three xfail stubs; Plan 15-02 ships
scripts/remotion/src/assetSelector.ts (D-09 fallback chain
assets/demo/ -> assets/placeholder/ via import.meta.glob) plus the
5 scene component files. This file flips xfails to byte-content
checks via Path() reads (no Node runtime required for the unit
tests; the GHA tsc + render smoke runs separately).

Local Path idiom mirrors the Plan 15-00 deviation 1 pattern (no
top-level tests/__init__.py).
"""

from __future__ import annotations

from pathlib import Path

_REMOTION_DIR = Path(__file__).resolve().parents[3] / "scripts" / "remotion"


def test_asset_selector_module_exists() -> None:
    """scripts/remotion/src/assetSelector.ts ships the per-scene asset resolver (D-09)."""
    path = _REMOTION_DIR / "src" / "assetSelector.ts"
    assert path.is_file(), path
    text = path.read_text()
    assert "import.meta.glob" in text
    assert "staticFile" in text
    assert "pickAsset" in text
    # Confirm the placeholder fallback path is wired:
    assert "assets/placeholder" in text, "assetSelector.ts must fall back to assets/placeholder/"
    assert "assets/demo" in text, "assetSelector.ts must check assets/demo/ first"


def test_walkthrough_composition_imports_5_scenes() -> None:
    """scripts/remotion/src/Walkthrough.tsx imports Scene1..Scene5 components."""
    text = (_REMOTION_DIR / "src" / "Walkthrough.tsx").read_text()
    for n in (1, 2, 3, 4, 5):
        assert f"Scene{n}" in text, f"Walkthrough.tsx missing Scene{n}"


def test_scene_components_use_pick_asset_helper() -> None:
    """Each src/scenes/Scene*.tsx delegates to assetSelector's pickAsset()."""
    scene_specs = [
        (1, "WorkItem"),
        (2, "Deploy"),
        (3, "TestGates"),
        (4, "AuditRecord"),
        (5, "Rollback"),
    ]
    for n, name in scene_specs:
        path = _REMOTION_DIR / "src" / "scenes" / f"Scene{n}{name}.tsx"
        assert path.is_file(), path
        text = path.read_text()
        assert "pickAsset" in text, f"{path.name} missing pickAsset reference"
        assert "assetSelector" in text, f"{path.name} missing assetSelector import"
