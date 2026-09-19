"""PrCommentPayload pydantic v2 models + byte-stable Markdown renderer (STARTER-07).

This module is the single source of truth for the PR-review bot's comment
contract. ``render_markdown(payload)`` produces a byte-identical string
that BOTH ``GithubProvider.post_comment`` and ``AdoProvider.post_comment``
(Plan 14-05) post unchanged. The cross-provider POST-body parity test
(Plan 14-05) closes STARTER-07 end-to-end; this module closes the
renderer half via the four-scenario byte-snapshot tests in
``tests/sigantry_core/pr_bot/test_payload_parity.py``.

Determinism discipline (mirrored verbatim from ``sigantry_core.release.record``):

- ``audit_hash`` is SHA-256 over a canonical JSON serialisation
  (``sort_keys=True``, ``separators=(',', ':')``, ``ensure_ascii=False``)
  of every field except the hash itself.
- The model is frozen (``ConfigDict(frozen=True)``) and rejects extra
  fields (``extra="forbid"``).
- ``audit_hash`` MUST be populated via ``.with_hash()`` rather than
  passed to the constructor. The constructor accepts a seed value for
  ergonomics (e.g. JSON round-tripping); ``.with_hash()`` always
  overwrites it.
- Per RESEARCH Open Question #3 the payload deliberately carries NO
  ``created_at`` / timestamp field so re-runs of the bot on the same PR
  commit produce identical comments. If a future plan adds such a field
  it MUST be popped from :meth:`PrCommentPayload.canonical_payload`.

Markdown rendering discipline:

- ``render_markdown`` is a pure function: same input -> same output, no
  ``time.now()`` / ``random`` / ``os.environ`` reads.
- Section order is fixed: header, summary table, TMDL diff (when
  non-empty), Lakehouse diff (when non-None), no-changes line (when
  both diff sections empty/None), audit-hash trailer.
- Embedded JSON inside diff fences uses
  ``json.dumps(..., sort_keys=True, ensure_ascii=False, indent=2)``.
- Diff fences use **tilde** (` ~~~json `) instead of backticks --
  defence-in-depth against Markdown-injection from a TMDL block name
  containing a triple-backtick (RESEARCH §Threat patterns); pydantic's
  frozen models + ``json.dumps(ensure_ascii=False)`` already escape
  control characters within string values.
- The Lakehouse section ALWAYS ends with the fixed footer
  :data:`_LAKEHOUSE_COLUMN_FOOTER` whenever ``lakehouse_diff is not None``,
  per RESEARCH §Critical Finding (Microsoft Fabric does not track
  Lakehouse table column types in Git).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

_CANONICAL_KWARGS: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}
"""Canonical-JSON kwargs -- identical to ``sigantry_core.release.record``.

Both modules MUST stay byte-equivalent: any drift in canonicalisation
breaks audit-hash compatibility across the audit plane and the PR-bot
comment plane.
"""

_HEADER: Final[str] = "Sigantry PR review"
"""Top-of-comment header line. Mirrored across ADO and GitHub providers."""

_SCHEMA_VERSION: Final[str] = "1.0"
"""Schema version literal pinned on PrCommentPayload + diff sections."""

_LAKEHOUSE_COLUMN_FOOTER: Final[str] = (
    "Note: Microsoft Fabric does not track Lakehouse table column types in Git. "
    "Column-level changes happen at runtime against OneLake and surface via "
    "`sigantry diff` against a live workspace, not via PR review."
)
"""Fixed reviewer-facing footer for every Lakehouse section.

Source: 14-RESEARCH.md §Critical Finding. Captured in 2/4 byte-snapshot
fixtures (lakehouse-only.md + both.md) so it cannot drift silently.
"""

_NO_CHANGES_LINE: Final[str] = "> No semantic-model or schema changes detected in this PR."
"""Rendered when both ``tmdl_diff`` and ``lakehouse_diff`` are absent / empty.

Mirrors D-07 ('No changes detected' still posts a comment; never silent).
"""


# ---------------------------------------------------------------------------
# Nested value-objects
# ---------------------------------------------------------------------------


class PrSummary(BaseModel):
    """Summary block at the top of every PR-bot comment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pr_id: str
    provider: str
    base_sha: str
    head_sha: str
    changed_files_count: int


