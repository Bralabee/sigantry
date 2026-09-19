# `sigantry sync apply` -- reconcile workspace folder topology against a manifest

**Phase 13 (SYNC-04 / SYNC-06 / INTROSPECT-01).** Operator-facing reference for the manifest-driven folder-reconcile engine. Reads a `sync.yml`, packages local sources into a tempdir staging tree, and reconciles the staging tree's folder topology + existing-item placement against the live workspace.

## 0. Decision matrix -- which sync verb am I looking for?

The five verbs the sync engine ships are easy to confuse. ADR-0012 formalises the boundary; this table is the operator's quick reference. Pay particular attention to the **first-time item creation** column -- that's the most common misread, and it's why your scaffold notebook didn't land when you first ran `sync apply`.

| You want to ... | Verb | Touches workspace? | First-time item creation? |
|---|---|---|---|
| Plan + reconcile folder topology against an existing workspace; move existing items into the manifest's `target_folder` paths | **`sigantry sync apply`** | yes -- creates / moves folders + relocates existing items | **NO** -- new items are staged locally but NOT published; see [§1.1](#11-what-sync-apply-does-not-do) |
| Mirror an existing workspace into a local IaC tree (`sync.yml` + sources) so future runs are no-op idempotent | **`sigantry sync pull`** | no -- read-only | n/a |
| Deploy first-time items + parameterise per environment (DEV/PREPROD/PROD) + write a `DeployRecord` for audit | **`sigantry deploy run`** | yes -- runs `fabric-cicd publish_all_items` | **YES** |
| Compare a manifest against a live workspace and report drift | **`sigantry diff`** | no -- read-only | n/a |
| Capture a workspace's current state for diffing later | **`sigantry sync snapshot`** | no -- read-only | n/a |

The greenfield `sigantry workspace bootstrap` (Phase 13.5 / BOOTSTRAP-XX) is a sixth, distinct verb -- it provisions a NEW workspace from scratch (workspace + capacity bind + folders + Git connect + initialize). It is not a brownfield operation; do not confuse it with `sync apply`.

## 1. Overview

`sigantry sync apply` is the canonical way to reconcile a `sync.yml`'s folder topology against a Fabric workspace. The flow is:

