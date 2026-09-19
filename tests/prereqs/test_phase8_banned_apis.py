"""Phase 8 banned-API invariant tests (Plan 08-02 Task 3).

Locks in PROD-05, PROD-06, PROD-07, PROD-08 at CI level: any future change
that re-introduces a deleted ACME surface (qualitykit import, nimbus_data_platform
import, Custom-Acme literal, ACME_ env-var read, deleted gate/profile files)
fails ``pytest tests/prereqs/``.

These tests are pure file-walk + regex checks — no imports, no mocks, no
network. They run in <100ms.

The assertions use **word boundaries** (``\\b``) to avoid false positives on
English words that happen to contain ``nimbus`` as a substring (e.g. ``claims``).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Post-rename path constant (Plan 10-02 / ADR-0011). The legacy name stays as
# a historical reference in the docstring above; the live walk targets
# sigantry_core/.
_SIGANTRY_CORE = _REPO_ROOT / "sigantry_core"
# Backwards-compat alias retained for any external callers that imported the
# old constant during the shim window; removed in v3.1.
_FABRIC_DATAOPS = _SIGANTRY_CORE


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _iter_py_files(root: Path):
    """Yield every ``.py`` file under ``root`` excluding ``__pycache__``."""
    for path in root.rglob("*.py"):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _module_scope_matches(pattern: re.Pattern[str], text: str) -> list[tuple[int, str]]:
    """Return ``(lineno, line)`` pairs for lines where ``pattern`` matches at
    module scope (no leading whitespace).
    """
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith((" ", "\t")):
            continue
        if pattern.search(line):
            hits.append((lineno, line))
    return hits


# --------------------------------------------------------------------------
# Banned imports
# --------------------------------------------------------------------------


def test_no_module_scope_qualitykit_import() -> None:
    """PROD-06 / CLAUDE.md: zero module-scope ``qualitykit`` imports in base."""
    pattern = re.compile(r"^(from|import)\s+qualitykit(\s|\.|$)")
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in _module_scope_matches(pattern, _read(path)):
            offenders.append((path, lineno, line))
    assert offenders == [], (
        "Module-scope `qualitykit` imports violate PROD-06: "
        f"{offenders}. qualitykit belongs in the ACME plugin package."
    )


def test_no_module_scope_nimbus_data_platform_import() -> None:
    """CLAUDE.md dep-direction: zero ``nimbus_data_platform`` imports at module scope."""
    pattern = re.compile(r"^(from|import)\s+nimbus_data_platform(\s|\.|$)")
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in _module_scope_matches(pattern, _read(path)):
            offenders.append((path, lineno, line))
    assert offenders == [], (
        "Module-scope `nimbus_data_platform` imports violate the "
        f"dep-direction invariant: {offenders}. NIMBUS depends on the "
        "toolkit, never the reverse."
    )


# --------------------------------------------------------------------------
# Banned string literals + env reads
# --------------------------------------------------------------------------


def test_no_custom_acme_literal() -> None:
    """PROD-05: zero ``Custom-Acme`` string literals in Python base."""
    pattern = re.compile(r"Custom-Acme")
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if pattern.search(line):
                offenders.append((path, lineno, line.strip()))
    assert offenders == [], (
        "Custom-Acme stream names are a plugin concern; the base package "
        f"must not carry them: {offenders}."
    )


def test_no_acme_env_var_reads() -> None:
    """PROD-05: zero ``os.environ.get('ACME_...')`` / ``os.getenv('ACME_...')`` calls."""
    pattern = re.compile(
        r"""os\.(environ\.get|getenv)\(\s*['"]ACME_""",
    )
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if pattern.search(line):
                offenders.append((path, lineno, line.strip()))
    assert offenders == [], (
        "ACME_* environment variables are consumer-plugin-specific; the base "
        f"package must not read them: {offenders}."
    )


# --------------------------------------------------------------------------
# Banned substrings (word-boundary scoped to avoid ``claims`` false positives)
# --------------------------------------------------------------------------


_BANNED_WORD_PATTERN = re.compile(r"\bacme\b|\baims\b|\bACME\b|\bAIMS\b|Acme[A-Za-z]|ACME_|NIMBUS_")


def test_no_acme_string_in_python_base() -> None:
    """PROD-08: zero ``acme`` / ``nimbus`` / ``ACME`` / ``NIMBUS`` / ``Acme<Pascal>`` /
    ``ACME_`` / ``NIMBUS_`` occurrences anywhere under ``sigantry_core/``
    (renamed from ``fabric_dataops_toolkits/`` in Plan 10-02 per ADR-0011).

    Word boundaries scope the check to whole tokens — ``claims``,
    ``dreams``, ``exclaims`` etc. do NOT trip the gate.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if _BANNED_WORD_PATTERN.search(line):
                offenders.append((path, lineno, line.strip()))
    assert offenders == [], (
        "ACME / NIMBUS branding survived in the base package: "
        f"{offenders}. Move ACME-specific content to the "
        "sigantry-acme plugin package (Plan 08-05, renamed in Plan 10-03)."
    )


def test_no_qualitykit_substring_in_python_base() -> None:
    """PROD-06 / PROD-08: zero ``qualitykit`` substring hits in the Python base."""
    pattern = re.compile(r"qualitykit")
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(_FABRIC_DATAOPS):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if pattern.search(line):
                offenders.append((path, lineno, line.strip()))
    assert offenders == [], (
        "qualitykit is a peer-platform package and must not appear "
        f"anywhere in the base package's Python sources: {offenders}."
    )


# --------------------------------------------------------------------------
# pyproject.toml — base must not depend on the peer packages
# --------------------------------------------------------------------------


def test_base_does_not_depend_on_qualitykit() -> None:
    """``pyproject.toml`` must not name ``qualitykit`` as a dependency."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    deps = list(project.get("dependencies", []) or [])
    for table in (project.get("optional-dependencies", {}) or {}).values():
        deps.extend(table or [])
    offenders = [d for d in deps if "qualitykit" in d or "qualitykit" in d]
    assert offenders == [], (
        "Base pyproject.toml must not depend on qualitykit: "
        f"{offenders}. qualitykit is a peer platform installed alongside "
        "the base package, never as a transitive dependency."
    )


def test_base_does_not_depend_on_nimbus_data_platform() -> None:
    """``pyproject.toml`` must not name ``nimbus_data_platform`` as a dependency."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    deps = list(project.get("dependencies", []) or [])
    for table in (project.get("optional-dependencies", {}) or {}).values():
        deps.extend(table or [])
    offenders = [d for d in deps if "nimbus_data_platform" in d or "nimbus-data-platform" in d]
    assert offenders == [], (
        f"Base pyproject.toml must not depend on nimbus_data_platform: {offenders}."
    )


# --------------------------------------------------------------------------
# Deleted-file invariants
# --------------------------------------------------------------------------


def test_deleted_gate_py_stays_deleted() -> None:
    """``sigantry_core/dq/gate.py`` (was ``fabric_dataops_toolkits/dq/gate.py``
    pre-Plan-10-02) was deleted in Plan 08-02."""
    gate_path = _SIGANTRY_CORE / "dq" / "gate.py"
    assert not gate_path.exists(), (
        f"{gate_path} must not exist — ACME gate body moved to "
        "sigantry-acme in Plan 08-05 (renamed in Plan 10-03)."
    )


def test_deleted_nimbus_profile_stays_deleted() -> None:
    """``sigantry_core/deploy/profiles/nimbus.py`` (was under
    ``fabric_dataops_toolkits/`` pre-Plan-10-02) was deleted in Plan 08-02."""
    nimbus_path = _SIGANTRY_CORE / "deploy" / "profiles" / "nimbus.py"
    assert not nimbus_path.exists(), (
        f"{nimbus_path} must not exist — ACME nimbus profile moved to "
        "sigantry-acme in Plan 08-05 (renamed in Plan 10-03)."
    )


def test_deleted_monitor_models_stays_deleted() -> None:
    """``sigantry_core/monitor/models.py`` (was under
    ``fabric_dataops_toolkits/`` pre-Plan-10-02) was deleted in Plan 08-02."""
    models_path = _SIGANTRY_CORE / "monitor" / "models.py"
    assert not models_path.exists(), (
        f"{models_path} must not exist — its TelemetryEvent dataclass was "
        "locked to the ACME DCR column set; callers now use "
        "sigantry_core.protocols.TelemetryEvent."
    )


# --------------------------------------------------------------------------
# Plan 08-05 extensions — repo-wide invariants outside the ACME plugin dirs.
# --------------------------------------------------------------------------


_PLUGIN_ROOT = _REPO_ROOT / "sigantry-acme"


# File paths (relative to repo root) that are allowed to mention the
# banned tokens because they are negative-assertion tests or structural
# references to the plugin-package name. Keep this list SHORT and
# SPECIFIC — each entry is a documented exception.
def _load_grep_allowlist() -> frozenset[str]:
    """Load the W4.3 structured banned-API allowlist from YAML.

    Pre-W4.3 the allowlist lived inline as an 88-entry ``frozenset``
    literal with rationale comments interspersed (~280 lines of mixed
    code + comment). W4.3 extracted it to
    ``tests/prereqs/banned_api_allowlist.yaml`` with structured fields
    (``path``, ``pattern_class``, ``adr``, ``rationale``) so adding an
    exemption is a 4-field YAML block instead of a Python-syntax edit.

    The loader runs at module import time; YAML parse failures fail
    the whole prereqs suite (intentional -- a malformed allowlist is
    a CI-stopping bug, not a per-test skip).
    """
    import yaml

    yaml_path = _REPO_ROOT / "tests" / "prereqs" / "banned_api_allowlist.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return frozenset(entry["path"] for entry in data["allowlist"])


_GREP_ALLOWLIST_RELATIVE: frozenset[str] = _load_grep_allowlist()

# Grep patterns we run repo-wide (word-boundary scoped per 08-02 Deviation).
_REPO_WIDE_PATTERN = re.compile(
    r"\bacme\b|\baims\b|\bACME\b|\bAIMS\b|Acme[A-Za-z]|ACME_|NIMBUS_|Custom-Acme|qualitykit"
)

# File extensions we scan. Binary files / lockfiles excluded.
_SCAN_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".bicep",
    ".bicepparam",
    ".json",
    ".ps1",
    ".psm1",
    ".psd1",
)

# Top-level directories excluded from the scan (plugin dirs, planning,
# caches, vendored trees).
_SCAN_EXCLUDED_DIRS: tuple[str, ...] = (
    ".planning",
    ".qa-council",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "build",
    "dist",
    "sigantry-acme",  # ACME plugin package (renamed from fabric-dataops-toolkits-acme in Plan 10-03).
    "SigantryAcme",  # ACME PowerShell plugin module (Plan 08-04 split as AcmeFabric; renamed to SigantryAcme in Plan 10-04 per ADR-0011).
    "docs/migration",  # Reserved for 08-06 release notes.
    "remotion-tutorial",  # Unrelated video-tutorial scratch project (gitignored).
)

# Filenames excluded from the scan (legacy release notes / contributor
# docs that reference historical ACME names by design).
_SCAN_EXCLUDED_FILES: tuple[str, ...] = (
    "CHANGELOG.md",
    "HANDOFF.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "README.md",
)


def _iter_scannable_files():
    """Yield every scannable file relative to the repo root."""
    for path in _REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SCAN_SUFFIXES:
            continue
        rel = path.relative_to(_REPO_ROOT)
        parts = rel.parts
        # Exclude tree roots + "docs/migration"-style two-part matches.
        if any(ex in parts for ex in _SCAN_EXCLUDED_DIRS):
            continue
        rel_str = str(rel).replace("\\", "/")
        if rel_str.startswith("docs/migration/"):
            continue
        if rel.name in _SCAN_EXCLUDED_FILES:
            continue
        yield path, rel_str


def test_acme_scripts_moved_from_base_root() -> None:
    """Plan 08-05: ACME PS scripts no longer live at repo-root ``scripts/``."""
    base_sc_bootstrap = _REPO_ROOT / "scripts" / "service-connection-bootstrap.ps1"
    base_prereqs = _REPO_ROOT / "scripts" / "prereqs"
    assert not base_sc_bootstrap.exists(), (
        f"{base_sc_bootstrap} must not exist — moved to plugin in Plan 08-05."
    )
    assert not base_prereqs.exists(), (
        f"{base_prereqs} must not exist — moved to plugin in Plan 08-05."
    )


def test_acme_docs_moved_from_base_tree() -> None:
    """Plan 08-05: ACME-specific docs no longer live under base ``docs/``."""
    for rel in (
        "docs/00-prerequisites",
        "docs/decisions/ADR-0001-gsd-init-and-wrap-upstream-stance.md",
        "docs/reference/medallion.md",
        "docs/reference/topology.md",
        "docs/reference/nimbus-dq-wiring.md",
    ):
        path = _REPO_ROOT / rel
        assert not path.exists(), (
            f"{path} must not exist under base docs — moved to plugin in Plan 08-05 (PROD-16)."
        )


def test_base_mkdocs_yml_is_vendor_agnostic() -> None:
    """Plan 08-05 / Plan 10-02: base ``mkdocs.yml`` has a generic product
    site_name (renamed from 'Fabric DataOps Toolkits' to 'Sigantry' in
    Plan 10-02 per ADR-0011) and no ACME nav."""
    src = (_REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "site_name: Sigantry" in src, (
        "base mkdocs.yml site_name must be the generic product name 'Sigantry' "
        "(renamed from 'Fabric DataOps Toolkits' in Plan 10-02 per ADR-0011)"
    )
    # No ACME/NIMBUS-branded pages in base nav.
    assert "acme" not in src.lower(), (
        "base mkdocs.yml must not reference 'acme' (plugin owns ACME nav)"
    )
    assert "nimbus" not in src.lower(), (
        "base mkdocs.yml must not reference 'nimbus' (plugin owns NIMBUS nav)"
    )


def test_no_acme_in_base_docs_tree() -> None:
    """Plan 08-05: ``docs/`` (excluding ``docs/migration/``) is ACME-free.

    Meta-reference docs authored during 08.1 gap-closure (thread-safety,
    observation-planes, ADR-0004) legitimately name ACME plugin seams as
    concrete examples of the plugin architecture -- they are excluded
    via the shared ``_GREP_ALLOWLIST_RELATIVE`` constant.
    """
    docs_root = _REPO_ROOT / "docs"
    offenders: list[tuple[str, int, str]] = []
    for path in docs_root.rglob("*.md"):
        if "migration" in path.relative_to(docs_root).parts:
            continue
        rel = str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
        if rel in _GREP_ALLOWLIST_RELATIVE:
            continue
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if _REPO_WIDE_PATTERN.search(line):
                offenders.append((rel, lineno, line.strip()))
    assert offenders == [], (
        f"ACME branding survived in base docs: {offenders}. Move ACME content to sigantry-acme/docs/."
    )


def test_no_acme_bicepparam_in_base() -> None:
    """Plan 08-05: base ``bicep/`` contains no ACME-tagged defaults."""
    bicep_root = _REPO_ROOT / "bicep"
    offenders: list[tuple[str, int, str]] = []
    for path in bicep_root.rglob("*.bicepparam"):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if _REPO_WIDE_PATTERN.search(line):
                offenders.append((str(path.relative_to(_REPO_ROOT)), lineno, line.strip()))
    assert offenders == [], (
        "ACME defaults leaked into base bicep/: "
        f"{offenders}. ACME defaults live in sigantry-acme/bicep/."
    )


def test_repo_wide_grep_clean_outside_plugin_dirs() -> None:
    """Plan 08-05 grep gate: zero ACME hits outside plugin + planning + migration."""
    offenders: list[tuple[str, int, str]] = []
    for path, rel_str in _iter_scannable_files():
        if rel_str in _GREP_ALLOWLIST_RELATIVE:
            continue
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if _REPO_WIDE_PATTERN.search(line):
                offenders.append((rel_str, lineno, line.strip()))
    assert offenders == [], (
        "Repo-wide ACME grep gate (Plan 08-05 PROD-17) fired outside the "
        f"plugin dirs: {offenders}. Move ACME content to "
        "sigantry-acme/ or allowlist the file in "
        "_GREP_ALLOWLIST_RELATIVE with a documented rationale."
    )


def test_base_pyproject_does_not_depend_on_acme_plugin() -> None:
    """Plan 08-05: base pyproject must not declare a dep on the ACME plugin.

    Dependency direction is one-way: the plugin depends on the base, not
    the reverse.
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    deps = list(project.get("dependencies", []) or [])
    for table in (project.get("optional-dependencies", {}) or {}).values():
        deps.extend(table or [])
    offenders = [
        d
        for d in deps
        if "fabric-dataops-toolkits-acme" in d
        or "fabric_dataops_toolkits_acme" in d
        or "sigantry-acme" in d
        or "sigantry_acme" in d
    ]
    assert offenders == [], (
        "Base pyproject.toml must not depend on the ACME plugin: "
        f"{offenders}. Dependency direction is one-way."
    )


# --------------------------------------------------------------------------
# Phase 10 Plan 01 regression tests — lock the banned-API gate's scope.
# --------------------------------------------------------------------------


def test_brief_paper_artefacts_on_allowlist() -> None:
    """BRIEF-01..06 paper artefacts are explicitly whitelisted (Phase 10 Plan 01).

    Locks in the 2026-04-24 user decision documented in
    .planning/phases/10-product-brief-architecture-refresh/10-CONTEXT.md: the
    narrowest-possible allowlist contains EXACTLY these five paths.
    """
    expected_brief_paths = {
        "docs/PRODUCT-BRIEF.md",
        "docs/decisions/ADR-0010-commercial-model.md",
        "docs/decisions/ADR-0011-rename-to-sigantry.md",
        "docs/reference/seam-map.md",
        "docs/reference/dual-ci-strategy.md",
    }
    missing = expected_brief_paths - _GREP_ALLOWLIST_RELATIVE
    assert not missing, (
        f"Phase 10 paper artefacts missing from _GREP_ALLOWLIST_RELATIVE: {missing}. "
        "Task 1 of 10-01-PLAN.md must add them verbatim."
    )
    # Regression guard: confirm no wildcard doc-path leaked in.
    for entry in _GREP_ALLOWLIST_RELATIVE:
        assert "*" not in entry, (
            f"Wildcard allowlist entry {entry!r} violates the "
            "'narrowest-possible allowlist' rule locked in 10-CONTEXT.md."
        )


def test_acme_env_leak_in_base_py_still_fails() -> None:
    """A synthetic ACME_ env-var read in base py still trips the repo-wide grep.

    We exercise the scanner's regex directly against a synthetic string --
    proves the gate still fires for the case the allowlist is NOT meant
    to cover.
    """
    synthetic_line = 'value = os.environ.get("ACME_FABRIC_TEST_TENANT_ID")'
    assert _REPO_WIDE_PATTERN.search(synthetic_line), (
        "_REPO_WIDE_PATTERN must continue to match ACME_ env-var reads"
    )
    acme_env_pattern = re.compile(r"""os\.(environ\.get|getenv)\(\s*['\"]ACME_""")
    hits = _module_scope_matches(acme_env_pattern, synthetic_line)
    assert hits, "ACME env-var regex must still match synthetic leak"


def test_unscoped_doc_with_acme_still_fails() -> None:
    """Regression: a doc NOT on the allowlist is still caught.

    Uses a synthetic relative path that is guaranteed NOT in the allowlist
    and asserts the repo-wide pattern fires. This proves future doc authors
    cannot quietly slip ACME strings into new docs/*.md files without
    explicitly amending _GREP_ALLOWLIST_RELATIVE.
    """
    synthetic_rel = "docs/synthetic/not-a-real-doc.md"
    assert synthetic_rel not in _GREP_ALLOWLIST_RELATIVE, (
        "Synthetic path must not collide with a real allowlist entry"
    )
    synthetic_line = "ACME will migrate to Sigantry per ADR-0011."
    assert _REPO_WIDE_PATTERN.search(synthetic_line), (
        "Repo-wide pattern must still fire on ACME token in arbitrary doc text"
    )
