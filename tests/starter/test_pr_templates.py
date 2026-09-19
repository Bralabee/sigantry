"""Plan 14-01 STARTER-04: dual PR templates rendered from a single source.

The single source is ``templates/starter/_partials/pr-checklist.md``.
Both ``templates/starter/.github/pull_request_template.md`` and
``templates/starter/.azuredevops/pull_request_template.md`` contain the
partial verbatim, demarcated by HTML-comment fences:

    <!-- pr-checklist:start -->
    ...
    <!-- pr-checklist:end -->

The fenced region is byte-identical between the two PR templates AND
byte-identical to the entire content of the partial (which is itself
the fenced block plus a trailing newline). The export script in Plan
14-07 adds a second guard against accidental drift in CI.
"""

from __future__ import annotations

from pathlib import Path

_STARTER_DIR = Path(__file__).resolve().parents[2] / "templates" / "starter"
_PARTIAL = _STARTER_DIR / "_partials" / "pr-checklist.md"
_GH_TEMPLATE = _STARTER_DIR / ".github" / "pull_request_template.md"
_ADO_TEMPLATE = _STARTER_DIR / ".azuredevops" / "pull_request_template.md"

_FENCE_START = b"<!-- pr-checklist:start -->"
_FENCE_END = b"<!-- pr-checklist:end -->"

_REQUIRED_CHECKLIST_ITEMS = (
    "Semantic-model changes (TMDL)",
    "Schema changes (Lakehouse / `.platform`)",
    "Variable-library changes",
    "Test evidence",
    "Deploy-record link",
)


def _extract_fenced_region(path: Path) -> bytes:
    """Return the byte slice between (and including) the fence markers.

    Uses the FIRST occurrence of each fence; the leading documentation
    comments in the PR templates deliberately avoid reproducing the
    fence-string literally so this extraction is unambiguous.
    """
    raw = path.read_bytes()
    start = raw.find(_FENCE_START)
    end = raw.find(_FENCE_END)
    assert start != -1, f"{path} missing start fence {_FENCE_START!r}"
    assert end != -1, f"{path} missing end fence {_FENCE_END!r}"
    return raw[start : end + len(_FENCE_END)]


def test_pr_templates_share_checklist_section() -> None:
    """Both PR templates contain the same checklist section, byte-for-byte (D-17)."""
    gh_fenced = _extract_fenced_region(_GH_TEMPLATE)
    ado_fenced = _extract_fenced_region(_ADO_TEMPLATE)
    assert gh_fenced == ado_fenced, (
        "GitHub and Azure DevOps PR templates have diverged inside the "
        "<!-- pr-checklist:start --> ... <!-- pr-checklist:end --> fences"
    )

    # The partial file's entire content IS the fenced block plus a trailing newline.
    partial_bytes = _PARTIAL.read_bytes()
    partial_fenced = _extract_fenced_region(_PARTIAL)
    assert gh_fenced == partial_fenced, (
        "PR template fenced region has drifted from _partials/pr-checklist.md"
    )
    assert partial_bytes.rstrip(b"\r\n") == partial_fenced.rstrip(b"\r\n"), (
        "_partials/pr-checklist.md should consist of the fenced block and "
        "nothing else (modulo trailing newline)"
    )


def test_github_pr_template_includes_required_checklist_items() -> None:
    """GitHub PR template includes TMDL diff link, lakehouse diff link, variable-library, test evidence, deploy-record link (D-17)."""
    body = _GH_TEMPLATE.read_text(encoding="utf-8")
    for item in _REQUIRED_CHECKLIST_ITEMS:
        assert item in body, f"GitHub PR template missing checklist item: {item!r}"


def test_azuredevops_pr_template_includes_required_checklist_items() -> None:
    """ADO PR template includes the same five checklist items as the GitHub template (D-17)."""
    body = _ADO_TEMPLATE.read_text(encoding="utf-8")
    for item in _REQUIRED_CHECKLIST_ITEMS:
        assert item in body, f"ADO PR template missing checklist item: {item!r}"
