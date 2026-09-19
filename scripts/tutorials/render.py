"""Render docs/tutorials/*.md into a single print-quality PDF.

The markdown files remain canonical -- this script reads them as-is
(index.md first, then the twelve numbered tutorials) and never modifies
them. Re-run after editing any tutorial and commit both.

Pipeline (same proven stack as scripts/userguide/render.py):

    docs/tutorials/{index,01..12}.md
       |
       v   concatenate in learning-path order; wrap each file in
       |   <section id="tut-NN"> so cross-tutorial links resolve as
       |   internal PDF anchors; rewrite NN-*.md links to #tut-NN and
       |   flatten links that point outside the tutorial set
       |
       +--> mermaid-cli (system Chrome) --> per-block PNG
       |     orientation is decided automatically per diagram: render
       |     portrait (1600px), parse the PNG header for width/height,
       |     and re-render at 2200px + tag landscape when the aspect
       |     ratio exceeds LANDSCAPE_ASPECT. (The user guide hardcodes
       |     its landscape set; tutorials get this measured instead.)
       |
       v   markdown -> HTML (markdown lib; toc extension supplies the
       |   heading anchors that the generated Contents page links to)
       |
       +--> WeasyPrint --> docs/Sigantry-Tutorials.pdf

PNG (not SVG) because mermaid 11.x emits <foreignObject>-wrapped HTML
labels that WeasyPrint cannot rasterise -- see the userguide renderer
docstring for the full rationale.

Run from repo root (project conda env, never base):

    conda run -n fabric-dataops-toolkits python scripts/tutorials/render.py
"""

from __future__ import annotations

import base64
import json
import re
import struct
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import markdown
import weasyprint

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIAL_DIR = REPO_ROOT / "docs" / "tutorials"
BASE_STYLE = REPO_ROOT / "scripts" / "userguide" / "style.css"
OVERRIDE_STYLE = Path(__file__).parent / "style.css"
COVER_TEMPLATE = Path(__file__).parent / "cover.html"
PUPPETEER_CFG = REPO_ROOT / "scripts" / "userguide" / "puppeteer-config.json"
VERSION_FILE = REPO_ROOT / "sigantry_core" / "_version.py"
OUTPUT = REPO_ROOT / "docs" / "Sigantry-Tutorials.pdf"
CHROME = "/usr/bin/google-chrome"

# A diagram whose rendered width exceeds this multiple of its height
# would squeeze its labels below legibility in the portrait text column
# (~174mm); rotate the page for it instead.
LANDSCAPE_ASPECT = 2.0

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


def ordered_sources() -> list[Path]:
    numbered = sorted(TUTORIAL_DIR.glob("[0-9][0-9]-*.md"))
    if not numbered:
        sys.exit(f"no numbered tutorials found under {TUTORIAL_DIR}")
    return [TUTORIAL_DIR / "index.md", *numbered]


def read_version() -> str:
    m = re.search(
        r"__version__\s*=\s*[\"']([^\"']+)[\"']", VERSION_FILE.read_text(encoding="utf-8")
    )
    return m.group(1) if m else "0.0.0"


def png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """Width/height from the IHDR chunk (always first, at byte 16)."""
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", png_bytes[16:24])
    return width, height


def run_mermaid_cli(mermaid_src: str, idx: int) -> bytes:
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
            # Tutorial diagrams render at their natural size (they never
            # fill a -w viewport), so resolution comes from the device
            # scale factor: 3x keeps labels crisp (~300 DPI) when the
            # PNG is stretched to the page column.
            "-s",
            "3",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"mermaid-cli failed for diagram {idx}: {result.stderr[-400:]}")
        return out_path.read_bytes()


