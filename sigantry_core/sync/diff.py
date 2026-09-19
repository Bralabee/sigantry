"""Drift detection between a local ``sync.yml`` manifest and a live workspace
(Phase 13 / DRIFT-01..02 / D-23 / D-24).

Public surface:

* :class:`DriftReport` -- frozen dataclass carrying the SemVer-pinned wire
  contract ``{schema_version, added, removed, modified, unchanged}``.
* :func:`diff_workspace_against_manifest` -- compute drift for a given
  ``(manifest_path, workspace_id)`` pair.

Locked decisions enforced here:

* **D-23** (JSON keyset): top-level ``{schema_version, added, removed,
  modified, unchanged}`` and per-entry keysets are locked. The committed
  JSON Schema at ``docs/reference/drift-schema.json`` is the SemVer-pinned
  wire contract; ``tests/sync/test_drift_schema_committed.py`` asserts
  the runtime emission validates against the committed schema.
* **D-24** (metadata-only modified): drift detection compares
  ``display_name``, ``type``, and ``folder_path`` -- never content
  (notebook bodies, pipeline definitions). Content-level drift is
  deferred -- tracked as a candidate enhancement in
  ``V3.X-ROADMAP.md``. Phase 13.5 BOOTSTRAP-XX shipped without
  including it.
* **D-09 / INTROSPECT-03**: a fresh :class:`WorkspaceSnapshot` is built
  on every invocation. No cross-run cache. Plan 13-04's apply engine
  does NOT call snapshot (W9 invariant); plan 13-06's diff engine DOES.
* **D-31** (one HTTP client): every REST call routes through
  :class:`sigantry_core.client.FabricRestClient`. No direct ``httpx``
  usage in this module.

Implementation notes:

* Manifest items are joined to workspace items by ``logical_id`` when
  the manifest declares one (operator pulled from a workspace before).
  Bootstrap manifests (operator-authored ``sync.yml`` without explicit
  ids) fall back to a deterministic synthetic key
  ``synth:<display_name>.<type>``. The synthetic key is also the
  ``logical_id`` field surfaced in the per-entry payload, so the diff
  is reproducible across runs.
* The workspace side joins on ``Item.id`` (the Fabric GUID); when an
  item was created via ``sync apply`` from a manifest entry that
  declared a ``logical_id``, the GUID and the manifest's ``logical_id``
  are the same string. Otherwise the workspace item gets the synthetic
  key on its side too -- so a workspace item with display_name "X" and
  type "Notebook" matches a manifest item with the same display_name
  and type even if neither side has an explicit GUID. This makes
  bootstrap diffs deterministic without a prior ``sync pull``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sigantry_core.client import FabricRestClient
from sigantry_core.sync.manifest import SyncManifest, load_manifest
from sigantry_core.sync.snapshot import WorkspaceSnapshot, snapshot_workspace

logger = logging.getLogger("sigantry_core.sync.diff")

#: Fields compared by the modified-detection algorithm (D-24).
_LOCKED_FIELDS: tuple[str, ...] = ("display_name", "type", "folder_path")

#: Current SemVer-pinned schema version (D-23 / D-37).
_SCHEMA_VERSION: str = "1.0.0"


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Frozen drift report -- SemVer-pinned wire contract (D-23).

    Each entry's keyset is locked:

    * ``added`` / ``removed``: ``{logical_id, display_name, type, folder_path}``
    * ``modified``: ``{logical_id, fields_changed}``
    * ``unchanged``: ``{logical_id}``

    Adding a top-level field or a per-entry field requires a SemVer-minor
    bump on the schema (D-37); the committed JSON Schema lives at
    ``docs/reference/drift-schema.json`` and is asserted byte-equal to
    runtime emission by ``tests/sync/test_drift_schema_committed.py``.
    """

    added: list[dict[str, str]] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    modified: list[dict[str, str | list[str]]] = field(default_factory=list)
    unchanged: list[dict[str, str]] = field(default_factory=list)
    schema_version: str = _SCHEMA_VERSION

    def to_json(self) -> dict[str, object]:
        """Return the canonical wire dict (D-23 keyset).

        Key order matches the committed JSON Schema's ``required`` list
        (sorted). Consumers that pin ``--output json`` and feed the
        output to ``jq`` can rely on this stable shape across releases
        within v1.x.
        """
        return {
            "added": [dict(entry) for entry in self.added],
            "modified": [dict(entry) for entry in self.modified],
            "removed": [dict(entry) for entry in self.removed],
            "schema_version": self.schema_version,
            "unchanged": [dict(entry) for entry in self.unchanged],
        }

    def has_drift(self) -> bool:
        """``True`` if any of ``added`` / ``removed`` / ``modified`` is non-empty.

        The ``unchanged`` array is informational; an empty workspace
        with an empty manifest still returns ``has_drift() == False``
        even though every list is empty.
        """
        return bool(self.added) or bool(self.removed) or bool(self.modified)


