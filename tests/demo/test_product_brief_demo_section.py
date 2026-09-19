"""Plan 15-04 / DEMO-01 + DEMO-04 -- docs/PRODUCT-BRIEF.md `## Demo` section invariants.

Plan 15-04 lands the `## Demo` section in docs/PRODUCT-BRIEF.md per
CONTEXT D-14 (with `<DEMO-URL>` placeholder + `[Watch the 90-second
walkthrough]` link + QUICKSTART reference). Wave 0 xfail stubs
flipped to real assertions here.
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


def test_product_brief_demo_section_has_walkthrough_link() -> None:
    """The `## Demo` section contains the placeholder URL + walkthrough mp4 link + QUICKSTART reference."""
    text = _BRIEF.read_text(encoding="utf-8")
    section = _demo_section(text)
    assert "<DEMO-URL>" in section, "Demo section missing <DEMO-URL> placeholder"
    assert "walkthrough" in section.lower(), "Demo section missing walkthrough reference"
    assert "QUICKSTART" in section, "Demo section missing QUICKSTART link"