def render_mermaid_block(mermaid_src: str, idx: int) -> tuple[str, bool]:
    """Render one mermaid block; return (<img> tag, landscape).

    Orientation is measured, not guessed: read the rendered PNG's
    intrinsic aspect ratio (scale-invariant) and rotate the page for
    diagrams too wide for the portrait column.
    """
    png_bytes = run_mermaid_cli(mermaid_src, idx)
    w, h = png_dimensions(png_bytes)
    landscape = (w / h) >= LANDSCAPE_ASPECT
    b64 = base64.b64encode(png_bytes).decode("ascii")
    img = f'<img class="mermaid-png" src="data:image/png;base64,{b64}" alt="diagram {idx}" />'
    print(f"    {w}x{h} (aspect {w / h:.2f}) -> {'landscape' if landscape else 'portrait'}")
    return img, landscape


_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")
_TUTORIAL_LINK_RE = re.compile(r"\((?:\./)?(\d{2})-[A-Za-z0-9-]+\.md(?:#[^)]*)?\)")
_INDEX_LINK_RE = re.compile(r"\((?:\./)?index\.md(?:#[^)]*)?\)")
_EXTERNAL_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!https?://|#)[^)]*\.md(?:#[^)]*)?\)")


def rewrite_links(md_text: str) -> str:
    """Point intra-tutorial links at PDF anchors; flatten links that
    leave the tutorial set (they cannot resolve inside a PDF)."""
    md_text = _TUTORIAL_LINK_RE.sub(r"(#tut-\1)", md_text)
    md_text = _INDEX_LINK_RE.sub("(#tut-index)", md_text)
    return _EXTERNAL_MD_LINK_RE.sub(r"\1", md_text)


def render_file(path: Path, fig_start: int) -> tuple[str, list[dict]]:
    """Render one tutorial file: mermaid blocks -> <figure> tags, links
    rewritten, the whole body wrapped in an anchored <section>.

    Returns (markdown_fragment, figure_metadata).
    """
    section_id = "tut-index" if path.name == "index.md" else f"tut-{path.name[:2]}"
    md_text = rewrite_links(path.read_text(encoding="utf-8"))

    title = "Tutorials"
    out: list[str] = []
    figs: list[dict] = []
    in_mermaid = False
    in_other_fence = False
    mermaid_buf: list[str] = []
    fig_idx = fig_start

    for line in md_text.splitlines():
        if in_mermaid:
            if line.startswith("```"):
                fig_idx += 1
                src = "\n".join(mermaid_buf) + "\n"
                print(f"  rendering figure {fig_idx} ({len(src)} chars) from {path.name}")
                img, landscape = render_mermaid_block(src, fig_idx)
                caption = f"Figure {fig_idx}: {title}"
                klass = "mermaid-figure landscape" if landscape else "mermaid-figure"
                anchor = f"figure-{fig_idx}"
                out.append(
                    f'\n<figure class="{klass}" id="{anchor}">\n'
                    f'  <div class="mermaid-svg-wrap">{img}</div>\n'
                    f"  <figcaption>{caption}</figcaption>\n"
                    f"</figure>\n"
                )
                figs.append(
                    {"idx": fig_idx, "caption": caption, "anchor": anchor, "landscape": landscape}
                )
                mermaid_buf.clear()
                in_mermaid = False
            else:
                mermaid_buf.append(line)
            continue

        if in_other_fence:
            out.append(line)
            if line.startswith("```"):
                in_other_fence = False
            continue

        fence_m = _FENCE_RE.match(line)
        if fence_m:
            if fence_m.group(1) == "mermaid":
                in_mermaid = True
                continue
            out.append(line)
            in_other_fence = True
            continue

        h1_m = _H1_RE.match(line)
        if h1_m:
            # Caption + running-header text: drop the "Tutorial NN -- " prefix.
            title = re.sub(r"^Tutorial\s+\d+\s+[—-]+\s+", "", h1_m.group(1))
        out.append(line)

    body = "\n".join(out)
    fragment = (
        f'<section class="tutorial" id="{section_id}" markdown="1">\n\n{body}\n\n</section>\n'
    )
    return fragment, figs


