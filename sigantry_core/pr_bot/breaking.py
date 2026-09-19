"""TMDL Semantic Model Breaking Change Impact Guard.

Analyzes TMDL diff sections for dropped tables, columns, measures, and relationships
that break downstream Power BI reports, DAX expressions, or scheduled refreshes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sigantry_core.pr_bot.payload import TmdlDiffSection

BreakingKind = Literal[
    "table_removed",
    "column_removed",
    "measure_removed",
    "relationship_removed",
]
Severity = Literal["CRITICAL", "HIGH"]


@dataclass(frozen=True, slots=True)
class BreakingChange:
    """A breaking change detected in a semantic model (TMDL)."""

    kind: BreakingKind
    severity: Severity
    target: str
    impact: str


def analyze_tmdl_breaking_changes(
    tmdl_diff: TmdlDiffSection | None,
) -> list[BreakingChange]:
    """Analyze a TmdlDiffSection and extract all breaking changes in order."""
    if tmdl_diff is None or tmdl_diff.is_empty():
        return []

    changes: list[BreakingChange] = []

    # 1. Dropped tables (CRITICAL)
    for table in tmdl_diff.tables_removed:
        changes.append(
            BreakingChange(
                kind="table_removed",
                severity="CRITICAL",
                target=table,
                impact="Table dropped; all associated measures and visual bindings will break.",
            )
        )

    # 2. Dropped columns (HIGH)
    for col in tmdl_diff.columns_removed:
        target_str = f"'{col.table}'.[{col.name}]" if col.table else col.name
        changes.append(
            BreakingChange(
                kind="column_removed",
                severity="HIGH",
                target=target_str,
                impact="Column dropped; visuals and calculations referencing it will fail.",
            )
        )

    # 3. Dropped measures (HIGH)
    for meas in tmdl_diff.measures_removed:
        target_str = f"[{meas.name}] ({meas.table})" if meas.table else f"[{meas.name}]"
        changes.append(
            BreakingChange(
                kind="measure_removed",
                severity="HIGH",
                target=target_str,
                impact="Measure dropped; report visuals and downstream DAX references will fail.",
            )
        )

    # 4. Dropped relationships (HIGH)
    for rel in tmdl_diff.relationships_removed:
        changes.append(
            BreakingChange(
                kind="relationship_removed",
                severity="HIGH",
                target=f"'{rel.from_table}' -> '{rel.to_table}'",
                impact="Relationship dropped; cross-filtering between these tables is removed.",
            )
        )

    return changes


def render_breaking_changes_markdown(changes: list[BreakingChange]) -> str:
    """Render a GitHub/ADO alert callout detailing breaking changes."""
    if not changes:
        return ""

    lines = [
        "> [!WARNING]",
        "> ### ⚠️ Breaking Changes Detected in Semantic Model (TMDL)",
        "> The following deletions may break downstream Power BI reports, DAX expressions, or scheduled refreshes:",
        ">",
    ]

    for c in changes:
        badge = "🔴 **CRITICAL**" if c.severity == "CRITICAL" else "🟠 **HIGH**"
        label = c.kind.replace("_", " ").title()
        lines.append(f"> - {badge} `{label}`: `{c.target}` — *{c.impact}*")

    return "\n".join(lines)


__all__ = [
    "BreakingChange",
    "BreakingKind",
    "Severity",
    "analyze_tmdl_breaking_changes",
    "render_breaking_changes_markdown",
]