def _synth_key(display_name: str, item_type: str) -> str:
    """Deterministic synthetic logical_id for an entry without an explicit GUID.

    Format: ``synth:<display_name>.<type>``. Surfacing the prefix in the
    payload makes the diff transparently honest about which entries
    matched on a synthesised key vs a real GUID -- operators can grep
    for ``synth:`` to find rows that lack a stable Fabric ``logical_id``.
    """
    return f"synth:{display_name}.{item_type}"


def _folder_path_for_id(folder_id: str | None, snapshot: WorkspaceSnapshot) -> str:
    """Resolve a workspace ``folder_id`` to its forward-slash path string.

    Mirrors :func:`sigantry_core.sync.snapshot._index_folders_by_path`
    by walking the parent chain to the workspace root. A ``None`` folder
    id (item lives at the workspace root) maps to ``"/"``.

    Cycle-safe via :func:`sigantry_core.workspace.folders.folder_path_string`
    (audit-2026-05-07 W1.6 — extracted the in-line ``visited`` set this
    site already carried into a shared helper used by the other two
    parent-walk sites in the codebase).
    """
    from sigantry_core.workspace.folders import folder_path_string

    return folder_path_string(folder_id, snapshot.folders_by_id)


def _build_manifest_index(
    manifest: SyncManifest,
) -> dict[str, dict[str, str]]:
    """Build ``{join_key: {logical_id, display_name, type, folder_path}}`` from the manifest.

    Uses the manifest's normalised view (D-07 / SYNC-06: folder-less
    types forced to ``/``) so the diff sees the same folder routing
    apply would push.
    """
    out: dict[str, dict[str, str]] = {}
    for item in manifest.normalised().items:
        if item.logical_id is not None:
            join_key = item.logical_id
            logical_id = item.logical_id
        else:
            join_key = _synth_key(item.display_name, item.type)
            logical_id = join_key
        out[join_key] = {
            "logical_id": logical_id,
            "display_name": item.display_name,
            "type": item.type,
            "folder_path": item.target_folder,
        }
    return out


