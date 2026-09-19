"""docs/ relative-link integrity.

Every relative markdown link in docs/ must resolve to a committed file.
Catches ADR renames, runbook deletions, typo'd paths - before they land on the wiki.

Phase 0 introduced this file covering docs/00-prerequisites/ and docs/decisions/.
Phase 1 (plan 01-03) extends coverage to docs/index.md, docs/getting-started/,
and docs/reference/, and teaches the resolver to handle ADO wiki-style links
that omit the .md suffix.

No network. No external dependencies beyond stdlib + pytest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
PLANNING_ROOT = REPO_ROOT / ".planning"

# Match markdown links whose target is a relative path (not http/https/mailto/anchor-only).
# Captures the path part before any anchor. E.g. [text](../foo/bar.md#anchor) -> '../foo/bar.md'.
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _iter_relative_links(md: Path) -> Iterable[tuple[str, str]]:
    """Yield (link_text, target_path) for every relative link in a markdown file."""
    text = md.read_text(encoding="utf-8")
    for match in _LINK_RE.finditer(text):
        link_text = match.group(1)
        target = match.group(2).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Strip anchor and query.
        path_part = target.split("#", 1)[0].split("?", 1)[0].strip()
        if not path_part:
            continue
        yield link_text, path_part


def _resolve_target(origin: Path, target: str) -> Path | None:
    """Resolve a relative link target against the origin markdown file.

    Supports:
    - direct file links (Phase 0 canonical form, e.g. `./foo.md`);
    - ADO wiki-style links that omit the .md suffix (Phase 1 extension);
    - folder links (`./subfolder/`) preserved from Phase 0 behaviour;
    - folder links that resolve via an `index.md` / `README.md` default page.
    """
    candidate = (origin.parent / target).resolve()
    if candidate.is_file():
        return candidate
    # ADO wiki style: links often omit the .md suffix. Try with .md appended.
    md_candidate = candidate.with_suffix(".md")
    if md_candidate.is_file():
        return md_candidate
    # Directory target: try default pages first, then accept the directory
    # itself (Phase 0 behaviour - a link to `./runbooks/` is valid when the
    # directory exists, even without an index page).
    if candidate.is_dir():
        for default in ("index.md", "README.md"):
            if (candidate / default).is_file():
                return candidate / default
        return candidate
    return None


def _is_absent_planning_target(origin: Path, target: str) -> bool:
    """True when a link points into `.planning/` and that tree is not checked out.

    `.planning/` holds local-only GSD artefacts and is gitignored by design, so
    on the GitHub runner every link into it is unresolvable for a reason that
    says nothing about documentation health. Suppressing exactly that case lets
    the module run in GitHub CI instead of being `--ignore`d wholesale - which is
    how a genuinely broken `docs/index.md` link survived ~2 months unnoticed.

    The suppression is conditional, not blanket: wherever `.planning/` IS present
    - every developer checkout, and the ADO runner - links into it resolve
    normally, so a typo'd planning path still fails there.
    """
    if PLANNING_ROOT.is_dir():
        return False
    candidate = (origin.parent / target).resolve()
    return candidate == PLANNING_ROOT or PLANNING_ROOT in candidate.parents


def _collect_broken_links() -> list[str]:
    broken: list[str] = []
    for md in sorted(DOCS_ROOT.rglob("*.md")):
        for link_text, target in _iter_relative_links(md):
            resolved = _resolve_target(md, target)
            if resolved is None and not _is_absent_planning_target(md, target):
                rel = md.relative_to(REPO_ROOT)
                broken.append(f"{rel}: [{link_text}]({target})")
    return broken


def test_no_broken_relative_links_in_docs() -> None:
    broken = _collect_broken_links()
    assert not broken, "Broken relative links in docs/:\n  " + "\n  ".join(broken)


def test_at_least_one_markdown_file_scanned() -> None:
    """Regression guard: if docs/ ever empties, the link test trivially passes -
    this test keeps that from happening.

    Plan 08-05 moved HS2 pages out of base docs; the floor is lower now.
    """
    count = sum(1 for _ in DOCS_ROOT.rglob("*.md"))
    # Base floor (Plan 08-05): index + 2 getting-started + reference/protocols
    # + runbooks/INDEX + contributing + release-process + 9 API pages = 15.
    assert count >= 8, f"Expected at least 8 .md files under docs/; found {count}."


# Phase 1 extensions -----------------------------------------------------------


def test_docs_index_references_all_top_level_sections() -> None:
    """docs/index.md MUST link to each top-level wiki section.

    Plan 08-05 stripped HS2-specific nav from base docs; the remaining
    required targets are the generic base-platform sections.
    """
    index = (DOCS_ROOT / "index.md").read_text(encoding="utf-8")
    required_targets = [
        "getting-started/install",
        "reference/protocols",
        "api/index",
        "runbooks/INDEX",
        "contributing",
        "release-process",
    ]
    missing = [t for t in required_targets if t not in index]
    assert not missing, f"docs/index.md missing links to: {missing}"


def test_every_wiki_folder_has_a_dot_order_file() -> None:
    """ADO wiki publish-from-code convention: every folder under docs/ that
    holds wiki pages needs a .order so rendering is deterministic.

    Non-wiki data folders (e.g. docs/00-prerequisites/evidence/samples,
    schema) are exempt.
    """
    folders = [d for d in DOCS_ROOT.rglob("*") if d.is_dir()]
    missing = [d for d in folders if not (d / ".order").is_file()]
    # docs/00-prerequisites/evidence/ and its children are data stores.
    missing = [d for d in missing if "evidence" not in d.parts]
    assert not missing, f"folders missing .order: {missing}"


def test_dot_order_files_are_lowercase_and_lf() -> None:
    """Mitigates the Phase 1 research pitfall on .order case sensitivity."""
    all_orders = list(DOCS_ROOT.rglob(".order"))
    # Case sanity: Linux filesystem is case-sensitive; any drift would show up.
    names = {o.name for o in all_orders}
    assert names == {".order"}, f"unexpected casing: {names}"
    for o in all_orders:
        raw = o.read_bytes()
        assert b"\r\n" not in raw, f"CRLF in {o} - .gitattributes must enforce LF"


def test_dot_order_entries_have_no_extensions_or_paths() -> None:
    """Phase 0 convention: one entry per line, no .md suffix, no paths."""
    for o in DOCS_ROOT.rglob(".order"):
        for raw_line in o.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            assert ".md" not in line, f"{o}: '.md' in line {line!r}"
            assert "/" not in line, f"{o}: path separator in line {line!r}"


def test_docs_tree_covers_base_platform_pages() -> None:
    """Regression guard: the base-platform generic pages exist.

    Plan 08-05 moved HS2-specific pages (00-prerequisites/, decisions/,
    HS2 reference pages) to the plugin docs tree; this test now only
    tracks the vendor-agnostic base docs contract.
    """
    required = [
        "index.md",
        "getting-started/install.md",
        "getting-started/quickstart.md",
        "reference/protocols.md",
        "runbooks/INDEX.md",
        "contributing.md",
        "release-process.md",
    ]
    missing = [r for r in required if not (DOCS_ROOT / r).is_file()]
    assert not missing, f"missing expected docs pages: {missing}"


# Per-file resolution -----------------------------------------------------------


@pytest.mark.parametrize(
    "md_path",
    sorted(DOCS_ROOT.rglob("*.md")),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_markdown_links_resolve(md_path: Path) -> None:
    """Per-file view so a broken link in a new page shows up as a named
    parametrised failure instead of being buried in the aggregate test."""
    broken: list[tuple[str, str]] = []
    for link_text, target in _iter_relative_links(md_path):
        resolved = _resolve_target(md_path, target)
        if resolved is None and not _is_absent_planning_target(md_path, target):
            broken.append((link_text, target))
    assert not broken, f"broken links in {md_path.relative_to(REPO_ROOT)}: {broken}"
