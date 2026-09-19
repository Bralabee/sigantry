"""Standalone interactive HTML report generators for Sigantry.

Provides zero-dependency, self-contained HTML reports with embedded CSS and JS
for drift analysis, release inspection, and release comparisons.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sigantry_core.sync.diff import DriftReport

_BASE_CSS = """
:root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-tertiary: #21262d;
    --border-color: #30363d;
    --text-primary: #f0f6fc;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-blue: #58a6ff;
    --accent-green: #3fb950;
    --accent-red: #f85149;
    --accent-yellow: #e3b341;
    --accent-purple: #bc8cff;
    --badge-green-bg: rgba(46, 160, 67, 0.15);
    --badge-red-bg: rgba(248, 81, 73, 0.15);
    --badge-yellow-bg: rgba(210, 153, 34, 0.15);
    --badge-blue-bg: rgba(56, 139, 253, 0.15);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.5;
    padding: 2rem 1.5rem;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
}

header {
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 1.5rem;
    margin-bottom: 2rem;
}

.header-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 1rem;
}

.brand {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.brand-icon {
    width: 32px;
    height: 32px;
    background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    color: #fff;
    font-size: 1.1rem;
}

h1 {
    font-size: 1.6rem;
    font-weight: 600;
    color: var(--text-primary);
}

.meta-badges {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin-top: 0.75rem;
}

.meta-pill {
    font-size: 0.8rem;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 20px;
    padding: 0.2rem 0.75rem;
    color: var(--text-secondary);
}

.meta-pill strong {
    color: var(--text-primary);
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
}

.stat-card {
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
}

.stat-label {
    font-size: 0.85rem;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.25rem;
}

.stat-value {
    font-size: 1.8rem;
    font-weight: 700;
}

.stat-added { color: var(--accent-green); }
.stat-removed { color: var(--accent-red); }
.stat-modified { color: var(--accent-yellow); }
.stat-unchanged { color: var(--text-secondary); }
.stat-total { color: var(--accent-blue); }

.controls-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 1rem;
    margin-bottom: 1.5rem;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 0.75rem 1rem;
}

.search-box {
    display: flex;
    align-items: center;
    background-color: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 0.4rem 0.75rem;
    width: 320px;
    max-width: 100%;
}

.search-box input {
    background: transparent;
    border: none;
    color: var(--text-primary);
    font-size: 0.9rem;
    outline: none;
    width: 100%;
}

.search-box input::placeholder {
    color: var(--text-muted);
}

.filter-pills {
    display: flex;
    gap: 0.4rem;
}

.filter-btn {
    background: transparent;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    color: var(--text-secondary);
    font-size: 0.85rem;
    padding: 0.35rem 0.75rem;
    cursor: pointer;
    transition: all 0.15s ease;
}

.filter-btn:hover {
    background-color: var(--bg-tertiary);
    color: var(--text-primary);
}

.filter-btn.active {
    background-color: var(--accent-blue);
    border-color: var(--accent-blue);
    color: #fff;
    font-weight: 600;
}

.table-card {
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    overflow: hidden;
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
    text-align: left;
}

th {
    background-color: var(--bg-tertiary);
    color: var(--text-secondary);
    font-weight: 600;
    padding: 0.8rem 1rem;
    border-bottom: 1px solid var(--border-color);
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
}

td {
    padding: 0.8rem 1rem;
    border-bottom: 1px solid var(--border-color);
}

tr:last-child td {
    border-bottom: none;
}

tr:hover td {
    background-color: rgba(255, 255, 255, 0.02);
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    white-space: nowrap;
}

.badge-added {
    background-color: var(--badge-green-bg);
    color: var(--accent-green);
    border: 1px solid rgba(46, 160, 67, 0.4);
}

.badge-removed {
    background-color: var(--badge-red-bg);
    color: var(--accent-red);
    border: 1px solid rgba(248, 81, 73, 0.4);
}

