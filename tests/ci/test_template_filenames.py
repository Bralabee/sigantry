"""Plan 08-03 guard: the two HS2-branded template filenames are renamed to
generic names (PROD-10). No consumer reference may point at the old names.

- `templates/extends/hs2-secure-pipeline.yml` -> `templates/extends/secure-pipeline.yml`
- `templates/environments/hs2-spark-diagnostic-emitter.yml` -> `templates/environments/spark-diagnostic-emitter.yml`
"""

from __future__ import annotations

import re
from pathlib import Path

OLD_SECURE = Path("templates/extends/hs2-secure-pipeline.yml")
NEW_SECURE = Path("templates/extends/secure-pipeline.yml")
OLD_SPARK = Path("templates/environments/hs2-spark-diagnostic-emitter.yml")
NEW_SPARK = Path("templates/environments/spark-diagnostic-emitter.yml")

# Carve-outs where historical references are intentional (migration docs,
# plan text, etc.). The repo-wide scan below excludes these trees.
#
# Matched as **full relative path prefixes**, not directory basenames: the
# previous basename comparison meant the `docs/migration/` carve-out silently
# applied to any directory called `migration` at any depth (GATE-15).
#
# Caches, build output and virtualenvs are not listed because the scan
# enumerates the git index (see the `repo_files` fixture in the project-root
# conftest) and git never reports them.
EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    ".planning/",
    "docs/migration/",
)

# Files that are allowed to reference the old HS2-branded filenames as
# before/after reference text. Plan 08-06: the migration guide's smoke
# test asserts the guide mentions the pre-v2.0 template names so consumer
# engineers recognise them while migrating.
_ALLOWLISTED_FILES: frozenset[str] = frozenset(
    {
        "tests/docs/test_migration_guide.py",
        # Plan 08-06 release notes necessarily reference the old template
        # filenames as part of the BREAKING CHANGES section for consumer
        # migration awareness.
        "CHANGELOG.md",
        # The HS2 plugin ships a consumer-facing migration walkthrough that
        # references the pre-v2.0 template filenames (same rationale as
        # docs/migration/1.x-to-2.0.md, which is excluded via
        # EXCLUDED_PATH_PREFIXES).
        # Path renamed from fabric-dataops-toolkits-hs2 in Plan 10-03 per ADR-0011.
        "sigantry-hs2/docs/tutorial/README.md",
    }
)


def _is_excluded(rel: str) -> bool:
    """`rel` is a repo-root-relative POSIX path, as `repo_files` yields it."""
    if rel.startswith(EXCLUDED_PATH_PREFIXES):
        return True
    return rel in _ALLOWLISTED_FILES


def test_old_hs2_filename_deleted():
    assert not OLD_SECURE.exists(), f"Plan 08-03: {OLD_SECURE} must be renamed (gone)"


def test_old_hs2_spark_filename_deleted():
    assert not OLD_SPARK.exists(), f"Plan 08-03: {OLD_SPARK} must be renamed (gone)"


def test_new_secure_pipeline_filename_exists():
    assert NEW_SECURE.exists(), f"Plan 08-03: {NEW_SECURE} must exist"


def test_new_spark_emitter_filename_exists():
    assert NEW_SPARK.exists(), f"Plan 08-03: {NEW_SPARK} must exist"


def test_no_references_to_old_secure_pipeline_filename(repo_files):
    """Scan the shippable repo for any reference to `hs2-secure-pipeline.yml`.

    Scans the git index (`repo_files`), not the working tree, so gitignored
    build output and untracked scratch directories cannot trip the gate.
    Excludes `.planning/` and `docs/migration/`.
    """
    pattern = re.compile(r"hs2-secure-pipeline\.yml")
    violations: list[str] = []
    for path, rel in repo_files:
        if _is_excluded(rel):
            continue
        # Allow this test file itself to mention the old name.
        if path.resolve() == Path(__file__).resolve():
            continue
        # Skip binary or very large files safely.
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            violations.append(rel)
    assert not violations, (
        "Plan 08-03: old filename 'hs2-secure-pipeline.yml' still referenced in:\n"
        + "\n".join(sorted(violations))
    )


def test_no_references_to_old_spark_emitter_filename(repo_files):
    """Scan the shippable repo for any reference to `hs2-spark-diagnostic-emitter.yml`."""
    pattern = re.compile(r"hs2-spark-diagnostic-emitter\.yml")
    violations: list[str] = []
    for path, rel in repo_files:
        if _is_excluded(rel):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            violations.append(rel)
    assert not violations, (
        "Plan 08-03: old filename 'hs2-spark-diagnostic-emitter.yml' still referenced in:\n"
        + "\n".join(sorted(violations))
    )


def test_templates_tree_has_no_hs2_references():
    """Direct grep gate at `templates/` level. Zero LEGACY HS2 hits.

    Plan 08-03 originally forbade any "hs2" substring under templates/. Plan
    10-03 (ADR-0011) renamed `fabric-dataops-toolkits-hs2` to `sigantry-hs2`
    and `Hs2Fabric` to `SigantryHs2`. Both new names legitimately contain the
    "hs2" substring as part of the post-rename Sigantry plugin naming. The
    forbidden cases are now narrower: the LEGACY substring patterns
    (`fabric-dataops-toolkits-hs2`, `Hs2Fabric`, free-standing `HS2`) and any
    `hs2` not preceded by `sigantry-`/`sigantry_`/`Sigantry`.

    Negative lookbehinds skip the legitimate post-rename names while still
    flagging the legacy patterns. `templates/parameters.example.yml` may still
    retain HS2 as an example value with a documented comment per the original
    Plan 08-03 allow-list rationale.
    """
    pattern = re.compile(r"(?<!sigantry-)(?<!sigantry_)(?<!sigantry)hs2", re.IGNORECASE)
    # The Plan 10-05 shim-dist allowlist entry for templates/stages/ci.yml
    # dropped in v3.1 with the shim machinery itself: no template may
    # reference legacy HS2-branded dist names any more.
    allowlist: set[str] = set()
    violations: list[str] = []
    for path in Path("templates").rglob("*"):
        if not path.is_file():
            continue
        rel = str(path).replace("\\", "/")
        if rel in allowlist:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            violations.append(str(path))
    assert not violations, (
        "Plan 08-03 (post-Plan-10-03): legacy HS2 references remain under templates/:\n"
        + "\n".join(sorted(violations))
    )
