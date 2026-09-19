# Work-Item Traceability -- Comment Rendering Runbook

**Phase 11 (TRACE-06).** Operator-facing reference for what a Sigantry
release-record comment looks like on Azure DevOps Work Items and on
GitHub Issues, and how to verify byte-equality across the two providers
during a live integration run.

## Overview

When `sigantry release record --provider <ado|github> ...` runs, it
posts a structured comment back to each linked work item or issue. The
comment text is intentionally a **fenced JSON code block** -- not
free-form Markdown. Both providers post the **byte-identical** comment
text (the TRACE-06 invariant): the unit-level golden test in
`tests/release/test_comment_parity.py` locks this contract. The two
systems render the block differently (ADO HTML-flavoured `<pre>`,
GitHub CommonMark `<pre><code>`), but the underlying text is the same.

## Comment payload schema

The fenced block contains the following keys (alphabetically sorted
because `json.dumps(sort_keys=True)` is invoked inside
`sigantry_core.workitems._payload.format_structured_comment`):

| Key | Type | Source |
|-----|------|--------|
| `approver` | `str` | `--approver` flag / `[release].approver` config |
| `audit_hash` | `str` (sha256 hex) | `DeployRecord.with_hash()` |
| `created_at` | `str` (ISO 8601 UTC, ms-truncated) | `datetime.now(UTC)` truncated to millisecond precision (Pitfall 8) |
| `fabric_items_changed_count` | `int` | `len(--fabric-items)` |
| `release_id` | `str` | `--release-id` flag |
| `test_evidence` | `dict[str, str]` | `--test-evidence` (JSON) |
| `workspace` | `str` | `--workspace` flag |

The fenced block is preceded by a single header line:
`Sigantry release record`.

## Example rendered comment

The following is a representative comment as it appears on either
provider after `sigantry release record` posts it (the inner JSON is
byte-identical on ADO and GitHub):

~~~text
Sigantry release record
```json
{
  "approver": "alice@example.invalid",
  "audit_hash": "<64-char-sha256-hex>",
  "created_at": "2026-04-26T12:00:00+00:00",
  "fabric_items_changed_count": 2,
  "release_id": "R-2026-04-26-1",
  "test_evidence": {
    "integration": "passed",
    "smoke": "passed"
  },
  "workspace": "ws-prod"
}
```
~~~

## Verifying byte-equality post-deploy

After a live integration run, the on-call operator can re-derive the
comment text from the `DeployRecord` and diff against what each provider
returned. The runtime invariant is that the inner JSON text (after
stripping the header + fence markers) is identical on both providers:

```python
from sigantry_core.workitems._payload import format_structured_comment

expected = format_structured_comment(record)
# Fetch the ADO comment text and assert ado_text == expected
# Fetch the GitHub comment body and assert github_body == expected
assert ado_text == github_body == expected
```

The unit-level invariant lives in `tests/release/test_comment_parity.py`;
the live invariant is exercised by
`tests/integration/workitems/test_live_ado_round_trip.py` and
`tests/integration/workitems/test_live_github_round_trip.py` (TRACE-08).

## Operator screenshot archive

After the live tests in `tests/integration/workitems/` run green, the
on-call operator captures screenshots of both rendered comments and
attaches them to the on-call notes for the week. Placeholder paths
(intentional -- the screenshots are NOT checked into the repo because
they leak tenant-specific UI):

- `docs/runbooks/work-item-traceability/screenshots/ado-rendering.png`
- `docs/runbooks/work-item-traceability/screenshots/github-rendering.png`

The runbook records that the artefacts exist somewhere accessible (the
on-call drive); it does not embed them.

## Triage: what to check when parity drifts

If the parity test (`tests/release/test_comment_parity.py`) starts
failing on a PR, walk through these in order:

1. **Did someone modify `format_structured_comment`?** Both providers
   funnel through that single function. Any drift in the payload keys,
   sort order, or fence syntax will break parity.
2. **Did someone add a provider-specific field on one side?** A reader
   on the GitHub provider (e.g. an `emoji` field) without the matching
   reader on the ADO provider will diverge. Both providers must call
   `format_structured_comment(record)` unmodified.
3. **Did the snapshot fixture move?** Golden fixtures live under
   `tests/release/fixtures/comment-parity/` -- a stale snapshot from a
   pre-Pitfall-8 millisecond truncation would diverge.

## Related

- [ADR-0011 -- Rename to Sigantry](../../decisions/ADR-0011-rename-to-sigantry.md)
- [Protocols reference -- WorkItemProvider](../../reference/protocols.md)
- [Observation planes (non-pluggable audit)](../../reference/observation-planes.md)
