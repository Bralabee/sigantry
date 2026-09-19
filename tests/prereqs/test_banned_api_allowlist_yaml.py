"""Audit-2026-05-07 W4.3 -- falsifiability tests for the structured
banned-API allowlist YAML extraction.

Pre-W4.3 the allowlist lived as an 88-entry inline ``frozenset[str]``
in ``tests/prereqs/test_phase8_banned_apis.py:253-540`` -- 280 lines
of mixed code + comment where each entry's rationale lived as a free-
form ``# comment`` block above the path string. W4.3 extracted to
``tests/prereqs/banned_api_allowlist.yaml`` with structured fields
(``path``, ``pattern_class``, ``adr``, ``rationale``).

Tests below pin:

- The YAML loads + parses cleanly.
- Every entry carries the four required fields.
- ``pattern_class`` is one of the documented values (currently only
  ``hs2_repo_wide_grep``; reserved for future per-rule scoping).
- ``adr`` references resolve to actual files in ``docs/decisions/``.
- The YAML's path set EQUALS the frozenset
  ``_GREP_ALLOWLIST_RELATIVE`` exposed by the loader -- no drift.
- The YAML self-allowlists (entry-zero rationale).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "tests" / "prereqs" / "banned_api_allowlist.yaml"

_VALID_PATTERN_CLASSES = frozenset({"hs2_repo_wide_grep"})


def _load_yaml() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def test_yaml_exists_and_parses() -> None:
    assert YAML_PATH.is_file()
    data = _load_yaml()
    assert "allowlist" in data
    assert isinstance(data["allowlist"], list)
    assert len(data["allowlist"]) > 0


def test_every_entry_has_required_fields() -> None:
    data = _load_yaml()
    for i, entry in enumerate(data["allowlist"]):
        assert "path" in entry, f"entry {i} missing 'path'"
        assert "pattern_class" in entry, f"entry {i} missing 'pattern_class'"
        assert "rationale" in entry, f"entry {i} missing 'rationale'"
        assert isinstance(entry["path"], str)
        assert isinstance(entry["pattern_class"], str)
        assert isinstance(entry["rationale"], str)
        # ADR is optional but if present must be a string.
        if "adr" in entry:
            assert isinstance(entry["adr"], str)


def test_pattern_class_is_documented_value() -> None:
    data = _load_yaml()
    for i, entry in enumerate(data["allowlist"]):
        assert entry["pattern_class"] in _VALID_PATTERN_CLASSES, (
            f"entry {i} ('{entry['path']}') has unknown pattern_class "
            f"{entry['pattern_class']!r}; valid: {sorted(_VALID_PATTERN_CLASSES)}"
        )


def test_adr_references_resolve_to_real_files() -> None:
    """Every ``adr: ADR-XXXX`` value points at a real file in docs/decisions/."""
    data = _load_yaml()
    adr_pattern = re.compile(r"^ADR-(\d{4})$")
    for entry in data["allowlist"]:
        if "adr" not in entry:
            continue
        m = adr_pattern.match(entry["adr"])
        assert m, f"entry '{entry['path']}' has malformed ADR id {entry['adr']!r}"
        # Look for any file matching ADR-{number}-*.md in docs/decisions/.
        adr_id = entry["adr"]
        matches = list((REPO_ROOT / "docs" / "decisions").glob(f"{adr_id}-*.md"))
        assert matches, (
            f"entry '{entry['path']}' references {adr_id} but no "
            f"matching docs/decisions/{adr_id}-*.md file exists."
        )


def test_yaml_path_set_matches_loaded_frozenset() -> None:
    """The YAML's path set is byte-equal to the loader's
    ``_GREP_ALLOWLIST_RELATIVE`` frozenset.

    Falsifiability: a regression that bypasses the YAML loader (e.g.
    re-introducing an inline frozenset) is caught here because the
    loader is the SOLE source of truth.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "test_phase8_banned_apis_mod",
        REPO_ROOT / "tests" / "prereqs" / "test_phase8_banned_apis.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _GREP_ALLOWLIST_RELATIVE = mod._GREP_ALLOWLIST_RELATIVE  # noqa: N806

    data = _load_yaml()
    yaml_paths = {entry["path"] for entry in data["allowlist"]}
    assert yaml_paths == _GREP_ALLOWLIST_RELATIVE, (
        "Drift between YAML allowlist and loaded frozenset.\n"
        f"  YAML only:    {sorted(yaml_paths - _GREP_ALLOWLIST_RELATIVE)}\n"
        f"  Loader only:  {sorted(_GREP_ALLOWLIST_RELATIVE - yaml_paths)}"
    )


def test_yaml_self_allowlists() -> None:
    """The YAML lists itself as an allowlist entry.

    The YAML file's rationale strings legitimately mention the banned
    plugin substrings (those rationales describe WHY each file is
    exempt). Without self-allowlisting, the W4.3 extraction would
    trip the very gate it's enabling.
    """
    data = _load_yaml()
    paths = {entry["path"] for entry in data["allowlist"]}
    assert "tests/prereqs/banned_api_allowlist.yaml" in paths


def test_no_duplicate_paths() -> None:
    """The same path doesn't appear twice in the allowlist."""
    data = _load_yaml()
    paths = [entry["path"] for entry in data["allowlist"]]
    duplicates = [p for p in set(paths) if paths.count(p) > 1]
    assert not duplicates, f"Duplicate paths in allowlist: {duplicates}"


def test_loader_reduces_test_phase8_loc_meaningfully() -> None:
    """``test_phase8_banned_apis.py`` shrunk substantially after W4.3.

    Pre-W4.3: 878 LOC (with the inline 280-line frozenset).
    Post-W4.3 target: < 700 LOC. A regression to >700 means either
    the inline allowlist regressed or new concerns crept inline.
    """
    path = REPO_ROOT / "tests" / "prereqs" / "test_phase8_banned_apis.py"
    loc = path.read_text(encoding="utf-8").count("\n") + 1
    assert loc < 700, (
        f"tests/prereqs/test_phase8_banned_apis.py is {loc} lines; "
        "W4.3 expected < 700 (the 280-line inline allowlist was "
        "extracted to YAML). Either the YAML extraction regressed "
        "or new code crept in."
    )


@pytest.mark.parametrize(
    "expected_path",
    [
        # A handful of well-known entries that the W4.3 extraction
        # MUST preserve. If any of these go missing, the YAML
        # extractor's regex broke for entries with trailing comments.
        "pyproject.toml",
        ".pre-commit-config.yaml",
        "docs/CONSUMING.md",
        "docs/decisions/ADR-0011-rename-to-sigantry.md",
        "docs/decisions/ADR-0014-plugin-trust-model.md",
    ],
)
def test_known_allowlist_entries_present(expected_path: str) -> None:
    """Spot-check that the W4.3 extraction preserved key entries."""
    data = _load_yaml()
    paths = {entry["path"] for entry in data["allowlist"]}
    assert expected_path in paths, (
        f"Expected allowlist entry {expected_path!r} is missing -- "
        "the W4.3 extraction broke for entries with trailing comments "
        "or post-tag additions."
    )