1. Parse `sync.yml` (validation per [`sync-schema.md`](../../reference/sync-schema.md)).
2. Snapshot the workspace via two paginated REST calls (`list_folders` + `list_items` -- INTROSPECT-01).
3. For each manifest item, run the type-appropriate packager (Notebook / DataPipeline / SemanticModel / Report / SparkJobDefinition / generic) into a staging tempdir. **The packager writes the item to local staging only -- it does NOT publish to the workspace.** See [§1.1](#11-what-sync-apply-does-not-do).
4. Pre-flight gate: refuse to apply if the workspace's `gitConnection.sync_state` is anything other than `"Synced"` (D-18 -- Council D constraint #6).
5. Call `sigantry_core.workspace.reconciler.reconcile_folders_from_repo(...)` against the staging tree -- delegates **folder create/move/delete and item-folder placement only**. No item is created or published in the workspace by this call.
6. Emit a `DeployRecord` to the audit ledger (`~/.sigantry/audit/deploys.jsonl`) with `provider="sync-engine"` and `release_id="sync-<ISO_TS>"`.
7. Cleanup the tempdir on success; on failure preserve it and print the path to stderr for debugging.

`sigantry sync apply` is **idempotent** -- applying the same `sync.yml` twice yields a no-op second run (zero `create_folder` / `move_item` operations). This is the strongest falsifiability gate the engine ships (Round-4-locked SPEC acceptance criterion, asserted by `tests/sync/test_apply.py::test_apply_idempotent_second_run_no_op`).

### 1.1. What `sync apply` does NOT do

This is the boundary that surprises operators most often -- the one ADR-0012 formalises. `sync apply`'s remit is **folder topology + existing-item placement** only. Specifically:

- **It does not create new items in the workspace.** If a manifest item's `(display_name, type)` is not already present in the workspace, `sync apply` packages the item into a tempdir but never POSTs to `/v1/workspaces/{id}/items`. Confirmed by inspection: `sigantry_core/sync/apply.py` and `sigantry_core/workspace/reconciler.py` contain zero references to `publish_all_items`, `FabricWorkspace`, or `fabric_cicd`. The `items_packaged` field in the success-line output reflects local staging only -- it is NOT a count of items deployed to the workspace.
- **It does not rename items in the workspace.** A manifest entry whose `display_name` differs from the live item's display name is treated as two distinct items (the old name's item is left alone; the new name's item is treated as not-yet-created and falls under the "does not create" rule above).
- **It does not delete items unless explicitly asked.** Items present in the workspace but absent from the manifest are preserved (per Council D constraint #5 -- the operator-created-paths preservation invariant). The `--unpublish-orphans --force` flag opts into deletion of orphans; it is OFF by default and requires both flags together.
- **It does not delete folders unless they become empty after orphan unpublish.** Folders in the workspace but absent from the manifest are preserved, again per Council D #5.
- **It does not write to OneLake.** Lakehouse / Warehouse data lives in OneLake / SQL, not in the `.platform` definition, so even when an item IS published the data side is out of scope for the sync engine. Use the Notebook / Spark layer for OneLake data manipulation.

**For first-time item creation, use `sigantry deploy run`** (Sigantry-native, fabric-cicd-driven, parameterised per environment, writes `DeployRecord`) **or a direct `POST /v1/workspaces/{id}/items` REST call** (lower-friction one-off scripts). See [`../pipeline-orchestration/deploy-with-tests.md`](../pipeline-orchestration/deploy-with-tests.md) and [`../../reference/parameters-yml.md`](../../reference/parameters-yml.md) for the `deploy run` setup; see the `msfabricpysdkcore` or raw REST docs for the one-off REST path.

### 1.2. CLI output trailer (D-26-bis)

When a `sync apply` invocation creates one or more new folders AND moves zero items, the success line is followed by a one-line operator hint reminding the reader that the manifest items have been staged but not published. The hint fires on this exact condition (`items_packaged > 0 AND folders_created > 0 AND items_moved == 0`) because that's the signature of "you just set up a new project folder and your items aren't in the workspace yet". On idempotent re-runs (`folders_created == 0`) the hint is suppressed.

### 1.3. First-time publish via `--with-publish`

Phase 17 ([ADR-0013](../../decisions/ADR-0013-sync-publish-parameters-resolution.md)) closes [ADR-0012](../../decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) Option C. The `--with-publish` flag composes folder reconcile and first-time `fabric-cicd publish_all_items` in one verb. This section is the operator-facing reference; the architectural decision is captured in ADR-0013, and the engine wiring lives at `sigantry_core/sync/apply.py` + `sigantry_core/deploy/sync_publish.py`.

#### When to use it

- First-time setup of a new project folder against a brownfield workspace (the case the 2026-05-01 brownfield test surfaced).
- Mixed-state runs where M existing items need reparenting AND N new items need publishing in one operator step.
- CI workflows that previously ran `sync apply` then `deploy run` back-to-back and now want a single audit-record per change set.

#### When NOT to use it

- Pure folder-shuffle runs (no new items) -- the default `sync apply` is faster and emits a lighter `provider="sync-engine"` record.
- Multi-env deployment fan-out (DEV -> PREPROD -> PROD) -- use `deploy run` with explicit `--environment` per stage; `--with-publish` is for one-shot first-time publish.
- Rollback -- `--with-publish` does NOT support `--rollback`; use `sigantry deploy run --rollback --to-release <id>` against the appropriate `R-...` record.

#### Command shape

```bash
sigantry sync apply \
  --manifest sync.yml \
  --workspace-id <guid> \
  --with-publish \
  --params parameters.yml \
  --environment DEV       # required if parameters.yml is multi-env; see below
```

#### Resolution rules (the load-bearing piece)

These two rules are codified in [ADR-0013](../../decisions/ADR-0013-sync-publish-parameters-resolution.md) and pinned by `tests/sync/test_cli_apply_with_publish.py`:

- `--with-publish` REQUIRES `--params <parameters.yml>`. If omitted, the CLI exits non-zero with an error message embedding the bare phrase `--with-publish requires --params <parameters.yml>` and pointing at this section + ADR-0013. There is NO sibling-file inference: the CLI does NOT look for `<manifest-dir>/parameters.yml`. This is deliberate -- the rule is captured in [ADR-0013](../../decisions/ADR-0013-sync-publish-parameters-resolution.md) §Decision rule 1.
- `--environment <name>` is REQUIRED when `parameters.yml` declares any environment beyond the wildcard `_ALL_`. Available environment names are listed in the error message. When `parameters.yml` is `_ALL_`-only, `--environment` is OPTIONAL. The detection rule is `ParametersConfig.environments_seen != set()`.

#### What the combined DeployRecord looks like

After a successful `--with-publish` invocation, the audit ledger receives ONE `DeployRecord`:

```json
{
  "workspace": "effa6941-0717-4578-b8e5-95339152f4b2",
  "release_id": "sync-publish-2026-05-01T12:34:56Z",
  "work_items": [],
  "fabric_items_changed": ["A.Notebook", "B.DataPipeline", "C.SemanticModel"],
  "test_evidence": {
    "provider": "sync-engine-publish",
    "outcome": "succeeded",
    "moved_items": "[\"A.Notebook\"]",
    "published_items": "[\"B.DataPipeline\", \"C.SemanticModel\"]"
  },
  "approver": "sync-engine",
  "audit_hash": "<sha256>",
  "created_at": "2026-05-01T12:34:56.000Z"
}
```

The `provider` field lives inside `test_evidence` (not as a top-level field) because `DeployRecord` is `extra="forbid"` + `frozen=True` -- schema-widening to add new top-level fields would break verify-without-trust on existing records (see [ADR-0011](../../decisions/ADR-0011-rename-to-sigantry.md) Sprint Note for the audit-hash determinism contract). Operators reading the audit ledger should `json.loads(record["test_evidence"]["published_items"])` to get the list back as a Python list. The `release_id` prefix `sync-publish-` (D-17-04) bucket-categorises these records distinct from `sync-<TS>` (sync-only), `R-...` (deploy-run), and `rollback-of-<id>-<TS>` (rollback).

#### Failure semantics

If the publish fails partway through (some items succeeded, one item raised), the folder reconcile remains committed (folder topology and publish are separate concerns) and the audit ledger receives a single combined record with `test_evidence['outcome'] == 'partial-failure'` and `test_evidence['failed_item'] == "<display_name>.<type>"` (best-effort -- see below). This separation is the load-bearing PUBLISH-05 invariant; operators can re-run with a manifest narrowed to the failed item without disturbing the folder topology.

**Best-effort `failed_item` extraction.** fabric-cicd 1.0.x does NOT expose a stable exception-shape contract for partial-failure; the toolkit extracts the failed item name from the exception text via regex against the known `items_to_include` set. If the field is empty (the `failed_item` key is absent from `test_evidence`), check stderr for the fabric-cicd traceback to identify the failed item manually. A future fabric-cicd 2.x stable contract may improve this. (Pitfall 3 from `17-RESEARCH.md`.)

#### Trailer suppression

When `--with-publish` is set, the D-26-bis boundary trailer described in [§1.2](#12-cli-output-trailer-d-26-bis) is SUPPRESSED. Under that flag the publish DID happen, so the trailer's prompt to "use deploy run to publish" would mislead and risk double-publish. The four-test contract at `tests/sync/test_cli_apply_boundary_hint.py` pins this behaviour. See ADR-0012 Consequences for the original trailer + ADR-0013 for the suppression rule (D-17-08).

#### Cross-references

- [ADR-0012](../../decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) -- boundary that this section's surface implements.
- [ADR-0013](../../decisions/ADR-0013-sync-publish-parameters-resolution.md) -- resolution rule + multi-env requirement.
- [`../pipeline-orchestration/deploy-with-tests.md`](../pipeline-orchestration/deploy-with-tests.md) -- when to use `deploy run` instead.

## 2. Operator setup

1. Install Sigantry into your conda env (or pipx-installed shell):

   ```bash
   conda activate fabric-dataops-toolkits
   pip install sigantry-core
   ```

2. Configure auth via `DefaultAzureCredential`:

   ```bash
   az login                              # interactive
   # OR
   azd auth login                        # interactive (azd-flavoured)
   # OR (CI)
   export AZURE_CLIENT_ID=...            # WIF
   export AZURE_TENANT_ID=...
   export AZURE_FEDERATED_TOKEN=...
   ```

3. Confirm Sigantry can talk to Fabric:

   ```bash
   sigantry doctor
   sigantry workspace list --workspace-id <coe-guid>
   ```

   `doctor` resolves the toolkit settings + verifies the auth chain. If it exits non-zero, fix the underlying error before invoking `sync apply`.

4. Author your `sync.yml` (see section 5 for the canonical 9-notebook example) and confirm it parses cleanly:

   ```bash
   sigantry sync apply --manifest sync.yml --workspace-id <id> --dry-run
   ```

## 3. Command reference

```text
sigantry sync apply
  --manifest    PATH       Path to sync.yml. (required)
  --workspace-id GUID      Target Fabric workspace. (required)
  --environment LABEL      Optional parameters.yml env label (reserved).
  --audit-dir   PATH       Override ~/.sigantry/audit/ (used in tests + CI).
  --dry-run                Print the would-be reconciler plan and exit 0
                           without applying.
```

Exit codes (D-26):

| Code | Meaning |
|------|---------|
| `0` | Apply succeeded, OR `--dry-run` returned a clean plan. |
| `1` | `ManifestValidationError` (manifest bad shape) or any other `SyncEngineError` subclass. |
| `2` | `WorkspacePendingGitUpdateError` (Git Sync state not yet `Synced` -- D-18). Distinguishable from validation errors so CI runners can branch on the exit code. |

## 4. Configuration

The runtime gate `workflow.preview_apis_acknowledged` controls the one-time Preview-API warning emitted on first invocation per process (see [`api-stability.md`](../../reference/api-stability.md)). Acknowledge in `.fabric-dataops.toml`:

```toml
[workflow]
preview_apis_acknowledged = true
```

The manifest's `folders[]` preservation set declares operator-created paths the engine MUST NOT auto-cleanup (Council D #5 -- see [`folder-preservation.md`](folder-preservation.md)).

The audit ledger location follows Phase 11 / 12 convention: `~/.sigantry/audit/deploys.jsonl` (mode 0o600, fsync'd, dir 0o700). Override via `--audit-dir` for hermetic CI runs.

## 5. The user's primary use case (canonical example)

The canonical Phase 13 fixture: 9 raw `.ipynb` files at `/home/sanmi/Documents/HS2/HS2_PROJECTS_2025/1_AIMS_LOCAL_2026/notebooks/` syncing into the `COE_F_ManagedData` workspace at `AIMS/01_NOTEBOOKS_AIMS_2026_V2/`.

### `sync.yml`

```yaml
# /home/sanmi/Documents/HS2/HS2_PROJECTS_2025/1_AIMS_LOCAL_2026/notebooks/sync.yml
schema_version: "1.0.0"
items:
  - {local_path: 00_AIMS_Orchestration.ipynb,            type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 00_AIMS_Orchestration}
  - {local_path: 01_AIMS_Bronze_Ingest.ipynb,            type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 01_AIMS_Bronze_Ingest}
  - {local_path: 02_AIMS_Bronze_Validate.ipynb,          type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 02_AIMS_Bronze_Validate}
  - {local_path: 03_AIMS_Silver_Standardise.ipynb,       type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 03_AIMS_Silver_Standardise}
  - {local_path: 04_AIMS_Silver_DQ.ipynb,                type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 04_AIMS_Silver_DQ}
  - {local_path: 05_AIMS_Gold_Conform.ipynb,             type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 05_AIMS_Gold_Conform}
  - {local_path: 06_AIMS_Gold_Aggregate.ipynb,           type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 06_AIMS_Gold_Aggregate}
  - {local_path: 07_AIMS_Publish.ipynb,                  type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 07_AIMS_Publish}
  - {local_path: 08_AIMS_Teardown.ipynb,                 type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: 08_AIMS_Teardown}
folders: []
```

> **Note:** the exact list of 9 notebooks above is illustrative -- the canonical test scenario is "9 raw `.ipynb` files in the AIMS notebooks directory" syncing into `AIMS/01_NOTEBOOKS_AIMS_2026_V2/`. Adjust `local_path` + `display_name` to match what your repo actually carries. The pattern (one entry per `.ipynb`, all targeting the same folder) is what's load-bearing.

### Invocation

```bash
cd /home/sanmi/Documents/HS2/HS2_PROJECTS_2025/1_AIMS_LOCAL_2026/notebooks/

# 1. Dry-run first.
sigantry sync apply \
  --manifest sync.yml \
  --workspace-id <coe-guid> \
  --dry-run

# Expected output:
#   dry-run would create 1 folder(s) and move 9 item(s) for 9 manifest item(s).

# 2. Apply.
sigantry sync apply \
  --manifest sync.yml \
  --workspace-id <coe-guid>

# Expected output:
#   sync apply succeeded release_id=sync-2026-04-27T... items_packaged=9
#     folders_created=1 items_moved=9
```

### Staging tree (intermediate, packager output)

`NotebookPackager` produces this layout in a tempdir which `fabric_cicd` then walks:

```text
<tempdir>/
  AIMS/
    01_NOTEBOOKS_AIMS_2026_V2/
      00_AIMS_Orchestration.Notebook/
        .platform                  # schema 2.0; logicalId UUID4 from sidecar
        notebook-content.ipynb     # LF-normalised raw .ipynb
      01_AIMS_Bronze_Ingest.Notebook/
        .platform
        notebook-content.ipynb
      ... (7 more)
```

`logical_id` values are persisted in `notebooks/.sigantry/notebook-ids.json`. **Commit this file to git** so re-deploys from another machine produce stable identifiers (D-11).

### DeployRecord

After a successful `sigantry sync apply`:

```bash
sigantry release list --limit 1 --json
```

Returns one record (newest first):

```json
[
  {
    "release_id": "sync-2026-04-27T14-30-05Z",
    "provider": "sync-engine",
    "workspace": "<coe-guid>",
    "fabric_items_changed": [
      {"logical_name": "00_AIMS_Orchestration", "item_type": "Notebook", "fabric_item_id": "<id>"},
      ...
    ],
    "outcome": "succeeded",
    "audit_hash": "<sha256>"
  }
]
```

### Idempotency confirmation

Re-run the same command:

```bash
sigantry sync apply --manifest sync.yml --workspace-id <coe-guid>
```

Expected output:

```text
sync apply succeeded release_id=sync-2026-04-27T... items_packaged=9
  folders_created=0 items_moved=0
```

Zero create / move operations on the second run. This is the locked Round-4 idempotency invariant.

## 6. Troubleshooting

| Symptom | Likely cause | Remediation |
|---------|--------------|-------------|
| Exit 2 -- `WorkspacePendingGitUpdateError: Workspace has pending Git Sync updates ... sync_state: "Updating"` | Native Git Sync queued an update that has not yet committed. | Open the workspace in the Fabric portal -> Source control -> commit or discard pending changes -> re-run `sync apply`. |
| Exit 1 -- `Manifest validation failed: 1 violation` with `target_folder_depth_exceeded` | A manifest item declares more than 10 path segments. | Refactor the target hierarchy to <=10 levels. |
| Exit 1 -- `Manifest validation failed` with `target_folder_banned_chars` | A path contains one of `~"#.&*:<>?/{|}`, leading/trailing space, or a control character. | Rename the folder; only ASCII letters / digits / hyphen / underscore / forward-slash are recommended. |
| Exit 1 -- `Manifest validation failed` with `unknown_item_type` | The `type` value isn't a member of `fabric_cicd.constants.ItemType`. | Check spelling and PascalCase: `Notebook`, `DataPipeline`, `SemanticModel`, `Report`, `SparkJobDefinition`. |
| Exit 1 -- `sync apply failed: <ReconcilerWrapError>: ...` | Underlying reconciler error (folder name collision, REST 4xx). | The tempdir is preserved; its path is printed to stderr. Inspect the staging tree at `/tmp/sigantry-sync-*` and re-run after fixing. |
| Warning -- `Dataflow 'X' cannot be assigned a folder; will be placed at workspace root.` | Folder-less type (Council D #4 -- D-07 / SYNC-06). | Either move the item to `target_folder: /` in the manifest, or accept the override and the warning. |
| One-time WARNING about Preview Folders REST endpoint | `workflow.preview_apis_acknowledged` is `False` (default). | Add `[workflow]\npreview_apis_acknowledged = true` to `.fabric-dataops.toml` (see [`api-stability.md`](../../reference/api-stability.md)). |

## 7. Known limitations

- **Folder-less item types** (Dataflow Gen2, streaming semantic models, streaming dataflows) are placed at workspace root regardless of `target_folder` (Council D #4).
- **Greenfield workspace bootstrap** (create workspace + bind capacity + connect Git) is deferred to Phase 13.5 / a future sub-phase. `sync apply` requires the workspace to already exist.
- **Content-level drift** is NOT detected -- `sigantry diff` only catches metadata drift (display_name, type, folder_path). For content drift, run `sync pull` and compare with `git diff`.
- **Cross-environment rollback** (DEV -> PROD) is rejected by the rollback engine (Phase 12 Pitfall 9). Stay within a single workspace per `release_id`.
- **Auto-cleanup** -- `fabric-cicd._unpublish_folders` deletes empty folders by default. Add operator-created paths to `folders[]` (see [`folder-preservation.md`](folder-preservation.md)).

## 8. Cross-references

- [`pull.md`](pull.md) -- `sigantry sync pull` IaC-fication workflow.
- [`folder-preservation.md`](folder-preservation.md) -- `folders[]` preservation set + Council D #5.
- [`snapshot-freshness.md`](snapshot-freshness.md) -- TTL'd cache discipline (INTROSPECT-03 / D-10).
- [`../drift-detection/scheduled-drift.md`](../drift-detection/scheduled-drift.md) -- ADO + GHA scheduled drift-check templates.
- [`../../reference/sync-schema.md`](../../reference/sync-schema.md) -- the `sync.yml` schema.
- [`../../reference/api-stability.md`](../../reference/api-stability.md) -- Fabric REST stability matrix.
- [`../pipeline-orchestration/deploy-with-tests.md`](../pipeline-orchestration/deploy-with-tests.md) -- Phase 12 deploy + rollback workflow.
- [`../work-item-traceability/comment-rendering.md`](../work-item-traceability/comment-rendering.md) -- Phase 11 audit-comment payload.
