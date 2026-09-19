"""Render docs/USER-GUIDE.md to a print-quality PDF.

Pipeline:

    USER-GUIDE.md (with embedded ```mermaid blocks)
       |
       v   parse: walk markdown line-by-line; for each ```mermaid block,
       |   record the most recent `## ` heading + a 1-based figure index
       |   (used for caption + landscape tagging + List of Figures)
       |
       +--> mermaid-cli (system Chrome) --> per-block PNG
       |     - portrait figures rendered at 1600px wide,
       |       landscape figures at 2200px, both with
       |       theme.fontSize=20 so labels stay crisp at A4 print DPI
       |     - per-figure mermaid config file is written to a tempdir
       |     - PNG (not SVG) because mermaid 11.x flowcharts emit
       |       <foreignObject>-wrapped HTML labels regardless of the
       |       htmlLabels config flag, and WeasyPrint cannot rasterise
       |       text inside <foreignObject>; PNG bypasses the issue
       |       (Chrome rasterises before WeasyPrint sees the bytes)
       |
       v   inline as <img class="mermaid-png" src="data:image/png;base64,...">
       |   wrapped in <figure> tags carrying the figure number, caption,
       |   and (where applicable) class="landscape" for rotated pages
       |
       +--> markdown -> HTML (markdown lib + extensions:
       |                       fenced_code, tables, toc, codehilite,
       |                       attr_list, def_list, footnotes)
       |
       v   wrap in print HTML template + style.css; insert a
       |   List of Figures block after [TOC]
       |
       +--> WeasyPrint --> docs/Sigantry-User-Guide.pdf

Why this stack
--------------

* No public diagram services reachable from this network -- mermaid.ink
  and kroki.io both timed out / 403'd in May 2026 testing. mermaid-cli
  with system Chromium IS available and renders deterministic PNG.
* WeasyPrint is the only Python HTML->PDF toolkit installed locally
  (verified: ``import weasyprint``  v68.1). Pandoc / wkhtmltopdf are
  not installed.
* Code highlighting via Pygments through markdown's ``codehilite``
  extension; matches what the existing MkDocs site uses.

Run from repo root:

    python scripts/userguide/render.py
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import markdown
import weasyprint

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "docs" / "USER-GUIDE.md"
STYLE = Path(__file__).parent / "style.css"
COVER_TEMPLATE = Path(__file__).parent / "cover.html"
OUTPUT = REPO_ROOT / "docs" / "Sigantry-User-Guide.pdf"
PUPPETEER_CFG = Path(__file__).parent / "puppeteer-config.json"
CHROME = "/usr/bin/google-chrome"

# Figures whose label density (node count, label length) makes them
# illegible at portrait text width (~656px after A4 margins). These get
# rotated to landscape, giving them ~1080px to breathe. Verified by
# inspection 2026-05-08:
#   1  -> Section 2 Architecture (18 nodes / 4 subgraphs)
#   3  -> Section 6 Authentication decision tree (12 nodes, wide labels)
#   4  -> Section 8 Seven verbs decision tree (8 boxes, wide labels)
#   11 -> Appendix E Troubleshooting decision tree (10 nodes, wide labels)
LANDSCAPE_FIGURES: set[int] = {1, 3, 4, 11}

# Mermaid theme config. We render to PNG via mermaid-cli + headless
# Chrome and embed as <img>; SVG output produces <foreignObject>-wrapped
# HTML labels for flowcharts (mermaid 11.x ignores htmlLabels=false in
# many cases) and WeasyPrint cannot rasterise <foreignObject> content,
# so PNG is the robust path. PNG resolution is driven by `-w` (width
# in pixels) -- set high so labels stay crisp at A4 print DPI.
MERMAID_CONFIG_BASE: dict = {
    "theme": "default",
    "themeVariables": {
        "fontFamily": '"Source Sans 3", "Source Sans Pro", "Helvetica Neue", Arial, sans-serif',
        "fontSize": "20px",
        "primaryColor": "#e8eef7",
        "primaryTextColor": "#1a1f2e",
        "primaryBorderColor": "#2b5d9b",
        "lineColor": "#5a6478",
        "secondaryColor": "#f6f7f9",
        "tertiaryColor": "#fafbfd",
    },
    "flowchart": {"htmlLabels": True, "curve": "basis", "nodeSpacing": 50, "rankSpacing": 60},
    "sequence": {
        "diagramMarginX": 25,
        "diagramMarginY": 15,
        "actorMargin": 45,
        "messageMargin": 30,
    },
}


def render_mermaid_block(mermaid_src: str, idx: int, *, landscape: bool) -> str:
    """Run mermaid-cli (with system Chrome) and return an <img> tag
    embedding the rendered diagram as a base64 PNG.

    Why PNG and not SVG: mermaid 11.x renders flowchart node labels
    via `<foreignObject>` even when `htmlLabels` is configured false;
    WeasyPrint can rasterise the SVG geometry but not the HTML inside
    `<foreignObject>`, so labels come out as empty boxes. PNG is the
    robust path -- Chrome rasterises everything (including HTML
    labels) before we hand the bytes to WeasyPrint.

    Resolution is driven by `-w`: portrait figures render at 1600px,
    landscape at 2200px. Both comfortably exceed the printable A4
    surface in pixels at 200 DPI (~2050x1400 / ~1900x2800), keeping
    labels crisp under any reasonable PDF zoom.
    """
    # Render width tuned for printable area + a 2x DPI bump so labels
    # survive zoom. Landscape gets more pixels because it fills the
    # rotated page (~245mm wide) instead of the portrait text column.
    width = 2200 if landscape else 1600
    with tempfile.TemporaryDirectory() as td:
        in_path = Path(td) / f"diagram-{idx}.mmd"
        out_path = Path(td) / f"diagram-{idx}.png"
        cfg_path = Path(td) / f"mermaid-config-{idx}.json"
        in_path.write_text(mermaid_src, encoding="utf-8")
        cfg_path.write_text(json.dumps(MERMAID_CONFIG_BASE), encoding="utf-8")
        cmd = [
            "npx",
            "--yes",
            "@mermaid-js/mermaid-cli@latest",
            "-i",
            str(in_path),
            "-o",
            str(out_path),
            "-p",
            str(PUPPETEER_CFG),
            "-c",
            str(cfg_path),
            "-b",
            "white",
            "-w",
            str(width),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"mermaid-cli failed for diagram {idx}: {result.stderr[-400:]}")
        png_bytes = out_path.read_bytes()
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return f'<img class="mermaid-png" src="data:image/png;base64,{b64}" alt="diagram {idx}" />'


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")


def _walk_blocks(md_text: str) -> list[dict]:
    """Walk markdown and emit a list of items: text-line spans and
    mermaid-block records with the most recent `## ` heading attached.

    Each mermaid record is `{"kind": "mermaid", "src": "...", "heading": "..."}`.
    Text records are `{"kind": "text", "lines": ["...", ...]}`.
    """
    items: list[dict] = []
    text_buf: list[str] = []
    in_mermaid = False
    in_other_fence = False
    mermaid_buf: list[str] = []
    last_h2: str = "Untitled section"

    def flush_text() -> None:
        if text_buf:
            items.append({"kind": "text", "lines": text_buf.copy()})
            text_buf.clear()

    for line in md_text.splitlines():
        if in_mermaid:
            if line.startswith("```"):
                items.append(
                    {"kind": "mermaid", "src": "\n".join(mermaid_buf) + "\n", "heading": last_h2}
                )
                mermaid_buf.clear()
                in_mermaid = False
            else:
                mermaid_buf.append(line)
            continue

        if in_other_fence:
            text_buf.append(line)
            if line.startswith("```"):
                in_other_fence = False
            continue

        fence_m = _FENCE_RE.match(line)
        if fence_m:
            if fence_m.group(1) == "mermaid":
                flush_text()
                in_mermaid = True
                continue
            text_buf.append(line)
            in_other_fence = True
            continue

        head_m = _HEADING_RE.match(line)
        if head_m and len(head_m.group(1)) == 2:
            # Strip leading numbering like "2. " / "10.1 " for caption use.
            raw = head_m.group(2).strip()
            last_h2 = re.sub(r"^(?:Appendix\s+[A-Z]\.\s+|\d+(?:\.\d+)*\.?\s+)", "", raw)
        text_buf.append(line)

    if in_mermaid:
        # Tolerate truncated source -- emit what we have.
        items.append({"kind": "mermaid", "src": "\n".join(mermaid_buf) + "\n", "heading": last_h2})
    flush_text()
    return items


def render_with_figures(md_text: str) -> tuple[str, list[dict]]:
    """Replace ```mermaid blocks with <figure>...<svg/>...<figcaption/></figure>
    and return (md_with_svgs, figure_list_metadata).

    figure_list_metadata is a list of {"idx": int, "caption": str,
    "anchor": str, "landscape": bool} suitable for rendering a List of
    Figures block.
    """
    items = _walk_blocks(md_text)
    out: list[str] = []
    figs: list[dict] = []
    fig_idx = 0
    for item in items:
        if item["kind"] == "text":
            out.append("\n".join(item["lines"]))
            continue
        # mermaid
        fig_idx += 1
        landscape = fig_idx in LANDSCAPE_FIGURES
        anchor = f"figure-{fig_idx}"
        caption = f"Figure {fig_idx}: {item['heading']}"
        print(
            f"  rendering diagram {fig_idx} ({len(item['src'])} chars, "
            f"{'landscape' if landscape else 'portrait'}, "
            f'caption="{caption}")...'
        )
        img = render_mermaid_block(item["src"], fig_idx, landscape=landscape)
        klass = "mermaid-figure landscape" if landscape else "mermaid-figure"
        out.append(
            f'\n\n<figure class="{klass}" id="{anchor}">\n'
            f'  <div class="mermaid-svg-wrap">{img}</div>\n'
            f"  <figcaption>{caption}</figcaption>\n"
            f"</figure>\n\n"
        )
        figs.append({"idx": fig_idx, "caption": caption, "anchor": anchor, "landscape": landscape})

    return "\n".join(out), figs


def md_to_html(md_text: str) -> str:
    """Convert markdown body to HTML with the extensions we need for a manual."""
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "codehilite",
            "attr_list",
            "def_list",
            "footnotes",
            "md_in_html",
            "sane_lists",
        ],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "codehilite"},
            "toc": {"toc_depth": "2-3", "anchorlink": False},
        },
    )
    return md.convert(md_text)


def build_lof(figs: list[dict]) -> str:
    """Render a List of Figures block. Mirrors the .toc visual style."""
    if not figs:
        return ""
    landscape_badge = ' <span class="lof-tag">landscape</span>'
    rows = "\n".join(
        f'    <li><a href="#{f["anchor"]}">{f["caption"]}</a>'
        f"{landscape_badge if f['landscape'] else ''}</li>"
        for f in figs
    )
    return f'<nav class="lof">\n  <ul>\n{rows}\n  </ul>\n</nav>\n'


def build_cover(version: str, date_str: str) -> str:
    return (
        COVER_TEMPLATE.read_text(encoding="utf-8")
        .replace("{{VERSION}}", version)
        .replace("{{DATE}}", date_str)
    )


def main() -> None:
    if not SOURCE.exists():
        sys.exit(f"missing source: {SOURCE}")
    if not STYLE.exists():
        sys.exit(f"missing style: {STYLE}")
    if not COVER_TEMPLATE.exists():
        sys.exit(f"missing cover template: {COVER_TEMPLATE}")

    if not PUPPETEER_CFG.exists():
        PUPPETEER_CFG.write_text(
            json.dumps(
                {
                    "executablePath": CHROME,
                    "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"reading {SOURCE.relative_to(REPO_ROOT)}")
    md_text = SOURCE.read_text(encoding="utf-8")

    version_match = re.search(r"<!--\s*VERSION:\s*([^\s]+)\s*-->", md_text)
    version = version_match.group(1) if version_match else "0.0.0"
    date_str = datetime.now(UTC).strftime("%d %B %Y")
    print(f"  version={version}  date={date_str}")

    print("rendering mermaid diagrams via mermaid-cli + system Chrome")
    md_with_svgs, figs = render_with_figures(md_text)
    print(
        f"  produced {len(figs)} figure(s); landscape: {sorted(LANDSCAPE_FIGURES & {f['idx'] for f in figs})}"
    )

    print("converting markdown -> HTML")
    body_html = md_to_html(md_with_svgs)

    # Insert the List of Figures right after the [TOC]'s rendered <div class="toc">.
    lof_html = build_lof(figs)
    if lof_html and 'class="toc"' in body_html:
        body_html = re.sub(
            r"(</div>\s*)(?=<h1|<h2|<p|<ul|<ol)", r"\1" + lof_html, body_html, count=1
        )

    print("composing print HTML")
    cover_html = build_cover(version, date_str)
    style_css = STYLE.read_text(encoding="utf-8")
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sigantry User Guide</title>
  <style>{style_css}</style>
</head>
<body>
{cover_html}
<main class="content">
{body_html}
</main>
</body>
</html>
"""

    print(f"rendering HTML -> {OUTPUT.relative_to(REPO_ROOT)} via WeasyPrint")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    weasyprint.HTML(string=html_doc, base_url=str(REPO_ROOT)).write_pdf(str(OUTPUT))

    size_kb = OUTPUT.stat().st_size // 1024
    print(f"done: {OUTPUT.relative_to(REPO_ROOT)}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
