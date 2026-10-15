"""Relative links in the starter and demo scaffolds resolve inside the scaffold.

#17. ``templates/starter/`` and ``templates/demo/`` are copied out of this
repository to be used, so a relative link that climbs out of the scaffold
(``../../docs/decisions/...``) resolves here and breaks in every copy. Links to
this repository's own documents use its GitHub URL instead.
"""

from __future__ import annotations

import pathlib
import re

_SCAFFOLDS: tuple[str, ...] = ("templates/starter/", "templates/demo/")

# An inline Markdown link target: ``](target)``, minus any ``#fragment``.
_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#)", re.IGNORECASE)


def _broken_links(path: pathlib.Path, scaffold_root: pathlib.Path, text: str) -> list[str]:
    broken: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for target in _LINK.findall(line):
            if _EXTERNAL.match(target):
                continue
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            inside = resolved == scaffold_root or scaffold_root in resolved.parents
            if not inside or not resolved.exists():
                broken.append(f"{lineno}: {target}")
    return broken


def test_scaffold_relative_links_resolve_inside_the_scaffold(
    repo_root: pathlib.Path,
    repo_files: tuple[tuple[pathlib.Path, str], ...],
) -> None:
    offenders: list[str] = []
    checked = 0
    for path, rel in repo_files:
        scaffold = next((s for s in _SCAFFOLDS if rel.startswith(s)), None)
        if scaffold is None or not rel.endswith(".md"):
            continue
        checked += 1
        root = (repo_root / scaffold).resolve()
        for hit in _broken_links(path.resolve(), root, path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{hit}")
    assert checked >= 10, f"only {checked} scaffold Markdown files read; the inventory is wrong"
    assert not offenders, (
        "these links leave the scaffold or point at nothing, so they break in a copy "
        "of it; link this repository's documents by GitHub URL:\n" + "\n".join(offenders)
    )


def test_the_link_check_fires(tmp_path: pathlib.Path) -> None:
    """Fail-direction arm: an escaping link and a missing file are both reported."""
    root = tmp_path / "starter"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "BRANCHING.md").write_text("x", encoding="utf-8")
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    page = root / "docs" / "QUICKSTART.md"
    text = (
        "[ok](BRANCHING.md) [ok](BRANCHING.md#top) [web](https://example.com/a)\n"
        "[escapes](../../outside.md)\n"
        "[missing](NOPE.md)\n"
    )
    assert _broken_links(page, root, text) == ["2: ../../outside.md", "3: NOPE.md"]
