"""Pure-Python Lakehouse metadata-file diff for STARTER-06 (Plan 14-04).

Walks two trees of ``*.Lakehouse/`` folders and returns a frozen
:class:`sigantry_core.pr_bot.payload.LakehouseDiffSection` over four
metadata files per item: ``.platform``, ``lakehouse.metadata.json``,
``shortcuts.metadata.json``, ``data-access-roles.json``.

Per RESEARCH §Critical Finding, Microsoft Fabric does NOT track Lakehouse
table column types in Git, so the diff is bounded to metadata-file
content; :data:`_COLUMN_WARNING_SHORT` is appended to every ``warnings``
list so programmatic consumers cannot accidentally drop the caveat
(the renderer in ``sigantry_core.pr_bot.payload.render_markdown`` emits
a longer human-readable footer separately).

Constraints (CONTEXT.md D-11 / threat register T-14-04-01..02):
pure stdlib + ``sigantry_core.pr_bot.payload`` (banned-API gate); ``json.loads``
only; per-file 1 MB cap raises :class:`LakehouseDiffError`; repo content
only -- no live REST queries during PR review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from sigantry_core.pr_bot.payload import (
    IdentityChange,
    LakehouseDiffSection,
    RoleChange,
    SchemaToggleChange,
    ShortcutChange,
)

_MAX_FILE_BYTES: Final[int] = 1_048_576  # 1 MB per-file cap (T-14-04-01).
_LAKEHOUSE_DIR_SUFFIX: Final[str] = ".Lakehouse"
_COLUMN_WARNING_SHORT: Final[str] = (
    "Note: Microsoft Fabric does not track Lakehouse table column types in Git."
)
_PLATFORM_FIELDS: Final[tuple[str, ...]] = ("displayName", "description", "type")
_PLATFORM_FILE: Final[str] = ".platform"
_LAKEHOUSE_METADATA_FILE: Final[str] = "lakehouse.metadata.json"
_SHORTCUTS_METADATA_FILE: Final[str] = "shortcuts.metadata.json"
_DATA_ACCESS_ROLES_FILE: Final[str] = "data-access-roles.json"


class LakehouseDiffError(ValueError):
    """Raised on size-cap breach or malformed Lakehouse metadata JSON."""


def _read_capped_json(path: Path) -> dict[str, Any]:
    """Return parsed JSON from ``path`` enforcing :data:`_MAX_FILE_BYTES`; ``{}`` if missing."""
    if not path.is_file():
        return {}
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise LakehouseDiffError(f"{path}: file size {size} bytes exceeds 1 MB cap")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LakehouseDiffError(f"{path}: malformed JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise LakehouseDiffError(
            f"{path}: top-level value must be an object, got {type(parsed).__name__}"
        )
    return parsed


def _load_lakehouse_metadata(item_dir: Path) -> dict[str, dict[str, Any]]:
    """Load the four metadata-file types under one ``*.Lakehouse/`` folder."""
    return {
        "platform": _read_capped_json(item_dir / _PLATFORM_FILE),
        "lakehouse_metadata": _read_capped_json(item_dir / _LAKEHOUSE_METADATA_FILE),
        "shortcuts_metadata": _read_capped_json(item_dir / _SHORTCUTS_METADATA_FILE),
        "data_access_roles": _read_capped_json(item_dir / _DATA_ACCESS_ROLES_FILE),
    }


def _find_lakehouse_dirs(root: Path) -> dict[str, Path]:
    """Map ``{stem: absolute_path}`` for every ``*.Lakehouse/`` under ``root``."""
    if not root.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*" + _LAKEHOUSE_DIR_SUFFIX)):
        if not path.is_dir():
            continue
        stem = path.name[: -len(_LAKEHOUSE_DIR_SUFFIX)]
        found[stem] = path
    return found


def _diff_identity(
    base_platform: dict[str, Any], head_platform: dict[str, Any]
) -> list[IdentityChange]:
    """Compare ``.platform`` ``metadata`` fields per :data:`_PLATFORM_FIELDS`."""
    base_meta = base_platform.get("metadata", {}) or {}
    head_meta = head_platform.get("metadata", {}) or {}
    changes: list[IdentityChange] = []
    for field in _PLATFORM_FIELDS:
        before = str(base_meta.get(field, ""))
        after = str(head_meta.get(field, ""))
        if before != after:
            changes.append(IdentityChange(field=field, before=before, after=after))
    return changes


def _diff_schema_toggle(
    base_lh: dict[str, Any], head_lh: dict[str, Any]
) -> SchemaToggleChange | None:
    """Compare ``defaultSchema`` -- absent renders as the literal ``"(absent)"``."""
    before = base_lh.get("defaultSchema", "(absent)")
    after = head_lh.get("defaultSchema", "(absent)")
    if before == after:
        return None
    return SchemaToggleChange(before=str(before), after=str(after))


def _diff_tracked_tables(
    base_lh: dict[str, Any], head_lh: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Compare optional ``tables`` array (name-only delta; types are NOT in Git)."""

    def _names(d: dict[str, Any]) -> set[str]:
        s = {t.get("name", "") for t in d.get("tables", []) if isinstance(t, dict)}
        s.discard("")
        return s

    base_names, head_names = _names(base_lh), _names(head_lh)
    return sorted(head_names - base_names), sorted(base_names - head_names)