class TableChange(BaseModel):
    """A column add/remove keyed by the (table, column-name) pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    name: str


class MeasureChange(BaseModel):
    """A measure add/remove/modify keyed by the (table, measure-name) pair.

    Kept distinct from :class:`TableChange` for clarity at call sites even
    though the schema is identical.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    name: str


class RelationshipChange(BaseModel):
    """A TMDL relationship add/remove keyed by (from-table, to-table)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_table: str
    to_table: str


class IdentityChange(BaseModel):
    """An identity-field change in a Lakehouse ``.platform`` file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    before: str
    after: str


class SchemaToggleChange(BaseModel):
    """A flip of the ``defaultSchema`` flag in ``lakehouse.metadata.json``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    before: str
    after: str


class ShortcutChange(BaseModel):
    """A OneLake shortcut add / remove / modify."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str
    target_path: str | None = None


class RoleChange(BaseModel):
    """A data-access role add / remove / modify."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    change: Literal["added", "removed", "modified"]


# ---------------------------------------------------------------------------
# Diff-section models
# ---------------------------------------------------------------------------


class TmdlDiffSection(BaseModel):
    """TMDL semantic-model diff section.

    Plan 14-03's ``tmdl_diff.diff(base_dir, head_dir)`` populates this
    section. Plan 14-02 lands the pydantic skeleton so the renderer +
    snapshot tests can be written before the parser ships.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    tables_added: list[str] = Field(default_factory=list)
    tables_removed: list[str] = Field(default_factory=list)
    tables_modified: list[str] = Field(default_factory=list)
    columns_added: list[TableChange] = Field(default_factory=list)
    columns_removed: list[TableChange] = Field(default_factory=list)
    measures_added: list[MeasureChange] = Field(default_factory=list)
    measures_removed: list[MeasureChange] = Field(default_factory=list)
    measures_modified: list[MeasureChange] = Field(default_factory=list)
    relationships_added: list[RelationshipChange] = Field(default_factory=list)
    relationships_removed: list[RelationshipChange] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """True iff every collection is empty (no diff to render)."""
        return not (
            self.tables_added
            or self.tables_removed
            or self.tables_modified
            or self.columns_added
            or self.columns_removed
            or self.measures_added
            or self.measures_removed
            or self.measures_modified
            or self.relationships_added
            or self.relationships_removed
        )


class LakehouseDiffSection(BaseModel):
    """Lakehouse metadata-file diff section.

    Diffs ``.platform``, ``lakehouse.metadata.json``,
    ``shortcuts.metadata.json``, ``data-access-roles.json``. Per
    RESEARCH §Critical Finding the bot does NOT diff column-level table
    schema -- Microsoft Fabric does not track it in Git. The fixed
    footer in :data:`_LAKEHOUSE_COLUMN_FOOTER` warns reviewers
    accordingly and is rendered every time this section is non-None.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    identity_changes: list[IdentityChange] = Field(default_factory=list)
    schema_toggle: SchemaToggleChange | None = None
    tracked_tables_added: list[str] = Field(default_factory=list)
    tracked_tables_removed: list[str] = Field(default_factory=list)
    shortcuts_added: list[ShortcutChange] = Field(default_factory=list)
    shortcuts_removed: list[ShortcutChange] = Field(default_factory=list)
    shortcuts_modified: list[ShortcutChange] = Field(default_factory=list)
    role_changes: list[RoleChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """True iff every collection is empty AND ``schema_toggle is None``."""
        return not (
            self.identity_changes
            or self.schema_toggle is not None
            or self.tracked_tables_added
            or self.tracked_tables_removed
            or self.shortcuts_added
            or self.shortcuts_removed
            or self.shortcuts_modified
            or self.role_changes
            or self.warnings
        )


# ---------------------------------------------------------------------------
# Top-level payload
# ---------------------------------------------------------------------------


class PrCommentPayload(BaseModel):
    """The single payload object the PR-bot posts to ADO + GitHub.

    See module docstring for invariants. Both providers MUST route their
    comment text through :func:`render_markdown` so the bytes match
    exactly (STARTER-07).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    header: str = _HEADER
    summary: PrSummary
    tmdl_diff: TmdlDiffSection | None = None
    lakehouse_diff: LakehouseDiffSection | None = None
    footer: str = ""
    audit_hash: str = ""

    def canonical_payload(self) -> bytes:
        """JSON canonicalisation of every field except ``audit_hash``.

        Output is UTF-8 bytes with sorted keys and minimal separators --
        identical to the input fed into :func:`hashlib.sha256` by both
        :meth:`with_hash` and :meth:`verify_hash`.

        If a future plan adds a non-deterministic field
        (e.g. ``created_at``) it MUST be popped here as well.
        """
        d = self.model_dump(mode="json")
        d.pop("audit_hash", None)
        return json.dumps(d, **_CANONICAL_KWARGS).encode("utf-8")

    def with_hash(self) -> PrCommentPayload:
        """Return a copy with ``audit_hash`` recomputed from the payload.

        Always overwrites any constructor-supplied seed value -- calling
        ``.with_hash()`` on a payload that already carries an
        ``audit_hash`` is the canonical re-canonicalisation path.
        """
        digest = hashlib.sha256(self.canonical_payload()).hexdigest()
        return self.model_copy(update={"audit_hash": digest})

    def verify_hash(self) -> bool:
        """Re-derive the hash and compare against the stored value."""
        recomputed = hashlib.sha256(self.canonical_payload()).hexdigest()
        return recomputed == self.audit_hash


