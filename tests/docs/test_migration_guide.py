"""Smoke test for the v1.x -> v2.0 migration guide (PROD-19).

Asserts that the guide file exists, carries every migration heading the
plan's consumer narrative walks through, references the public v2.0
surface names, and contains the migration commands consumers actually
paste into their repos.

Living under ``tests/docs/`` (not ``docs/migration/``) keeps the guide
free of negative-assertion preamble while still failing CI if the guide
drifts from its published contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_GUIDE_PATH = Path(__file__).resolve().parents[2] / "docs" / "migration" / "1.x-to-2.0.md"


@pytest.fixture(scope="module")
def guide_text() -> str:
    assert _GUIDE_PATH.is_file(), f"migration guide missing at {_GUIDE_PATH}"
    return _GUIDE_PATH.read_text(encoding="utf-8")


def test_guide_exists_and_is_non_trivial(guide_text: str) -> None:
    assert len(guide_text.splitlines()) >= 150, (
        "Migration guide should be substantial (>= 150 lines) covering all "
        "eight migration steps, troubleshooting, and rollback."
    )


@pytest.mark.parametrize(
    "heading",
    [
        "# Migration:",
        "## Summary of changes",
        "## Step 1: Update dependency declarations",
        "## Step 2: Create `.fabric-dataops.toml`",
        "## Step 3: Migrate `deploy` calls",
        "## Step 4: Migrate `run_gate` calls",
        "## Step 5: Migrate telemetry emit calls",
        "## Step 6: Update ADO pipeline template references",
        "## Step 7: Update PowerShell module pin",
        "## Step 8: Verify with `fabric-dataops-toolkits doctor`",
        "## Troubleshooting",
        "## Rollback",
    ],
)
def test_guide_has_heading(guide_text: str, heading: str) -> None:
    assert heading in guide_text, f"migration guide missing heading: {heading!r}"


@pytest.mark.parametrize(
    "reference",
    [
        "fabric-dataops-toolkits",
        "fabric-dataops-toolkits-hs2",
        "FabricDataOps.from_config",
        "DeployContext",
        ".fabric-dataops.toml",
        "hs2-secure-pipeline.yml",  # old template filename referenced for migration
        "secure-pipeline.yml",  # new template filename
        "deploy_aims",  # v1.x API named for the before/after diff
    ],
)
def test_guide_references_api_surface(guide_text: str, reference: str) -> None:
    assert reference in guide_text, f"migration guide does not mention {reference!r}"


def test_guide_cross_repo_script_names_both_consumers(guide_text: str) -> None:
    """The cross-repo migration script names both consumer repos by path."""
    assert "1_AIMS_LOCAL_2026" in guide_text
    assert "2_DATA_QUALITY_LIBRARY" in guide_text