def _build_workspace_index(
    snapshot: WorkspaceSnapshot,
) -> dict[str, dict[str, str]]:
    """Build ``{join_key: {logical_id, display_name, type, folder_path}}`` from the workspace snapshot.

    Workspace items get their Fabric ``Item.id`` (a GUID) as the join
    key when the snapshot's ``items_by_id`` carries the canonical id
    that matches a manifest's ``logical_id``. Items lacking a
    correspondence in any operator manifest (i.e. extras in Fabric)
    still appear here under the GUID and surface as ``added`` once the
    set difference runs.

    The function ALSO indexes synthetic keys (``synth:<display>.<type>``)
    so a bootstrap manifest (no explicit logical_ids) can join against a
    workspace item by display_name + type alone. The Fabric GUID is
    preferred; the synthetic key is a fallback used only when the
    manifest side carries no explicit id.
    """
    out: dict[str, dict[str, str]] = {}
    # Track the GUID of the first item that claimed each synth_key so
    # the WR-05 collision-WARNING message can surface BOTH GUIDs (the
    # synth alias entry's ``logical_id`` is the synth_key itself, not
    # the underlying Fabric GUID, so we cannot recover the first GUID
    # from ``out[synth_key]`` alone).
    synth_first_guid: dict[str, str] = {}
    for item_id, it in snapshot.items_by_id.items():
        folder_path = _folder_path_for_id(it.folder_id, snapshot)
        entry = {
            "logical_id": item_id,
            "display_name": it.display_name,
            "type": it.type,
            "folder_path": folder_path,
        }
        out[item_id] = entry
        synth_key = _synth_key(it.display_name, it.type)
        # Only add the synthetic alias if it doesn't collide with an
        # existing key. ``synth:`` prefix makes accidental collisions
        # against real GUID keys statistically impossible. We DO NOT
        # overwrite a real entry so GUID-keyed lookups stay
        # deterministic.
        #
        # WR-05 (REVIEW.md): when a workspace has TWO items sharing
        # ``(display_name, type)`` (Fabric does not enforce uniqueness
        # across folders), the second item's synth alias would be
        # silently dropped pre-fix -- a bootstrap manifest joining via
        # the synth key would match only the first item and the second
        # would surface as ``added`` (incorrectly, when the manifest
        # says one item exists and the workspace says two). Post-fix
        # we LOG the collision at WARNING so operators can see the
        # duplicate; the GUID-keyed entry above still surfaces both
        # workspace items, so the second one correctly appears in the
        # ``added`` bucket of the diff -- which IS the right semantics
        # ("manifest declares 1, workspace has 2 -> 1 unchanged + 1
        # added"). The WARNING gives operators the signal they need to
        # investigate the duplicate display_name in their workspace.
        if synth_key in out:
            logger.warning(
                "diff_workspace_synth_alias_collision display_name=%s "
                "type=%s first_item_id=%s second_item_id=%s; "
                "manifest joins to first match, second surfaces as `added`",
                it.display_name,
                it.type,
                synth_first_guid.get(synth_key, "<unknown>"),
                item_id,
            )
            continue
        # Mark the entry as a synth-alias by copying with the synth
        # logical_id surfaced (the operator-facing value), so the
        # set-difference output of unmatched items uses the
        # reproducible synth: prefix rather than the workspace GUID.
        synth_entry = dict(entry)
        synth_entry["logical_id"] = synth_key
        out[synth_key] = synth_entry
        synth_first_guid[synth_key] = item_id
    return out


def _compare_locked_fields(a: dict[str, str], b: dict[str, str]) -> list[str]:
    """Return the sorted list of locked-field names whose values differ.

    Compares ``display_name``, ``type``, ``folder_path`` only (D-24).
    """
    changed: list[str] = []
    for f in _LOCKED_FIELDS:
        if a.get(f) != b.get(f):
            changed.append(f)
    return sorted(changed)


