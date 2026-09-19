# `sigantry sync pull` -- IaC-fy an existing workspace

**Phase 13 (SYNC-05).** Operator-facing reference for the workspace -> local pull engine. Reads a Fabric workspace, fetches per-item definitions via REST, and writes a `sync.yml` plus the pulled sources to a local directory.

## 0. Decision matrix -- which sync verb am I looking for?

The five verbs the sync engine ships are easy to confuse. [ADR-0012](../../decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) formalises the boundary; this table is the operator's quick reference. Pay particular attention to the **first-time item creation** column -- that's the most common misread, and it's why a scaffold notebook can fail to land if you reach for `sync apply` when you meant `deploy run`. `sync pull` is the IaC-fication verb -- it flows workspace -> local, never the other way.

| You want to ... | Verb | Touches workspace? | First-time item creation? |
|---|---|---|---|
| Mirror an existing workspace into a local IaC tree (`sync.yml` + sources) so future runs are no-op idempotent | **`sigantry sync pull`** | no -- read-only | n/a -- pull never publishes |
| Plan + reconcile folder topology against an existing workspace; move existing items into the manifest's `target_folder` paths | **`sigantry sync apply`** | yes -- creates / moves folders + relocates existing items | **NO** -- new items are staged locally but NOT published; see [`apply.md` section 1.1](apply.md#11-what-sync-apply-does-not-do) |
| Deploy first-time items + parameterise per environment (DEV/PREPROD/PROD) + write a `DeployRecord` for audit | **`sigantry deploy run`** | yes -- runs `fabric-cicd publish_all_items` | **YES** |
| Compare a manifest against a live workspace and report drift | **`sigantry diff`** | no -- read-only | n/a |
| Capture a workspace's current state for diffing later | **`sigantry sync snapshot`** | no -- read-only | n/a |

The greenfield `sigantry workspace bootstrap` (Phase 13.5 / BOOTSTRAP-XX) is a sixth, distinct verb -- it provisions a NEW workspace from scratch (workspace + capacity bind + folders + Git connect + initialize). It is not a brownfield operation; do not confuse it with `sync pull` (which assumes the workspace already exists).

## 1. Overview

`sigantry sync pull` is the inverse of `sync apply`. The flow is:

1. Snapshot the workspace via two paginated REST calls (`list_folders` + `list_items`).
2. For each item type in scope (default: `Notebook,DataPipeline,SemanticModel,Report,SparkJobDefinition`), call the type-appropriate `Get Item Definition` REST endpoint.
   - Notebook items use `format=ipynb` (Council A) so the local file is the raw `.ipynb`, not the `.py`-formatted variant.
   - Other types use the default `fabricGitSource` format.
3. Unwrap the base64-encoded definition parts to local files.
4. Emit a `sync.yml` adjacent to the pulled sources mirroring the workspace topology -- preserving each item's workspace `logical_id` verbatim.

`sync pull` is read-only against the source workspace -- no `DeployRecord` is emitted (D-15). Run it whenever, as often as you like; the only side-effect is the local directory you wrote.

### 1.1. What `sync pull` does NOT do

This is the boundary `sync pull` enforces -- ADR-0012 formalises the verb landscape; this fence calls out the negative claims operators read from `sync pull` documentation most often. `sync pull`'s remit is **workspace -> local IaC tree**, period. Specifically:

- **It does not write to the workspace.** No POST / PATCH / DELETE leaves the toolkit; the only side-effect is on your local filesystem (the directory passed to `--into`). A read-quota throttle is the only network failure surface.
- **It does not emit a `DeployRecord`.** There is no audit ledger entry for a pull -- it is not a deploy, it is a snapshot-fetch (D-15). If you need a per-pull audit trail, commit the emitted `sync.yml` + sources to git -- the git history is the authoritative record.
- **It does not detect content-level drift.** Pull simply mirrors what the workspace currently holds; it makes no judgement about whether that state matches your manifest. Use `sigantry diff` for content-level drift detection.
- **It does not handle conflict resolution UI.** If `sync pull` and Native Git Sync race on the same workspace (the `gitConnection.sync_state != "Synced"` guard), the engine fails fast and tells the operator to commit pending Fabric-UI changes before re-running. There is no merge-conflict resolver.

**For first-time item creation, use `sigantry deploy run`. For drift detection, use `sigantry diff`.**

## 2. Operator setup

Same as [`apply.md`](apply.md) section 2. Auth via `DefaultAzureCredential`, confirm with `sigantry doctor`.

## 3. Command reference