def _shortcut_key(s: dict[str, Any]) -> str:
    """Stable equality key for shortcuts: prefer ``name`` else ``path``."""
    for k in ("name", "path"):
        v = s.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _shortcut_to_change(s: dict[str, Any]) -> ShortcutChange:
    """Project a parsed shortcut dict onto :class:`ShortcutChange`."""
    target = s.get("target") or {}
    target_path = target.get("path") if isinstance(target, dict) else None
    return ShortcutChange(
        name=str(s.get("name", "")),
        path=str(s.get("path", "")),
        target_path=str(target_path) if target_path else None,
    )


def _diff_shortcuts(
    base_sc: dict[str, Any], head_sc: dict[str, Any]
) -> tuple[list[ShortcutChange], list[ShortcutChange], list[ShortcutChange]]:
    """Diff ``shortcuts.metadata.json`` -- returns ``(added, removed, modified)``."""

    def _map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for entry in payload.get("shortcuts", []) or []:
            if isinstance(entry, dict):
                key = _shortcut_key(entry)
                if key:
                    out[key] = entry
        return out

    base_map = _map(base_sc)
    head_map = _map(head_sc)
    added_keys = sorted(set(head_map) - set(base_map))
    removed_keys = sorted(set(base_map) - set(head_map))
    common_keys = sorted(set(base_map) & set(head_map))
    added = [_shortcut_to_change(head_map[k]) for k in added_keys]
    removed = [_shortcut_to_change(base_map[k]) for k in removed_keys]
    modified = [_shortcut_to_change(head_map[k]) for k in common_keys if base_map[k] != head_map[k]]
    return added, removed, modified


def _diff_roles(base_dar: dict[str, Any], head_dar: dict[str, Any]) -> list[RoleChange]:
    """Diff ``data-access-roles.json`` -- emit RoleChange per role name."""

    def _role_map(dar: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for entry in dar.get("roles", []) or []:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name:
                    out[name] = entry
        return out

    base_map = _role_map(base_dar)
    head_map = _role_map(head_dar)
    changes: list[RoleChange] = []
    for name in sorted(set(head_map) - set(base_map)):
        changes.append(RoleChange(role=name, change="added"))
    for name in sorted(set(base_map) - set(head_map)):
        changes.append(RoleChange(role=name, change="removed"))
    for name in sorted(set(base_map) & set(head_map)):
        if base_map[name] != head_map[name]:
            changes.append(RoleChange(role=name, change="modified"))
    return changes


def diff_lakehouse(base_dir: Path | str, head_dir: Path | str) -> LakehouseDiffSection:
    """Diff two trees of ``*.Lakehouse/`` metadata folders.

    Joins items by stem name (e.g. ``Sales`` for ``Sales.Lakehouse``) and
    aggregates per-item diffs. Items present on only one side are not
    surfaced at this layer; Plan 14-06's CLI dispatcher layers file-level
    context. The returned ``warnings`` list ends with the short-form
    column-warning so consumers cannot drop the caveat.
    """
    base_dirs = _find_lakehouse_dirs(Path(base_dir))
    head_dirs = _find_lakehouse_dirs(Path(head_dir))
    identity_changes: list[IdentityChange] = []
    schema_toggle: SchemaToggleChange | None = None
    tracked_tables_added: list[str] = []
    tracked_tables_removed: list[str] = []
    shortcuts_added: list[ShortcutChange] = []
    shortcuts_removed: list[ShortcutChange] = []
    shortcuts_modified: list[ShortcutChange] = []
    role_changes: list[RoleChange] = []

    for stem in sorted(set(base_dirs) & set(head_dirs)):
        base_meta = _load_lakehouse_metadata(base_dirs[stem])
        head_meta = _load_lakehouse_metadata(head_dirs[stem])
        identity_changes.extend(_diff_identity(base_meta["platform"], head_meta["platform"]))
        # First non-None schema_toggle wins (single-lakehouse renderer surface).
        if schema_toggle is None:
            schema_toggle = _diff_schema_toggle(
                base_meta["lakehouse_metadata"], head_meta["lakehouse_metadata"]
            )
        added_t, removed_t = _diff_tracked_tables(
            base_meta["lakehouse_metadata"], head_meta["lakehouse_metadata"]
        )
        tracked_tables_added.extend(added_t)
        tracked_tables_removed.extend(removed_t)
        s_added, s_removed, s_modified = _diff_shortcuts(
            base_meta["shortcuts_metadata"], head_meta["shortcuts_metadata"]
        )
        shortcuts_added.extend(s_added)
        shortcuts_removed.extend(s_removed)
        shortcuts_modified.extend(s_modified)
        role_changes.extend(
            _diff_roles(base_meta["data_access_roles"], head_meta["data_access_roles"])
        )

    return LakehouseDiffSection(
        identity_changes=identity_changes,
        schema_toggle=schema_toggle,
        tracked_tables_added=sorted(tracked_tables_added),
        tracked_tables_removed=sorted(tracked_tables_removed),
        shortcuts_added=shortcuts_added,
        shortcuts_removed=shortcuts_removed,
        shortcuts_modified=shortcuts_modified,
        role_changes=role_changes,
        warnings=[_COLUMN_WARNING_SHORT],
    )


__all__ = ["LakehouseDiffError", "diff_lakehouse"]
