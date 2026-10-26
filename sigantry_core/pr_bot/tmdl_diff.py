"""Pure-Python TMDL parser + ``diff_tmdl`` for STARTER-05 (Plan 14-03).

Walks two directory trees of ``*.tmdl`` files (PR base + PR head),
parses every file with a line-based indentation-aware parser, and
returns a :class:`sigantry_core.pr_bot.payload.TmdlDiffSection`
populated with table / column / measure / relationship adds, removes,
and modifications.

Design constraints (CONTEXT.md D-08/09/10 + RESEARCH §Pattern 1):

- Pure stdlib only; no third-party ``tmdl`` PyPI dep, no
  ``fabric`` deploy-engine import (which would pull in .NET-bridge
  bindings on Windows-only), no direct HTTP-client import (banned-API
  gate). Cross-platform.
- Shallow diff: byte-different bodies surface as "modified"; reviewer
  adjudicates DAX equivalence. DAX text changes AND description-only
  changes both classify as modified per RESEARCH Open Q #2.
- Iterative parser. The 1MB / 256KB byte caps (RESEARCH §Threat
  patterns) raise :class:`TmdlParseError` rather than stack-overflow on
  pathological input.

TMDL spec edge cases (Microsoft Learn TMDL syntax doc):

- ``///`` description blocks bind to the *following* object declaration
  (Pitfall 6); description-only edits surface as ``modified``.
- ``'It''s a sale'`` is ONE name (Pitfall 7); outer ``'`` delimits, ``''``
  escapes a literal single quote. Verbatim quoted form preserved in
  :attr:`TmdlBlock.name`.
- Indentation drives nesting: a decl at indent N opens a block whose
  body absorbs subsequent lines at indent > N until indent drops to <= N.
- Indentation is tabs or spaces. The serializer emits one tab per level
  ("A TMDL document uses a default single tab indentation rule", Learn,
  TMDL overview, Indentation); hand-written files commonly use four
  spaces. A tab is measured as :data:`_TAB_WIDTH` columns so both forms,
  and a file mixing them at four columns per level, nest the same way.
- Inside a ``` expression fence (opened only by ``= ```` ending a line) the
  indentation rules are off: lines are taken verbatim until the closing
  fence, flush-left or not. A fence that never closes raises.
- A bare (unquoted) name is identifier- or GUID-shaped (word characters and
  hyphens); relationships are named by bare, often digit-led, GUID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sigantry_core.pr_bot.payload import (
    MeasureChange,
    RelationshipChange,
    TableChange,
    TmdlDiffSection,
)

_MAX_FILE_BYTES: Final[int] = 1_048_576
"""1 MB per-file cap (RESEARCH §Threat patterns)."""

_MAX_BLOCK_BYTES: Final[int] = 262_144
"""256 KB per-block body cap. Iterative parser raises before unbounded growth."""

_BLOCK_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "model",
        "database",
        "table",
        "measure",
        "column",
        "relationship",
        "partition",
        "role",
        "perspective",
        "calculationGroup",
        "calculationItem",
        "hierarchy",
        "expression",
    }
)
"""Block-decl keywords (RESEARCH §Pattern 1 + Wave 2 TMDL completeness surface)."""

_TABLE_SUBBLOCK_KINDS: Final[frozenset[str]] = frozenset(
    {"partition", "hierarchy", "calculationGroup", "calculationItem"}
)
"""Child block kinds nested under a ``table`` whose edits mark the parent table modified."""

# Name token: single-quoted with `''` escape, OR bare and identifier- or
# GUID-shaped: word characters and hyphens. Exports name relationships by bare
# GUID (`8f3c0a2e-5b1d-...`), digit-led and hyphenated. The spec allows any bare
# name free of `. = : '` and whitespace, but that wide a pattern turned M steps
# inside a partition source (`column #"x" = 1`) into declarations.
_NAME_RE: Final[str] = r"(?:'(?:[^']|'')*'|\w[\w-]*)"
_BLOCK_DECL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<kind>"
    + "|".join(sorted(_BLOCK_KEYWORDS))
    + r")\b\s+(?P<name>"
    + _NAME_RE
    + r")(?P<rest>.*)$"
)
"""Compiled at module import: keyword + first name token of a block decl."""

# Best-effort regex over a relationship body to extract endpoints.
# Falls back to "?" rather than raising; the diff still reports a
# modification, just without precise endpoints.
_REL_FROM_RE: Final[re.Pattern[str]] = re.compile(
    r"\bfromColumn\s*:\s*(?P<table>'(?:[^']|'')*'|[^'.\s]+(?:\.[^'.\s]+)*)\.",
    re.IGNORECASE,
)
_REL_TO_RE: Final[re.Pattern[str]] = re.compile(
    r"\btoColumn\s*:\s*(?P<table>'(?:[^']|'')*'|[^'.\s]+(?:\.[^'.\s]+)*)\.",
    re.IGNORECASE,
)


class TmdlParseError(ValueError):
    """Raised on size-cap breach or unrecoverable indentation.

    Per RESEARCH §Threat T-14-03-03 the message includes the source path
    + the cap value, but NEVER the file content -- prevents leaking
    adopter-controlled bytes into shared CI logs.
    """


@dataclass(frozen=True, slots=True)
class TmdlBlock:
    """One parsed TMDL block (model / table / measure / column / ...).

    ``body`` spans the decl line + every indented continuation line +
    any leading ``///`` description block that bound to this object.
    ``parent_table`` is the most recent enclosing ``table`` block's name
    (verbatim quoted form when quoted), or ``None`` for top-level.
    """

    kind: str
    name: str
    parent_table: str | None
    body: str
    line_start: int


_TAB_WIDTH: Final[int] = 4
"""Columns one tab advances to when measuring indentation (see module docstring)."""

_FENCE: Final[str] = "```"
"""Expression fence delimiter; the closing one stands on a line of its own."""

_FENCE_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"=\s*```\s*$")
"""A fence opens only as ``= ```` ending a decl or property line (spec, Expressions)."""


def _opens_fence(content: str) -> bool:
    """True when an (un-indented) line opens a ``` expression fence.

    Per the spec (TMDL overview, Expressions) the fence follows the ``=``
    on the same line and the closing fence stands alone; inside it the
    indentation rules do not apply, so the parser must not let a flush-left
    expression line close the open block. A value that merely ends in
    ``` (a one-line ```` ```1``` ````, or ``description: see ````) opens nothing.
    """
    return _FENCE_OPEN_RE.search(content) is not None


def _split_indent(line: str) -> tuple[int, str]:
    """Return ``(width, rest)``: the indentation width in columns and the line after it.

    Width counts leading spaces and tabs, a tab advancing to the next
    multiple of :data:`_TAB_WIDTH`. Only relative width matters to the
    parser: a child is any line wider than its parent's declaration.
    """
    rest = line.lstrip(" \t")
    return len(line[: len(line) - len(rest)].expandtabs(_TAB_WIDTH)), rest


def parse(text: str, source: str = "<string>") -> list[TmdlBlock]:
    """Parse a TMDL document into a flat list of blocks in document order.

    Iterative + indent-stack-driven. ``///`` description lines accumulate
    into a pending buffer that gets prepended to the body of the next
    block declaration. Bare ``//`` comment lines are kept in the active
    block's body verbatim so a comment-only edit also surfaces as
    ``modified``.
    """
    if len(text.encode("utf-8")) > _MAX_FILE_BYTES:
        raise TmdlParseError(f"{source}: file exceeds {_MAX_FILE_BYTES} bytes")

    blocks: list[TmdlBlock] = []
    # Stack of (decl_indent, blocks_index, body_lines_buffer).
    stack: list[tuple[int, int, list[str]]] = []
    pending_desc: list[str] = []
    table_stack: list[str] = []  # parent_table tracking
    in_fence = False  # inside a ``` expression: indentation rules are off
    fence_line = 0

    def _close_to(indent: int) -> None:
        """Close every open block whose decl-indent >= ``indent``."""
        while stack and stack[-1][0] >= indent:
            _decl_indent, idx, body_lines = stack.pop()
            old = blocks[idx]
            body = "\n".join(body_lines)
            if len(body.encode("utf-8")) > _MAX_BLOCK_BYTES:
                raise TmdlParseError(
                    f"{source}: block '{old.name}' body exceeds {_MAX_BLOCK_BYTES} bytes"
                )
            blocks[idx] = TmdlBlock(
                kind=old.kind,
                name=old.name,
                parent_table=old.parent_table,
                body=body,
                line_start=old.line_start,
            )
            if old.kind == "table" and table_stack and table_stack[-1] == old.name:
                table_stack.pop()

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if in_fence:
            # Verbatim until the closing delimiter, whatever the indentation.
            if stack:
                stack[-1][2].append(raw)
            if raw.strip() == _FENCE:
                in_fence = False
            continue

        if not raw.strip():
            if stack:
                stack[-1][2].append(raw)
            continue

        indent, stripped = _split_indent(raw)

        # `///` binds to the next block; never appended to an open block.
        if stripped.startswith("///"):
            pending_desc.append(raw)
            continue

        # Close any blocks whose decl-indent >= our indent (sibling or
        # outdented; the previous block is finished).
        _close_to(indent)

        m = _BLOCK_DECL_RE.match(stripped)
        if m:
            kind = m.group("kind")
            name = m.group("name")
            parent = table_stack[-1] if table_stack else None
            body_lines: list[str] = []
            if pending_desc:
                body_lines.extend(pending_desc)
                pending_desc = []
            body_lines.append(raw)
            blocks.append(
                TmdlBlock(
                    kind=kind,
                    name=name,
                    parent_table=parent,
                    body="",  # filled in on close
                    line_start=lineno,
                )
            )
            stack.append((indent, len(blocks) - 1, body_lines))
            if kind == "table":
                table_stack.append(name)
        elif stack:
            # Non-decl line: append to innermost open block's body if any.
            if pending_desc:
                stack[-1][2].extend(pending_desc)
                pending_desc = []
            stack[-1][2].append(raw)
        else:
            pending_desc = []
        if _opens_fence(stripped):
            in_fence = True
            fence_line = lineno

    if in_fence:
        raise TmdlParseError(f"{source}: expression fence opened at line {fence_line} never closes")
    _close_to(-1)
    return blocks


def _semantic_model_stem(file_path: Path, root: Path) -> str | None:
    """Return the enclosing ``<stem>.SemanticModel`` stem for ``file_path`` under ``root``."""
    try:
        rel_parts = file_path.relative_to(root).parts
    except ValueError:
        rel_parts = file_path.parts
    for part in rel_parts[:-1]:
        if part.endswith(".SemanticModel") and len(part) > len(".SemanticModel"):
            return part[: -len(".SemanticModel")]
    return None


def _discover_model_stems(root: Path) -> set[str]:
    """Return all ``*.SemanticModel`` stems that contain ``*.tmdl`` files under ``root``."""
    if not root.exists():
        return set()
    stems: set[str] = set()
    for f in sorted(root.rglob("*.tmdl")):
        if not f.is_file():
            continue
        stem = _semantic_model_stem(f, root)
        if stem:
            stems.add(stem)
    return stems


def _walk_blocks(
    root: Path,
    *,
    qualify_models: bool | None = None,
) -> dict[tuple[str | None, str, str], str]:
    """Parse every ``*.tmdl`` under ``root`` and key blocks by triple.

    Key: ``(parent_table, kind, name)``. Value: block body (str).
    When multiple ``*.SemanticModel`` directories exist (or ``qualify_models=True``),
    top-level block names and ``parent_table`` values are prefixed with
    ``<model_stem>/`` so identically named tables/measures across models do
    not overwrite each other.
    """
    out: dict[tuple[str | None, str, str], str] = {}
    if not root.exists():
        return out
    tmdl_files = [f for f in sorted(root.rglob("*.tmdl")) if f.is_file()]
    if qualify_models is None:
        stems = {stem for f in tmdl_files if (stem := _semantic_model_stem(f, root)) is not None}
        qualify_models = len(stems) > 1
    for f in tmdl_files:
        stem = _semantic_model_stem(f, root) if qualify_models else None
        text = f.read_text(encoding="utf-8")
        for blk in parse(text, source=str(f)):
            if stem:
                if blk.parent_table is not None:
                    parent = f"{stem}/{blk.parent_table}"
                    name = blk.name
                else:
                    parent = None
                    name = f"{stem}/{blk.name}"
            else:
                parent = blk.parent_table
                name = blk.name
            out[(parent, blk.kind, name)] = blk.body
    return out


def _parse_relationship_endpoints(body: str) -> tuple[str, str]:
    """Best-effort extraction of (from_table, to_table); ``"?"`` on miss."""
    m_from = _REL_FROM_RE.search(body)
    m_to = _REL_TO_RE.search(body)
    return (
        m_from.group("table") if m_from else "?",
        m_to.group("table") if m_to else "?",
    )


def diff_tmdl(base_dir: Path, head_dir: Path) -> TmdlDiffSection:
    """Diff two trees of TMDL files into a :class:`TmdlDiffSection`.

    Walks ``*.tmdl`` files in both trees, parses each, and computes set
    differences over the ``(parent_table, kind, name)`` keyspace. Blocks
    present in both with byte-different bodies surface as ``*_modified``
    -- including description-only changes (per Open Q #2 and the
    description-block binding rule).
    """
    base_dir = Path(base_dir)
    head_dir = Path(head_dir)
    all_models = _discover_model_stems(base_dir) | _discover_model_stems(head_dir)
    qualify_models = len(all_models) > 1
    base = _walk_blocks(base_dir, qualify_models=qualify_models)
    head = _walk_blocks(head_dir, qualify_models=qualify_models)

    base_keys = set(base.keys())
    head_keys = set(head.keys())
    added_keys = head_keys - base_keys
    removed_keys = base_keys - head_keys
    common_keys = base_keys & head_keys
    modified_keys = {k for k in common_keys if base[k] != head[k]}

    tables_added = sorted(n for (p, k, n) in added_keys if k == "table" and p is None)
    tables_removed = sorted(n for (p, k, n) in removed_keys if k == "table" and p is None)
    subblock_modified_tables = {
        p
        for (p, k, _n) in (added_keys | removed_keys | modified_keys)
        if p is not None and k in _TABLE_SUBBLOCK_KINDS and (None, "table", p) in common_keys
    }
    tables_modified = sorted(
        {n for (p, k, n) in modified_keys if k == "table" and p is None} | subblock_modified_tables
    )

    def _cols(keys: set[tuple[str | None, str, str]]) -> list[TableChange]:
        return sorted(
            (TableChange(table=p or "", name=n) for (p, k, n) in keys if k == "column"),
            key=lambda c: (c.table, c.name),
        )

    def _meas(keys: set[tuple[str | None, str, str]]) -> list[MeasureChange]:
        return sorted(
            (MeasureChange(table=p or "", name=n) for (p, k, n) in keys if k == "measure"),
            key=lambda m: (m.table, m.name),
        )

    def _top_names(keys: set[tuple[str | None, str, str]], kind: str) -> list[str]:
        return sorted(n for (p, k, n) in keys if k == kind and p is None)

    columns_added = _cols(added_keys)
    columns_removed = _cols(removed_keys)
    columns_modified = _cols(modified_keys)
    measures_added = _meas(added_keys)
    measures_removed = _meas(removed_keys)
    measures_modified = _meas(modified_keys)

    relationships_added: list[RelationshipChange] = []
    for key in sorted(k for k in added_keys if k[1] == "relationship"):
        ft, tt = _parse_relationship_endpoints(head[key])
        relationships_added.append(RelationshipChange(from_table=ft, to_table=tt))
    relationships_removed: list[RelationshipChange] = []
    for key in sorted(k for k in removed_keys if k[1] == "relationship"):
        ft, tt = _parse_relationship_endpoints(base[key])
        relationships_removed.append(RelationshipChange(from_table=ft, to_table=tt))
    relationships_modified: list[RelationshipChange] = []
    for key in sorted(k for k in modified_keys if k[1] == "relationship"):
        ft, tt = _parse_relationship_endpoints(head[key])
        relationships_modified.append(RelationshipChange(from_table=ft, to_table=tt))

    return TmdlDiffSection(
        tables_added=tables_added,
        tables_removed=tables_removed,
        tables_modified=tables_modified,
        columns_added=columns_added,
        columns_removed=columns_removed,
        columns_modified=columns_modified,
        measures_added=measures_added,
        measures_removed=measures_removed,
        measures_modified=measures_modified,
        relationships_added=relationships_added,
        relationships_removed=relationships_removed,
        relationships_modified=relationships_modified,
        roles_added=_top_names(added_keys, "role"),
        roles_removed=_top_names(removed_keys, "role"),
        roles_modified=_top_names(modified_keys, "role"),
        perspectives_added=_top_names(added_keys, "perspective"),
        perspectives_removed=_top_names(removed_keys, "perspective"),
        perspectives_modified=_top_names(modified_keys, "perspective"),
        expressions_added=_top_names(added_keys, "expression"),
        expressions_removed=_top_names(removed_keys, "expression"),
        expressions_modified=_top_names(modified_keys, "expression"),
    )


__all__ = [
    "TmdlBlock",
    "TmdlParseError",
    "diff_tmdl",
    "parse",
]