def md_to_html(md_text: str) -> tuple[str, list[dict]]:
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
            "toc": {"toc_depth": "1-2", "anchorlink": False},
        },
    )
    html = md.convert(md_text)
    return html, md.toc_tokens


def build_toc(toc_tokens: list[dict]) -> str:
    """Contents page from the markdown lib's heading tokens. Page
    numbers are resolved by WeasyPrint via target-counter (see the
    override stylesheet)."""
    rows: list[str] = []
    for h1 in toc_tokens:
        rows.append(f'    <li><a href="#{h1["id"]}">{h1["name"]}</a></li>')
        for h2 in h1.get("children", []):
            rows.append(f'      <li class="toc-sub"><a href="#{h2["id"]}">{h2["name"]}</a></li>')
    body = "\n".join(rows)
    return f'<nav class="toc">\n  <ul>\n{body}\n  </ul>\n</nav>\n'


def build_lof(figs: list[dict]) -> str:
    if not figs:
        return ""
    landscape_badge = ' <span class="lof-tag">landscape</span>'
    rows = "\n".join(
        f'    <li><a href="#{f["anchor"]}">{f["caption"]}</a>'
        f"{landscape_badge if f['landscape'] else ''}</li>"
        for f in figs
    )
    return f'<nav class="lof">\n  <ul>\n{rows}\n  </ul>\n</nav>\n'


def build_cover(version: str, date_str: str, n_tutorials: int) -> str:
    return (
        COVER_TEMPLATE.read_text(encoding="utf-8")
        .replace("{{VERSION}}", version)
        .replace("{{DATE}}", date_str)
        .replace("{{TUTORIALS}}", str(n_tutorials))
    )


def main() -> None:
    for required in (BASE_STYLE, OVERRIDE_STYLE, COVER_TEMPLATE, VERSION_FILE):
        if not required.exists():
            sys.exit(f"missing: {required}")
    if not PUPPETEER_CFG.exists():
        PUPPETEER_CFG.write_text(
            json.dumps(
                {"executablePath": CHROME, "args": ["--no-sandbox", "--disable-dev-shm-usage"]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    sources = ordered_sources()
    version = read_version()
    date_str = datetime.now(UTC).strftime("%d %B %Y")
    n_tutorials = len(sources) - 1
    print(f"version={version}  date={date_str}  files={len(sources)} ({n_tutorials} tutorials)")

    fragments: list[str] = []
    figs: list[dict] = []
    for src in sources:
        print(f"reading {src.relative_to(REPO_ROOT)}")
        fragment, file_figs = render_file(src, fig_start=len(figs))
        fragments.append(fragment)
        figs.extend(file_figs)
    landscape_idxs = sorted(f["idx"] for f in figs if f["landscape"])
    print(f"figures: {len(figs)} total; landscape: {landscape_idxs}")

    print("converting markdown -> HTML")
    body_html, toc_tokens = md_to_html("\n\n".join(fragments))

    print("composing print HTML")
    cover_html = build_cover(version, date_str, n_tutorials)
    toc_html = build_toc(toc_tokens)
    lof_html = build_lof(figs)
    style_css = (
        BASE_STYLE.read_text(encoding="utf-8")
        + "\n\n/* tutorials overrides */\n"
        + OVERRIDE_STYLE.read_text(encoding="utf-8")
    )
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sigantry Tutorials</title>
  <style>{style_css}</style>
</head>
<body>
{cover_html}
{toc_html}
{lof_html}
<main class="content">
{body_html}
</main>
</body>
</html>
"""

    print(f"rendering HTML -> {OUTPUT.relative_to(REPO_ROOT)} via WeasyPrint")
    weasyprint.HTML(string=html_doc, base_url=str(REPO_ROOT)).write_pdf(str(OUTPUT))
    size_kb = OUTPUT.stat().st_size // 1024
    print(f"done: {OUTPUT.relative_to(REPO_ROOT)}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
