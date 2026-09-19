"""Plan 15-02 / DEMO-03 / D-08 -- scripts/build-walkthrough.sh determinism wrapper.

Wave 0 stamped three xfail stubs; Plan 15-02 ships
scripts/build-walkthrough.sh which wraps `npm ci && npm run render`
with a determinism check (Node version pinned via .nvmrc; lockfile
coherence per RESEARCH §Pitfall 7).
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "build-walkthrough.sh"


def test_build_walkthrough_script_invokes_npm_ci_and_render() -> None:
    """scripts/build-walkthrough.sh contains both `npm ci` and `npm run render`."""
    text = _SCRIPT.read_text()
    assert "npm ci" in text, "build-walkthrough.sh must call `npm ci` for deterministic install"
    assert "npm run render" in text, "build-walkthrough.sh must invoke `npm run render`"
    assert "--codec h264" in text, "must pass explicit --codec h264 (Pitfall 4 size budget)"
    assert "--jpeg-quality 80" in text, (
        "must pass explicit --jpeg-quality 80 (Pitfall 4 size budget)"
    )


def test_build_walkthrough_script_executable_bit_set() -> None:
    """scripts/build-walkthrough.sh has the executable mode bit set."""
    assert _SCRIPT.is_file(), _SCRIPT
    assert os.access(_SCRIPT, os.X_OK), f"{_SCRIPT} is not executable"


def test_build_walkthrough_script_pins_node_version_via_nvmrc() -> None:
    """build-walkthrough.sh pins Node via nvm/.nvmrc/22.22.2 reference."""
    text = _SCRIPT.read_text()
    assert "nvm use" in text or ".nvmrc" in text or "22.22.2" in text, (
        "build-walkthrough.sh must reference nvm/.nvmrc/22.22.2 to pin Node version"
    )
