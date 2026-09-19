"""Smoke test for the v2.x -> v3.0 migration guide (BRIEF-05).

Locks the structural invariants of ``docs/migration/2.x-to-3.0.md`` so
future PRs cannot silently delete one of the six migration-axis sections,
weaken the search-and-replace recipes, or accidentally re-introduce a
live ``sigantry.dev`` link before domain clearance lands.

The guide itself is authored in Phase 10 Plan 07 (BRIEF-05). Every
runtime DeprecationWarning emitted by the v3.0 deprecation shims (Plan
10-05) routes consumers here.

Living under ``tests/docs/`` (not ``docs/migration/``) keeps the guide
free of negative-assertion preamble while still failing CI if the guide
drifts from its published contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_GUIDE_PATH = Path(__file__).resolve().parents[2] / "docs" / "migration" / "2.x-to-3.0.md"


@pytest.fixture(scope="module")
def guide_text() -> str:
    assert _GUIDE_PATH.is_file(), f"migration guide missing at {_GUIDE_PATH}"
    return _GUIDE_PATH.read_text(encoding="utf-8")


def test_migration_guide_exists() -> None:
    """The v2.x -> v3.0 migration guide ships at the canonical path the
    deprecation shims point at."""
    assert _GUIDE_PATH.is_file(), f"migration guide missing at {_GUIDE_PATH}"


def test_migration_guide_has_six_section_headings(guide_text: str) -> None:
    """All six migration axes from ADR-0011 are present as level-2
    headings, in the order ADR-0011 enumerates them."""
    expected_in_order = [
        "## Migration axis 1 -- Python imports",
        "## Migration axis 2 -- Config file",
        "## Migration axis 3 -- Environment variables",
        "## Migration axis 4 -- Entry-point groups",
        "## Migration axis 5 -- PowerShell modules",
        "## Migration axis 6 -- CLI",
    ]

    last_index = -1
    for heading in expected_in_order:
        assert heading in guide_text, (
            f"migration guide missing heading: {heading!r} -- ADR-0011 "
            f"enumerates six migration axes; the docs smoke test asserts "
            f"each one is documented as its own level-2 section."
        )
        index = guide_text.index(heading)
        assert index > last_index, (
            f"migration guide heading {heading!r} appears out of order "
            f"relative to ADR-0011's enumeration."
        )
        last_index = index


def test_migration_guide_has_tldr_block(guide_text: str) -> None:
    """The guide opens with a TL;DR block so consumers can see the full
    six-step migration recipe in under a screen-height."""
    assert "## TL;DR" in guide_text, (
        "migration guide missing '## TL;DR' section -- the TL;DR block is the "
        "first thing a consumer sees after landing from a deprecation warning."
    )


def test_migration_guide_has_runnable_sed_recipes(guide_text: str) -> None:
    """Both sed substitutions are present (plugin-first, then base) so a
    consumer who copy-pastes the recipes does not corrupt their repo via
    the substring-superset hazard documented in ADR-0011."""
    plugin_first = "sed -i 's/fabric_dataops_toolkits_hs2/sigantry_hs2/g'"
    base_second = "sed -i 's/fabric_dataops_toolkits/sigantry_core/g'"
    assert plugin_first in guide_text, (
        "migration guide missing plugin-first sed recipe "
        f"({plugin_first!r}). The fabric_dataops_toolkits_hs2 substring is "
        "a superset of the base substring; the plugin pattern MUST run "
        "first or the second pass corrupts already-renamed identifiers."
    )
    assert base_second in guide_text, f"migration guide missing base sed recipe ({base_second!r})."

    plugin_pos = guide_text.index(plugin_first)
    base_pos = guide_text.index(base_second)
    assert plugin_pos < base_pos, (
        "migration guide sed recipes are in the wrong order -- the plugin "
        "sed (superset substring fabric_dataops_toolkits_hs2) must appear "
        "BEFORE the base sed or the second pass partially rewrites "
        "already-renamed identifiers to 'sigantry_core_hs2'."
    )


def test_migration_guide_references_adr_0011_and_0010(guide_text: str) -> None:
    """The guide cross-references both v3.0 ADRs (the rename plan and
    the commercial-model decision) so consumers can navigate to the
    decision records from the migration guide directly."""
    assert "ADR-0011" in guide_text, (
        "migration guide must reference ADR-0011 (the rename-cutover decision)."
    )
    assert "ADR-0010" in guide_text, (
        "migration guide must reference ADR-0010 (Apache-2.0 commercial model)."
    )
    assert "ADR-0010-commercial-model.md" in guide_text, (
        "migration guide must link to ADR-0010 by filename."
    )
    assert "ADR-0011-rename-to-sigantry.md" in guide_text, (
        "migration guide must link to ADR-0011 by filename."
    )


def test_migration_guide_links_to_product_brief(guide_text: str) -> None:
    """Consumers reading the migration guide should be one click away
    from the product brief that explains the rename's business motivation."""
    assert "PRODUCT-BRIEF.md" in guide_text, (
        "migration guide must link to PRODUCT-BRIEF.md for the productisation "
        "rationale behind the rename."
    )


def test_migration_guide_does_not_link_sigantry_dev(guide_text: str) -> None:
    """sigantry.dev domain clearance is deferred (per 10-CONTEXT.md and
    ADR-0011 V3-RISK-1). The migration guide must not advertise the
    domain as a live URL until clearance lands -- otherwise we ship a
    dead link to every v2.x consumer who upgrades."""
    forbidden_patterns = [
        "https://sigantry.dev",
        "http://sigantry.dev",
        "(sigantry.dev)",
        "://sigantry.dev",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in guide_text, (
            f"migration guide must NOT contain a live sigantry.dev link "
            f"({pattern!r}). Domain clearance is still deferred per ADR-0011 "
            f"V3-RISK-1; a live link strands every v2.x consumer who upgrades."
        )


def test_migration_guide_references_canonical_repo(guide_text: str) -> None:
    """ADR-0011 records the GitHub canonical-home decision. The migration
    guide must surface the canonical issue-filing URL so consumers landing
    here from a DeprecationWarning know where to file regressions."""
    assert "https://github.com/Bralabee/fabric_dataops" in guide_text, (
        "migration guide must reference the canonical GitHub repo "
        "(https://github.com/Bralabee/fabric_dataops) so consumers can file "
        "issues. ADR-0011 records GitHub as the canonical home for v3.0+."
    )