```text
sigantry sync pull
  --workspace-id GUID      Source Fabric workspace. (required)
  --into        PATH       Target directory; must be empty or absent
                           unless --force. (required)
  --type        CSV        Comma-separated list of item types to pull
                           (default: Notebook,DataPipeline,SemanticModel,
                           Report,SparkJobDefinition).
  --force                  Allow --into to be a non-empty directory;
                           existing files MAY be overwritten. (D-21)
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Pull succeeded. |
| `1` | `PullTargetNotEmptyError` (non-empty `--into` without `--force`) or any other `SyncEngineError` subclass. |

## 4. IaC-fication workflow

A typical operator run looks like:

```bash
# 1. Pull the existing workspace into a fresh directory.
sigantry sync pull \
  --workspace-id <coe-guid> \
  --into ./fabric-iac

# Expected output:
#   sync pull succeeded items_pulled=27 sync_yml=fabric-iac/sync.yml

# 2. Inspect the emitted manifest.
cat ./fabric-iac/sync.yml | head -20

# 3. Commit the manifest + sources to git as the source of truth.
cd ./fabric-iac
git init
git add .
git commit -m "Initial IaC-fication of <workspace> via sigantry sync pull"

# 4. Apply the manifest to a fresh / staging workspace to verify the round-trip.
sigantry sync apply \
  --manifest ./sync.yml \
  --workspace-id <staging-guid>

# 5. Diff to confirm convergence.
sigantry diff -e staging \
  --workspace-id <staging-guid> \
  --manifest ./sync.yml \
  --output json | jq
```

## 5. Round-trip preservation invariant (D-22)

`sync pull` preserves each item's `logical_id` verbatim from the workspace snapshot. This means an immediate `sync apply` against the SAME workspace -- using the just-emitted `sync.yml` -- is a no-op:

```bash
sigantry sync pull --workspace-id <coe-guid> --into /tmp/pulled
sigantry sync apply --manifest /tmp/pulled/sync.yml --workspace-id <coe-guid>
# Expected output:
#   sync apply succeeded release_id=sync-... folders_created=0 items_moved=0
```

The live integration test in `tests/integration/sync/test_e2e_sync_round_trip.py` proves this against the user's `COE_F_ManagedData` workspace (gated on `SIGANTRY_FABRIC_TEST_*` env vars per V3-RISK-3).

A follow-up `sync apply` against a **fresh** (different) workspace will re-create folders and place items per the manifest -- but folder GUIDs and item GUIDs will differ (they're freshly minted), even though paths and display names match.

## 6. `--force` semantics

`sync pull` refuses to write into a non-empty target without `--force` (D-21). The flag is destructive -- existing files MAY be overwritten -- so it carries the same operator-only ergonomics as `git clean -f`:

```bash
# Refuses (target has content).
sigantry sync pull --workspace-id <id> --into ./existing-dir
# -> sync pull refused: target directory ./existing-dir is non-empty;
#    pass --force to allow overwrite (existing files may be clobbered).

# Proceeds (operator accepts the overwrite risk).
sigantry sync pull --workspace-id <id> --into ./existing-dir --force
```

When in doubt, pull into a fresh directory and use `rsync` / `cp -a` to merge.

## 7. Known limitations

- **Per-type packagers shipped:** Notebook, DataPipeline, SemanticModel, Report, SparkJobDefinition. The generic `.platform.j2` template covers types whose definition payload uses the `fabricGitSource` format.
- **Shell-only types deferred:** Lakehouse, Warehouse, SQLDatabase, MLExperiment. These have no per-type packager because their data lives in OneLake / SQL, not the `.platform` folder. A future Phase 14+ candidate.
- **Folder-less item types** (Dataflow Gen2, streaming semantic models, streaming dataflows) are pulled with `target_folder: /` regardless of UI placement (Council D #4 -- they have no `folderId`).
- **Conflict resolution UI** is out of scope. If `sync pull` and Native Git Sync race on the same workspace, the engine fails fast and tells the operator to commit pending changes via the Fabric UI before re-running.

## 8. Cross-references

- [`apply.md`](apply.md) -- `sigantry sync apply` push engine.
- [`folder-preservation.md`](folder-preservation.md) -- `folders[]` preservation set.
- [`snapshot-freshness.md`](snapshot-freshness.md) -- TTL'd cache discipline.
- [`../../reference/sync-schema.md`](../../reference/sync-schema.md) -- the `sync.yml` schema (the emitted manifest conforms).
- [`../../reference/api-stability.md`](../../reference/api-stability.md) -- Fabric REST stability matrix.
- Microsoft Learn: [Notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition).