.badge-modified {
    background-color: var(--badge-yellow-bg);
    color: var(--accent-yellow);
    border: 1px solid rgba(210, 153, 34, 0.4);
}

.badge-unchanged {
    background-color: var(--bg-tertiary);
    color: var(--text-secondary);
    border: 1px solid var(--border-color);
}

.badge-type {
    background-color: var(--badge-blue-bg);
    color: var(--accent-blue);
    border: 1px solid rgba(56, 139, 253, 0.4);
}

.item-name {
    font-weight: 600;
    color: var(--text-primary);
}

.item-path {
    font-family: monospace;
    font-size: 0.8rem;
    color: var(--text-muted);
}

.item-detail {
    font-family: monospace;
    font-size: 0.8rem;
    color: var(--text-secondary);
}

.empty-state {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
}

footer {
    margin-top: 2rem;
    text-align: center;
    font-size: 0.8rem;
    color: var(--text-muted);
}
"""

_BASE_JS = """
function filterItems() {
    const query = document.getElementById('search-input').value.toLowerCase();
    const activeFilter = document.querySelector('.filter-btn.active').dataset.filter;
    const rows = document.querySelectorAll('tbody tr');
    let visibleCount = 0;

    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        const status = row.dataset.status;

        const matchesQuery = text.includes(query);
        const matchesFilter = (activeFilter === 'all') || (status === activeFilter);

        if (matchesQuery && matchesFilter) {
            row.style.display = '';
            visibleCount++;
        } else {
            row.style.display = 'none';
        }
    });

    const empty = document.getElementById('no-match-row');
    if (empty) {
        empty.style.display = (visibleCount === 0) ? '' : 'none';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.addEventListener('input', filterItems);
    }

    const buttons = document.querySelectorAll('.filter-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            buttons.forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            filterItems();
        });
    });
});
"""


def render_drift_html_report(
    report: DriftReport,
    *,
    environment: str,
    workspace_id: str,
) -> str:
    """Render a standalone interactive HTML drift report from a DriftReport object."""
    now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    added_count = len(report.added)
    removed_count = len(report.removed)
    modified_count = len(report.modified)
    unchanged_count = len(report.unchanged)
    total_drift = added_count + removed_count + modified_count

    rows_html: list[str] = []

    for item in report.added:
        type_ = html.escape(str(item.get("type", "")))
        name = html.escape(str(item.get("display_name", "")))
        folder = html.escape(str(item.get("folder_path", "")))
        lid = html.escape(str(item.get("logical_id", "")))
        rows_html.append(
            f'<tr data-status="added">'
            f'<td><span class="badge badge-added">+ added</span></td>'
            f'<td><span class="badge badge-type">{type_}</span></td>'
            f'<td><div class="item-name">{name}</div><div class="item-path">{folder}</div></td>'
            f'<td><span class="item-detail">{lid}</span></td>'
            f"</tr>"
        )

    for item in report.removed:
        type_ = html.escape(str(item.get("type", "")))
        name = html.escape(str(item.get("display_name", "")))
        folder = html.escape(str(item.get("folder_path", "")))
        lid = html.escape(str(item.get("logical_id", "")))
        rows_html.append(
            f'<tr data-status="removed">'
            f'<td><span class="badge badge-removed">- removed</span></td>'
            f'<td><span class="badge badge-type">{type_}</span></td>'
            f'<td><div class="item-name">{name}</div><div class="item-path">{folder}</div></td>'
            f'<td><span class="item-detail">{lid}</span></td>'
            f"</tr>"
        )

    for mod_item in report.modified:
        lid = html.escape(str(mod_item.get("logical_id", "")))
        fields: Any = mod_item.get("fields_changed", [])
        detail = (
            ", ".join(html.escape(str(f)) for f in fields)
            if isinstance(fields, list)
            else html.escape(str(fields))
        )
        rows_html.append(
            f'<tr data-status="modified">'
            f'<td><span class="badge badge-modified">~ modified</span></td>'
            f'<td><span class="badge badge-type">Item</span></td>'
            f'<td><div class="item-name">{lid}</div></td>'
            f'<td><span class="item-detail">changed: {detail}</span></td>'
            f"</tr>"
        )

    for unch_item in report.unchanged:
        lid = html.escape(str(unch_item.get("logical_id", "")))
        rows_html.append(
            f'<tr data-status="unchanged">'
            f'<td><span class="badge badge-unchanged">= unchanged</span></td>'
            f'<td><span class="badge badge-type">Item</span></td>'
            f'<td><div class="item-name">{lid}</div></td>'
            f'<td><span class="item-detail">in-sync</span></td>'
            f"</tr>"
        )

    table_body = (
        "\n".join(rows_html)
        if rows_html
        else '<tr><td colspan="4" class="empty-state">No items recorded.</td></tr>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sigantry Drift Report - {html.escape(environment)}</title>
    <style>{_BASE_CSS}</style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-top">
                <div class="brand">
                    <div class="brand-icon">S</div>
                    <div>
                        <h1>Sigantry Drift Report</h1>
                    </div>
                </div>
                <div class="meta-badges">
                    <span class="meta-pill">Env: <strong>{html.escape(environment)}</strong></span>
                    <span class="meta-pill">Workspace: <strong>{html.escape(workspace_id)}</strong></span>
                    <span class="meta-pill">Generated: <strong>{now_utc}</strong></span>
                </div>
            </div>
        </header>

        <section class="stats-grid">
            <div class="stat-card">
                <span class="stat-label">Total Drift</span>
                <span class="stat-value {"stat-modified" if total_drift > 0 else "stat-unchanged"}">{total_drift}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Added</span>
                <span class="stat-value stat-added">+{added_count}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Removed</span>
                <span class="stat-value stat-removed">-{removed_count}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Modified</span>
                <span class="stat-value stat-modified">~{modified_count}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Unchanged</span>
                <span class="stat-value stat-unchanged">={unchanged_count}</span>
            </div>
        </section>

        <div class="controls-bar">
            <div class="search-box">
                <input type="text" id="search-input" placeholder="Filter items by name, path, or type...">
            </div>
            <div class="filter-pills">
                <button type="button" class="filter-btn active" data-filter="all">All ({total_drift + unchanged_count})</button>
                <button type="button" class="filter-btn" data-filter="added">Added ({added_count})</button>
                <button type="button" class="filter-btn" data-filter="removed">Removed ({removed_count})</button>
                <button type="button" class="filter-btn" data-filter="modified">Modified ({modified_count})</button>
                <button type="button" class="filter-btn" data-filter="unchanged">Unchanged ({unchanged_count})</button>
            </div>
        </div>

        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th style="width: 130px;">Status</th>
                        <th style="width: 140px;">Type</th>
                        <th>Item</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
                    {table_body}
                    <tr id="no-match-row" style="display: none;">
                        <td colspan="4" class="empty-state">No matching items found.</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <footer>
            Generated by Sigantry v1.0.0 &bull; JToye Digital
        </footer>
    </div>
    <script>{_BASE_JS}</script>
</body>
</html>
"""


