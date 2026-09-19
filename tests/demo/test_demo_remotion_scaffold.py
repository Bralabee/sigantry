"""Plan 15-02 / DEMO-03 -- Remotion subproject scaffold under scripts/remotion/.

Wave 0 (Plan 15-00) stamped seven xfail stubs; Plan 15-02 ships
package.json (pinning Remotion 4.0.451 per /remotion-tutorial/),
tsconfig.json, src/Root.tsx, src/Walkthrough.tsx (5 scenes),
src/assetSelector.ts, src/scenes/, and script.md. Plan 15-02 flipped
each xfail to a real Path()-based assertion (no Node runtime required
for the unit tests; the GHA mp4-build workflow runs the tsc + render
smoke separately).

Local Path idiom mirrors tests/starter/test_starter_content.py:20 and
the Plan 15-00 deviation 1 carryover (no top-level tests/__init__.py).
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_REMOTION_DIR = _REPO / "scripts" / "remotion"


def test_remotion_package_json_exists() -> None:
    """scripts/remotion/package.json exists and declares the remotion dep."""
    path = _REMOTION_DIR / "package.json"
    assert path.is_file(), path
    data = json.loads(path.read_text())
    assert "remotion" in data.get("dependencies", {}), data.get("dependencies")
    assert data.get("name") == "sigantry-walkthrough", data.get("name")
    assert "render" in data.get("scripts", {}), data.get("scripts")
    assert "lint" in data.get("scripts", {}), data.get("scripts")


def test_remotion_package_json_pins_remotion_4_0_451() -> None:
    """package.json pins remotion@4.0.451 exactly (no caret) per RESEARCH §Pitfall 7."""
    data = json.loads((_REMOTION_DIR / "package.json").read_text())
    assert data["dependencies"]["remotion"] == "4.0.451", data["dependencies"]["remotion"]
    assert data["dependencies"]["@remotion/cli"] == "4.0.451", data["dependencies"]["@remotion/cli"]
    # And the lockfile is committed for deterministic CI runs.
    assert (_REMOTION_DIR / "package-lock.json").is_file()


def test_remotion_tsconfig_exists() -> None:
    """scripts/remotion/tsconfig.json exists with strict TS settings."""
    path = _REMOTION_DIR / "tsconfig.json"
    assert path.is_file(), path
    data = json.loads(path.read_text())
    opts = data.get("compilerOptions", {})
    assert opts.get("strict") is True, opts
    assert opts.get("jsx") == "react-jsx", opts.get("jsx")


def test_remotion_root_tsx_registers_walkthrough_composition() -> None:
    """src/Root.tsx registers a <Composition id="Walkthrough" .../>."""
    text = (_REMOTION_DIR / "src" / "Root.tsx").read_text()
    assert 'id="Walkthrough"' in text or "id='Walkthrough'" in text, (
        "Root.tsx must register a Composition with id 'Walkthrough'"
    )
    assert "Composition" in text


def test_remotion_walkthrough_tsx_has_5_scenes() -> None:
    """src/Walkthrough.tsx imports Scene1..Scene5 components."""
    text = (_REMOTION_DIR / "src" / "Walkthrough.tsx").read_text()
    for n in (1, 2, 3, 4, 5):
        assert f"Scene{n}" in text, f"Walkthrough.tsx missing reference to Scene{n}"


def test_remotion_asset_selector_module_exists() -> None:
    """src/assetSelector.ts uses import.meta.glob + staticFile per Pattern 3."""
    text = (_REMOTION_DIR / "src" / "assetSelector.ts").read_text()
    assert "import.meta.glob" in text
    assert "staticFile" in text
    assert "pickAsset" in text


def test_remotion_script_md_exists() -> None:
    """scripts/remotion/script.md is the source-of-truth for walkthrough text (D-10)."""
    path = _REMOTION_DIR / "script.md"
    assert path.is_file(), path
