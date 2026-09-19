"""Structured-comment payload formatter -- single source of truth for TRACE-06.

Both ADO (Plan 11-04) and GitHub (Plan 11-05) providers MUST call
:func:`format_structured_comment` with the same :class:`DeployRecord`;
both emit byte-identical comment text. The TRACE-06 parity test in
``tests/release/test_comment_parity.py`` enforces this by diffing the
runtime output against a checked-in golden snapshot AND by diffing the
two providers' captured outputs against each other.

The payload is a fenced JSON code block (NOT free-form markdown):

- ADO renders fenced blocks faithfully (HTML-flavoured pre/code).
- GitHub renders them as CommonMark ``<pre><code>``.
- The TEXT content is byte-identical regardless of how each side renders.

Source: 11-RESEARCH.md Pattern 5 (line 374-388).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from sigantry_core.release.record import DeployRecord


_HEADER: Final[str] = "Sigantry release record"


def build_comment_payload(record: DeployRecord) -> dict[str, Any]:
    """Return the dict that gets serialised into the fenced JSON block.

    Exposed separately so tests can assert payload shape without parsing
    the markdown around it. The dict is intentionally lossy: it carries
    the *count* of fabric items changed, not the raw item paths -- that
    keeps Fabric workspace structure out of the work-item tracker.
    """
    return {
        "release_id": record.release_id,
        "audit_hash": record.audit_hash,
        "workspace": record.workspace,
        "fabric_items_changed_count": len(record.fabric_items_changed),
        "test_evidence": dict(record.test_evidence),
        "approver": record.approver,
        "created_at": record.created_at.isoformat(),
    }


def format_structured_comment(record: DeployRecord) -> str:
    """Format the DeployRecord as the byte-identical comment text.

    BOTH ``AdoWorkItemProvider`` AND ``GithubWorkItemProvider`` MUST call
    this function unchanged. The TRACE-06 parity test asserts both
    providers post the IDENTICAL string -- divergence becomes impossible
    by construction.

    The output structure is::

        Sigantry release record
        ```json
        <pretty-printed JSON, sorted keys, 2-space indent>
        ```

    ``json.dumps`` escapes any embedded backticks or markdown control
    characters in the structured fields, so a malicious commit message in
    ``release_id`` cannot break out of the fenced block.
    """
    payload = build_comment_payload(record)
    body = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
    return f"{_HEADER}\n```json\n{body}\n```"