def render_release_html_report(record_data: dict[str, Any]) -> str:
    """Render a standalone interactive HTML report for a single DeployRecord."""
    release_id = html.escape(str(record_data.get("release_id", "")))
    workspace = html.escape(str(record_data.get("workspace", "")))
    approver = html.escape(str(record_data.get("approver", "")))
    created_at = html.escape(str(record_data.get("created_at", "")))
    audit_hash = html.escape(str(record_data.get("audit_hash", "")))
    prev_hash = html.escape(str(record_data.get("prev_hash", "") or "genesis"))

    items = record_data.get("fabric_items_changed", []) or []
    work_items = record_data.get("work_items", []) or []
    test_evidence = record_data.get("test_evidence", {}) or {}

    rows_html: list[str] = []
    for item in items:
        spec = html.escape(str(item))
        parts = spec.rsplit(".", 1)
        name = parts[0] if len(parts) == 2 else spec
        type_ = parts[1] if len(parts) == 2 else "Item"
        rows_html.append(
            f'<tr data-status="changed">'
            f'<td><span class="badge badge-added">&bull; changed</span></td>'
            f'<td><span class="badge badge-type">{type_}</span></td>'
            f'<td><div class="item-name">{name}</div></td>'
            f'<td><span class="item-detail">{spec}</span></td>'
            f"</tr>"
        )

    table_body = (
        "\n".join(rows_html)
        if rows_html
        else '<tr><td colspan="4" class="empty-state">No items changed in this release.</td></tr>'
    )

    evidence_badges = (
        " ".join(
            f'<span class="meta-pill">{html.escape(k)}: <strong>{html.escape(str(v))}</strong></span>'
            for k, v in test_evidence.items()
        )
        or '<span class="meta-pill">None</span>'
    )

    work_items_badges = (
        " ".join(
            f'<span class="badge badge-type">{html.escape(str(wi))}</span>' for wi in work_items
        )
        or '<span class="item-detail">None</span>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sigantry Release Report - {release_id}</title>
    <style>{_BASE_CSS}</style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-top">
                <div class="brand">
                    <div class="brand-icon">S</div>
                    <div>
                        <h1>Release Report &bull; {release_id}</h1>
                    </div>
                </div>
                <div class="meta-badges">
                    <span class="meta-pill">Workspace: <strong>{workspace}</strong></span>
                    <span class="meta-pill">Approver: <strong>{approver}</strong></span>
                    <span class="meta-pill">Timestamp: <strong>{created_at}</strong></span>
                </div>
            </div>
            <div class="meta-badges" style="margin-top: 0.75rem;">
                <span class="meta-pill">Audit Hash: <strong style="font-family: monospace;">{audit_hash[:16]}...</strong></span>
                <span class="meta-pill">Prev Hash: <strong style="font-family: monospace;">{prev_hash[:16] if prev_hash != "genesis" else "genesis"}</strong></span>
                <span class="meta-pill">Evidence: {evidence_badges}</span>
                <span class="meta-pill">Work Items: {work_items_badges}</span>
            </div>
        </header>

        <section class="stats-grid">
            <div class="stat-card">
                <span class="stat-label">Items Changed</span>
                <span class="stat-value stat-total">{len(items)}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Work Items</span>
                <span class="stat-value stat-unchanged">{len(work_items)}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Evidence Checks</span>
                <span class="stat-value stat-added">{len(test_evidence)}</span>
            </div>
        </section>

        <div class="controls-bar">
            <div class="search-box">
                <input type="text" id="search-input" placeholder="Filter changed items...">
            </div>
        </div>

        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th style="width: 130px;">Action</th>
                        <th style="width: 140px;">Type</th>
                        <th>Item Name</th>
                        <th>Identifier</th>
                    </tr>
                </thead>
                <tbody>
                    {table_body}
                    <tr id="no-match-row" style="display: none;">
                        <td colspan="4" class="empty-state">No matching items found.</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <footer>
            Generated by Sigantry v1.0.0 &bull; JToye Digital
        </footer>
    </div>
    <script>{_BASE_JS}</script>
</body>
</html>
"""


def render_release_diff_html_report(diff_data: dict[str, Any]) -> str:
    """Render a standalone interactive HTML report for comparing two releases."""
    rel_a = html.escape(str(diff_data.get("release_a", "")))
    rel_b = html.escape(str(diff_data.get("release_b", "")))
    added = diff_data.get("added", []) or []
    removed = diff_data.get("removed", []) or []
    unchanged = diff_data.get("unchanged", []) or []

    rows_html: list[str] = []
    for item in added:
        fid = html.escape(str(item.get("fabric_item_id", "")))
        itype = html.escape(str(item.get("item_type", "")))
        lname = html.escape(str(item.get("logical_name", "")))
        rows_html.append(
            f'<tr data-status="added">'
            f'<td><span class="badge badge-added">+ added</span></td>'
            f'<td><span class="badge badge-type">{itype}</span></td>'
            f'<td><div class="item-name">{lname}</div></td>'
            f'<td><span class="item-detail">{fid}</span></td>'
            f"</tr>"
        )

    for item in removed:
        fid = html.escape(str(item.get("fabric_item_id", "")))
        itype = html.escape(str(item.get("item_type", "")))
        lname = html.escape(str(item.get("logical_name", "")))
        rows_html.append(
            f'<tr data-status="removed">'
            f'<td><span class="badge badge-removed">- removed</span></td>'
            f'<td><span class="badge badge-type">{itype}</span></td>'
            f'<td><div class="item-name">{lname}</div></td>'
            f'<td><span class="item-detail">{fid}</span></td>'
            f"</tr>"
        )

    for item in unchanged:
        fid = html.escape(str(item.get("fabric_item_id", "")))
        itype = html.escape(str(item.get("item_type", "")))
        lname = html.escape(str(item.get("logical_name", "")))
        rows_html.append(
            f'<tr data-status="unchanged">'
            f'<td><span class="badge badge-unchanged">= unchanged</span></td>'
            f'<td><span class="badge badge-type">{itype}</span></td>'
            f'<td><div class="item-name">{lname}</div></td>'
            f'<td><span class="item-detail">{fid}</span></td>'
            f"</tr>"
        )

    table_body = (
        "\n".join(rows_html)
        if rows_html
        else '<tr><td colspan="4" class="empty-state">No item differences recorded.</td></tr>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sigantry Release Diff - {rel_a} vs {rel_b}</title>
    <style>{_BASE_CSS}</style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-top">
                <div class="brand">
                    <div class="brand-icon">S</div>
                    <div>
                        <h1>Release Comparison</h1>
                    </div>
                </div>
                <div class="meta-badges">
                    <span class="meta-pill">Earlier: <strong>{rel_a}</strong></span>
                    <span class="meta-pill">Later: <strong>{rel_b}</strong></span>
                </div>
            </div>
        </header>

        <section class="stats-grid">
            <div class="stat-card">
                <span class="stat-label">Added Items</span>
                <span class="stat-value stat-added">+{len(added)}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Removed Items</span>
                <span class="stat-value stat-removed">-{len(removed)}</span>
            </div>
            <div class="stat-card">
                <span class="stat-label">Unchanged Items</span>
                <span class="stat-value stat-unchanged">={len(unchanged)}</span>
            </div>
        </section>

        <div class="controls-bar">
            <div class="search-box">
                <input type="text" id="search-input" placeholder="Filter comparison items...">
            </div>
            <div class="filter-pills">
                <button type="button" class="filter-btn active" data-filter="all">All ({len(added) + len(removed) + len(unchanged)})</button>
                <button type="button" class="filter-btn" data-filter="added">Added ({len(added)})</button>
                <button type="button" class="filter-btn" data-filter="removed">Removed ({len(removed)})</button>
                <button type="button" class="filter-btn" data-filter="unchanged">Unchanged ({len(unchanged)})</button>
            </div>
        </div>

        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th style="width: 130px;">Status</th>
                        <th style="width: 140px;">Type</th>
                        <th>Logical Name</th>
                        <th>Fabric Item ID</th>
                    </tr>
                </thead>
                <tbody>
                    {table_body}
                    <tr id="no-match-row" style="display: none;">
                        <td colspan="4" class="empty-state">No matching items found.</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <footer>
            Generated by Sigantry v1.0.0 &bull; JToye Digital
        </footer>
    </div>
    <script>{_BASE_JS}</script>
</body>
</html>
"""
