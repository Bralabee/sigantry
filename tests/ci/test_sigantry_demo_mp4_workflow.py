"""Plan 15-02 / DEMO-03 -- .github/workflows/sigantry-demo-mp4.yml structural validation.

Wave 0 stamped four xfail stubs; Plan 15-02 ships the GHA workflow
that runs `npm ci && npm run render` to produce out/walkthrough.mp4
reproducibly (D-08); the workflow is exempt from the dual-CI parity
registry via a ci-mechanics exception annotation per CONTEXT D-05 +
RESEARCH §Pattern 5.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "sigantry-demo-mp4.yml"


def test_sigantry_demo_mp4_workflow_exists() -> None:
    """Plan 15-02 ships .github/workflows/sigantry-demo-mp4.yml."""
    assert _WORKFLOW.is_file(), _WORKFLOW
    # Sanity-check it parses as YAML.
    yaml.safe_load(_WORKFLOW.read_text())


def test_sigantry_demo_mp4_workflow_pins_node_version_via_nvmrc() -> None:
    """The actions/setup-node step uses node-version-file: scripts/remotion/.nvmrc (Pitfall 7)."""
    text = _WORKFLOW.read_text()
    assert (
        "node-version-file: scripts/remotion/.nvmrc" in text
        or "node-version-file: ./scripts/remotion/.nvmrc" in text
    ), "workflow must pin Node via node-version-file pointing at scripts/remotion/.nvmrc"


def test_sigantry_demo_mp4_workflow_uploads_artifact() -> None:
    """The workflow uploads scripts/remotion/out/walkthrough.mp4 as a CI artifact (D-08)."""
    text = _WORKFLOW.read_text()
    assert "actions/upload-artifact" in text
    assert "scripts/remotion/out/walkthrough.mp4" in text


def test_sigantry_demo_mp4_workflow_carries_ci_mechanics_exception_annotation() -> None:
    """First ~10 lines must contain the annotation so check-dual-ci-parity.py skips this workflow."""
    head = _WORKFLOW.read_text().splitlines()[:10]
    head_blob = "\n".join(head)
    assert "sigantry-dual-ci-exception: ci-mechanics" in head_blob, (
        "Annotation missing in first 10 lines; check-dual-ci-parity.py "
        "EXCEPTION_PATTERN won't find it (header scan is 20 lines, but "
        "this test enforces a tighter discipline so the annotation stays "
        "near the top)."
    )
