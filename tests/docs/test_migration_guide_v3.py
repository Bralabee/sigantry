"""Smoke test for the legacy-names note at ``docs/migration/2.x-to-3.0.md``.

The registry's DeprecationWarning for a plugin registered under a legacy
entry-point group points readers at this path
(``sigantry_core.registry._LEGACY_WARNING_TEMPLATE``), so the note must exist
there and must describe every legacy name the code still reads.

Living under ``tests/docs/`` (not ``docs/migration/``) keeps the note free of
negative-assertion preamble while still failing CI if it drifts from the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sigantry_core import registry

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUIDE_REL = "docs/migration/2.x-to-3.0.md"
_GUIDE_PATH = _REPO_ROOT / _GUIDE_REL


@pytest.fixture(scope="module")
def guide_text() -> str:
    assert _GUIDE_PATH.is_file(), f"migration note missing at {_GUIDE_PATH}"
    return _GUIDE_PATH.read_text(encoding="utf-8")


def test_registry_warning_points_at_this_note() -> None:
    """The legacy-group DeprecationWarning names this file, and the file exists."""
    assert _GUIDE_REL in registry._LEGACY_WARNING_TEMPLATE
    assert _GUIDE_PATH.is_file(), f"migration note missing at {_GUIDE_PATH}"


def test_note_has_one_section_per_legacy_surface(guide_text: str) -> None:
    """Each legacy surface the code still handles has its own level-2 section."""
    expected_in_order = [
        "## TL;DR",
        "## Python imports",
        "## Config file",
        "## Environment variables",
        "## Entry-point groups",
        "## CLI",
    ]
    last_index = -1
    for heading in expected_in_order:
        assert heading in guide_text, f"migration note missing heading: {heading!r}"
        index = guide_text.index(heading)
        assert index > last_index, f"migration note heading {heading!r} is out of order"
        last_index = index


@pytest.mark.parametrize(
    "token",
    [
        ".sigantry.toml",
        ".fabric-dataops.toml",
        "SIGANTRY_<SECTION>__<KEY>",
        "FDT_",
        "DeprecationWarning",
        r"'s/\bfabric_dataops_toolkits\b/sigantry_core/g'",
    ],
)
def test_note_names_old_and_new_forms(guide_text: str, token: str) -> None:
    assert token in guide_text, f"migration note does not mention {token!r}"


def test_note_names_every_legacy_group_the_registry_reads(guide_text: str) -> None:
    """Derived from the registry, so a group added or dropped there fails here."""
    groups = set(registry.Registry.known_legacy_groups())
    assert groups, "registry reports no legacy groups; the probe would be vacuous"
    named = set(re.findall(r"\bfabric_dataops_toolkits\.[a-z_]+\b", guide_text))
    missing = sorted(groups - named)
    assert not missing, f"legacy groups read but not documented: {missing}"
    extra = sorted(named - groups)
    assert not extra, f"legacy groups documented but no longer read: {extra}"


_PIP_INSTALL = re.compile(r"\bpip3? install\b(?P<args>[^\n`]*)")
_SPEC_END = re.compile(r"[<>=!~\[;@\s]")


def _install_targets(text: str) -> list[str]:
    """Every distribution named after ``pip install``, normalised (PEP 503).

    Options (``-U``, ``--upgrade``, ...) are skipped; quotes, version
    specifiers, extras and trailing punctuation are stripped. Every remaining
    word on the line counts, so a second target cannot hide behind the first.
    """
    names = []
    for match in _PIP_INSTALL.finditer(text):
        for arg in match.group("args").split("#", 1)[0].split():
            if arg.startswith("-"):
                continue
            spec = _SPEC_END.split(arg.strip("\"'"), maxsplit=1)[0].strip("\"'.,:()")
            if spec:
                names.append(re.sub(r"[-_.]+", "-", spec).lower())
    return names


def test_note_installs_only_the_published_distribution(guide_text: str) -> None:
    """The only distribution the note tells readers to install is ``sigantry``.

    A ``pip install`` of any other name, quoted or not, names a distribution
    this project does not publish: the dependency-confusion shape.
    """
    names = _install_targets(guide_text)
    assert names, "note must show the published install line"
    offenders = [name for name in names if name != "sigantry"]
    assert offenders == [], (
        f"note installs a distribution this project does not publish: {offenders}"
    )
    assert re.search(r"^pip install sigantry$", guide_text, re.MULTILINE), (
        "note must show the published install line"
    )


def test_verify_section_is_scoped_past_1_0_0(guide_text: str) -> None:
    """1.0.0 reads only the old names and warns about neither.

    The Verify command passes on 1.0.0 whatever the config file is called, so
    the section must say which releases it applies to and what 1.0.0 reads.
    """
    assert "## Verify" in guide_text, "migration note missing heading: '## Verify'"
    verify = guide_text.split("## Verify", 1)[1].split("\n## ", 1)[0]
    for token in ("after 1.0.0", "On 1.0.0", ".fabric-dataops.toml", "FDT_"):
        assert token in verify, f"Verify section does not mention {token!r}"


def test_note_references_adr_0011(guide_text: str) -> None:
    assert "ADR-0011-rename-to-sigantry.md" in guide_text, (
        "migration note must link ADR-0011 (the rename decision) by filename."
    )


def test_note_does_not_link_sigantry_dev(guide_text: str) -> None:
    """The sigantry.dev domain is not cleared (ADR-0011 V3-RISK-1)."""
    for pattern in (
        "https://sigantry.dev",
        "http://sigantry.dev",
        "(sigantry.dev)",
        "://sigantry.dev",
    ):
        assert pattern not in guide_text, f"migration note links sigantry.dev ({pattern!r})"


def test_note_points_issues_at_the_public_repository(guide_text: str) -> None:
    assert "https://github.com/Bralabee/sigantry/issues" in guide_text, (
        "migration note must tell readers where to file issues."
    )
