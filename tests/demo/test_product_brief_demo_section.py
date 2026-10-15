"""Plan 15-04 / DEMO-01 + DEMO-04 -- docs/PRODUCT-BRIEF.md `## Demo` section invariants.

The `## Demo` section (CONTEXT D-14) says where the demo lives and links the
QUICKSTART. The demo is `templates/demo/` in this repository and is not
provisioned as its own repository (#17), so the section names that tree and
no longer carries the `<DEMO-URL>` placeholder for a public mirror.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BRIEF = _REPO / "docs" / "PRODUCT-BRIEF.md"


def _demo_section(text: str) -> str:
    """Return the slice of PRODUCT-BRIEF.md from `## Demo` to the next H2 (or EOF)."""
    idx = text.find("## Demo")
    assert idx != -1, "PRODUCT-BRIEF.md missing `## Demo` section (CONTEXT D-14)"
    next_idx = text.find("\n## ", idx + len("## Demo"))
    return text[idx : next_idx if next_idx != -1 else len(text)]


def test_product_brief_has_demo_section() -> None:
    """docs/PRODUCT-BRIEF.md contains a `## Demo` section heading (CONTEXT D-14)."""
    text = _BRIEF.read_text(encoding="utf-8")
    assert "## Demo" in text, "PRODUCT-BRIEF.md missing `## Demo` section"


def test_product_brief_demo_section_names_the_tree_and_quickstart() -> None:
    """The `## Demo` section names the in-tree demo and links the QUICKSTART."""
    section = _demo_section(_BRIEF.read_text(encoding="utf-8"))
    assert "templates/demo/" in section, "Demo section does not name templates/demo/"
    assert "not provisioned as its own repository" in section
    assert "<DEMO-URL>" not in section, "Demo section still points at a public mirror URL"
    assert "QUICKSTART" in section, "Demo section missing QUICKSTART link"
