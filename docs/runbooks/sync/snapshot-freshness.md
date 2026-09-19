# Snapshot freshness -- TTL'd cache discipline

**Phase 13 (INTROSPECT-03 / D-09 / D-10).** Operator-facing reference for the snapshot lifecycle. Snapshots are TTL'd to a single CLI invocation. Never trust a saved JSON snapshot file as if it were live.

## 1. Overview

A `WorkspaceSnapshot` is the deterministic `{folder_path -> folder_id, item_logical_id -> folder_id, items_by_id, folders_by_id}` map produced by two paginated REST calls (`list_folders` + `list_items`). Every Phase 13 surface that needs to know "what's in the workspace right now" calls `snapshot_workspace(workspace_id)` at the start of its run:

- `sigantry sync apply` -- snapshots, packages, reconciles.
- `sigantry sync pull` -- snapshots, fetches definitions.
- `sigantry diff` -- snapshots, compares against manifest.
- `sigantry sync snapshot` -- snapshots, prints / writes JSON.

Snapshots are NEVER cached across CLI invocations. Each run re-fetches.

## 2. The locked warning (D-10)

> Native Git Sync + REST writes can change folder GUIDs between snapshots; trust the live API, never a previous snapshot file.

This is the locked Council D #6 / D-10 invariant. It carries through this runbook, the schema docstring, and the integration test fixture for INTROSPECT-03.

## 3. What this means in practice

Two real scenarios where a stale snapshot bites:

### Scenario A -- Native Git Sync rewrites folder GUIDs

Operator runs `sigantry sync snapshot --workspace-id <id> --output snap.json` at 09:00. At 09:30 a teammate triggers a Native Git Sync update from the workspace's bound repo, and Fabric internally re-creates the folder hierarchy (folder GUIDs change). At 10:00 the operator runs:

```bash
# Wrong: feeding the stale snapshot file into a downstream tool.
some-third-party-tool --snapshot snap.json --do-stuff <id>
```

The tool now operates against folder GUIDs that NO LONGER EXIST. Any folder-id-keyed write fails with `404 FolderNotFound`; any folder-path-keyed write succeeds against the wrong (newly-minted) folder.

**Correct workflow:** never persist a snapshot beyond the lifetime of the operation that produced it. If you need to compare "before" and "after," persist the comparison (the diff JSON), not the snapshot.

### Scenario B -- REST writes between snapshots

Operator runs `sigantry sync apply` at 14:00 (which snapshots internally). The apply succeeds. At 14:05 the operator runs `sigantry diff` against the same workspace. The diff CLI re-snapshots -- because INTROSPECT-03 says snapshots are per-invocation -- and correctly sees the post-apply state.

If `sigantry diff` reused a 14:00 snapshot, it would report stale "added" / "modified" entries that the apply already handled.

## 4. The two-call contract (Council D)

Each `snapshot_workspace` invocation issues exactly two paginated REST calls:

1. `GET /v1/workspaces/{id}/folders?continuationToken=...` (Preview -- see [`api-stability.md`](../../reference/api-stability.md))
2. `GET /v1/workspaces/{id}/items?continuationToken=...` (GA)

Both endpoints support continuation tokens. Pagination is exercised by the unit test in `tests/sync/test_snapshot.py::test_snapshot_workspace_paginates` against a synthetic >100-folder workspace.

The two calls are sequential, not parallel -- folder records are needed before items can be path-resolved. Total wall time scales with `O(folders + items)` paginated through the standard 100-record page size.

## 5. CLI surface

The `sigantry sync snapshot` command exposes a snapshot directly:

```bash
sigantry sync snapshot --workspace-id <id>                      # JSON to stdout
sigantry sync snapshot --workspace-id <id> --output snap.json   # JSON to file
```

The on-disk shape is documented inline in `sigantry_core.sync.cli.snapshot_cmd` and asserted by `tests/sync/test_cli_sync.py`. The shape is **not** SemVer-pinned across major versions -- it's an operator diagnostic, not a stable API surface. If you need a SemVer-stable snapshot, write your own consumer of `sigantry_core.sync.snapshot.snapshot_workspace` and pin the schema yourself.

## 6. Cross-references

- [`apply.md`](apply.md) -- `sigantry sync apply` snapshots at the start of every run.
- [`pull.md`](pull.md) -- `sigantry sync pull` snapshots at the start of every run.
- [`../drift-detection/scheduled-drift.md`](../drift-detection/scheduled-drift.md) -- scheduled CI runs implicitly snapshot per cron tick.
- [`../../reference/api-stability.md`](../../reference/api-stability.md) -- the Folders endpoint Preview status (relevant to snapshot freshness because the Preview surface itself can change without warning).