def compute_audit_hash(payload: PrCommentPayload) -> str:
    """Module-level helper -- SHA-256 hex digest over canonical payload JSON.

    Re-exported alongside :meth:`PrCommentPayload.with_hash` for callers
    (e.g. Plan 14-06's CLI orchestration) that need the digest before
    finalising a payload.
    """
    return hashlib.sha256(payload.canonical_payload()).hexdigest()


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_summary_table(summary: PrSummary) -> list[str]:
    """Render the top-of-comment summary table (5 rows, fixed order)."""
    return [
        "## Summary",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| PR | {summary.pr_id} |",
        f"| Provider | {summary.provider} |",
        f"| Base | {summary.base_sha} |",
        f"| Head | {summary.head_sha} |",
        f"| Changed files | {summary.changed_files_count} |",
    ]


def _render_diff_block(heading: str, diff_model: BaseModel) -> list[str]:
    """Render a fenced JSON block for a diff section.

    Tilde-fenced (` ~~~json `) so a TMDL block-name carrying triple
    backticks cannot break out of the fenced block. ``ensure_ascii=False``
    keeps human-readable Unicode while still JSON-escaping control
    characters within string values.
    """
    body = json.dumps(
        diff_model.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
    )
    return [
        f"## {heading}",
        "",
        "~~~json",
        body,
        "~~~",
    ]


def render_markdown(payload: PrCommentPayload) -> str:
    """Render ``payload`` as the byte-identical PR-bot comment text.

    Pure function: no ``time.now()``, no ``random``, no ``os.environ``,
    no I/O. Same input -> same output, every time. The four byte-snapshot
    fixtures under ``tests/fixtures/pr-bot/snapshots/`` lock the output
    bytes; any drift in this function surfaces as a snapshot diff in CI.
    """
    lines: list[str] = [payload.header, ""]
    lines.extend(_render_summary_table(payload.summary))
    lines.append("")

    tmdl_present = payload.tmdl_diff is not None and not payload.tmdl_diff.is_empty()
    lakehouse_present = payload.lakehouse_diff is not None

    if tmdl_present:
        # Type-checker hint: tmdl_present already proved this is non-None.
        assert payload.tmdl_diff is not None
        lines.extend(_render_diff_block("TMDL diff", payload.tmdl_diff))
        lines.append("")

    if lakehouse_present:
        assert payload.lakehouse_diff is not None
        lines.extend(_render_diff_block("Lakehouse diff", payload.lakehouse_diff))
        lines.append("")
        lines.append(_LAKEHOUSE_COLUMN_FOOTER)
        lines.append("")

    if not tmdl_present and not lakehouse_present:
        lines.append(_NO_CHANGES_LINE)
        lines.append("")

    if payload.footer:
        lines.append(payload.footer)
        lines.append("")

    lines.append("---")
    lines.append(f"audit_hash: `{payload.audit_hash}`")

    return "\n".join(lines)


__all__ = [
    "IdentityChange",
    "LakehouseDiffSection",
    "MeasureChange",
    "PrCommentPayload",
    "PrSummary",
    "RelationshipChange",
    "RoleChange",
    "SchemaToggleChange",
    "ShortcutChange",
    "TableChange",
    "TmdlDiffSection",
    "compute_audit_hash",
    "render_markdown",
]