def _compute_buckets(
    manifest_index: dict[str, dict[str, str]],
    workspace_index: dict[str, dict[str, str]],
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str | list[str]]],
    list[dict[str, str]],
]:
    """Run the set-difference algorithm and return the four bucket lists.

    The bucket payloads honour the D-23 per-entry keyset:

    * ``added`` / ``removed``: ``{logical_id, display_name, type, folder_path}``
    * ``modified``: ``{logical_id, fields_changed}``
    * ``unchanged``: ``{logical_id}``
    """
    manifest_keys = set(manifest_index.keys())
    workspace_keys = set(workspace_index.keys())
    # Resolve the bidirectional set difference but de-duplicate on the
    # synthetic-vs-GUID alias: a workspace item may be in both
    # ``workspace_index`` under its GUID AND under its synthetic alias.
    # We keep the GUID-keyed entry as the canonical workspace surface;
    # the synthetic alias is consulted only for the manifest-side join.

    added: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    modified: list[dict[str, str | list[str]]] = []
    unchanged: list[dict[str, str]] = []

    # Track GUID keys we have already attributed to a manifest match so
    # the synthetic-alias pass below does not double-count them.
    matched_workspace_keys: set[str] = set()

    for key in sorted(manifest_keys):
        manifest_entry = manifest_index[key]
        if key in workspace_index:
            workspace_entry = workspace_index[key]
            matched_workspace_keys.add(key)
            # Also mark the GUID key matched if the join was via synth alias.
            if key.startswith("synth:"):
                # The workspace-side entry under the synth key carries
                # the synth logical_id; its GUID lookup-back happens by
                # locating the matching real-GUID entry with the same
                # display_name + type.
                for guid_key, ws_entry in workspace_index.items():
                    if (
                        guid_key != key
                        and not guid_key.startswith("synth:")
                        and ws_entry["display_name"] == workspace_entry["display_name"]
                        and ws_entry["type"] == workspace_entry["type"]
                    ):
                        matched_workspace_keys.add(guid_key)
                        break
            fields_changed = _compare_locked_fields(manifest_entry, workspace_entry)
            if fields_changed:
                modified.append(
                    {
                        "logical_id": manifest_entry["logical_id"],
                        "fields_changed": fields_changed,
                    }
                )
            else:
                unchanged.append({"logical_id": manifest_entry["logical_id"]})
        else:
            removed.append(
                {
                    "logical_id": manifest_entry["logical_id"],
                    "display_name": manifest_entry["display_name"],
                    "type": manifest_entry["type"],
                    "folder_path": manifest_entry["folder_path"],
                }
            )

    # Workspace items NOT matched by any manifest entry -> added.
    # Iterate only over real-GUID keys (skip synth aliases) so each
    # workspace item surfaces exactly once.
    for key in sorted(workspace_keys):
        if key.startswith("synth:"):
            continue
        if key in matched_workspace_keys:
            continue
        workspace_entry = workspace_index[key]
        added.append(
            {
                "logical_id": workspace_entry["logical_id"],
                "display_name": workspace_entry["display_name"],
                "type": workspace_entry["type"],
                "folder_path": workspace_entry["folder_path"],
            }
        )

    return added, removed, modified, unchanged


def diff_workspace_against_manifest(
    manifest_path: str | Path,
    workspace_id: str,
    *,
    client: FabricRestClient | None = None,
    snapshot: WorkspaceSnapshot | None = None,
) -> DriftReport:
    """Compute drift between a ``sync.yml`` manifest and a live workspace.

    Parameters
    ----------
    manifest_path:
        Path to ``sync.yml`` (or any file accepted by
        :func:`sigantry_core.sync.manifest.load_manifest`).
    workspace_id:
        Fabric workspace GUID.
    client:
        Optional :class:`FabricRestClient`. Tests pass an explicit
        respx-backed client so the underlying httpx transport can be
        intercepted. ``None`` -> :func:`snapshot_workspace` constructs
        one via :meth:`FabricRestClient.from_defaults`.
    snapshot:
        Optional pre-built :class:`WorkspaceSnapshot`. Tests pass a
        synthetic snapshot to assert the bucket algorithm without
        exercising the REST surface. Production callers (CLI) should
        leave this ``None`` so the function builds a fresh snapshot
        per invocation (D-09 / INTROSPECT-03).

    Returns
    -------
    DriftReport
        Frozen, SemVer-pinned. Empty-workspace + empty-manifest
        produces a report whose ``has_drift()`` is ``False``.

    Notes
    -----
    The ``manifest`` and ``snapshot`` are loaded / fetched once per
    invocation and dropped on return -- no module-level cache.
    """
    manifest = load_manifest(manifest_path)
    snap = snapshot if snapshot is not None else snapshot_workspace(workspace_id, client=client)

    manifest_index = _build_manifest_index(manifest)
    workspace_index = _build_workspace_index(snap)

    added, removed, modified, unchanged = _compute_buckets(manifest_index, workspace_index)

    return DriftReport(
        schema_version=_SCHEMA_VERSION,
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
    )


__all__ = (
    "DriftReport",
    "diff_workspace_against_manifest",
)
