# `sync.yml` Schema Reference

**Phase 13 (SYNC-01).** Operator reference for the manifest contract consumed by `sigantry sync apply`, `sigantry sync pull`, and `sigantry diff`.

The committed JSON Schema lives at [`docs/reference/sync-schema.json`](sync-schema.json) -- runtime-equality with `SyncManifest.model_json_schema()` is enforced by `tests/sync/test_sync_schema_committed.py` (drift catcher, D-08).

## 1. Overview

`sync.yml` is the declarative input the Sigantry sync engine consumes. It enumerates the set of local source items the operator wants reflected inside a Fabric workspace, the target folder for each, the canonical display name, and (optionally) the workspace-side `logical_id` so subsequent `sync apply` runs are no-op idempotent (D-22).

Three Phase 13 surfaces consume it:

- `sigantry sync apply --manifest sync.yml --workspace-id <id>` (SYNC-04) -- pushes manifest items into the workspace.
- `sigantry sync pull --workspace-id <id> --into <dir>` (SYNC-05) -- emits a `sync.yml` mirroring the workspace's actual topology.
- `sigantry diff -e <env> --workspace-id <id> --manifest sync.yml` (DRIFT-01) -- compares manifest vs live workspace.

## 2. Schema version (SemVer commitment, D-05 / D-08)

The first key in every `sync.yml` is `schema_version`. v3.0 ships **`schema_version: "1.0.0"`** (or the equivalent two-component shorthand `"1.0"`). The `SyncManifest` pydantic v2 model accepts:

| Value | Behaviour |
|-------|-----------|
| `"1.0"` | Accepted (forward-compat alias). |
| `"1.0.0"` | Accepted (canonical form). |
| `"1.0.1"`, `"1.1.0"`, ... future minor / patch | Accepted (forward-compat: minor versions accepted). |
| `"2.0.0"` | **Rejected** with `ManifestValidationError` (forward-compat: major version mismatch). |

The schema's keyset is locked by the drift-catcher test in `tests/sync/test_sync_schema_committed.py`: any future addition or removal of a top-level / nested field requires updating the committed `sync-schema.json` AND bumping the schema version. The contract is SemVer-pinned for the whole v3.0 line.

## 3. Top-level fields

```yaml
schema_version: "1.0.0"
items:
  - local_path: notebooks/00_AIMS_Orchestration.ipynb
    type: Notebook
    target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2
    display_name: 00_AIMS_Orchestration
    # logical_id: <uuid>  # optional; minted by the packager when absent
folders:
  - /raw/UI-Created
  - /curated/Adhoc
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `str` | yes | SemVer-pinned manifest version. See section 2. |
| `items` | `list[SyncItem]` | yes | Items the engine will reconcile against the workspace. |
| `folders` | `list[str]` | no (default `[]`) | Folder paths to exclude from `sync apply --unpublish-orphans` cleanup (paths AND their ancestors). Has no effect on the additive default `sync apply`. See [folder-preservation runbook](../runbooks/sync/folder-preservation.md). Council D constraint #5. |

`additionalProperties: false` -- unknown top-level keys raise `ManifestValidationError` so a typo never silently no-ops.

## 4. `SyncItem` fields

```yaml
items:
  - local_path: notebooks/00_AIMS_Orchestration.ipynb
    type: Notebook
    target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2
    display_name: 00_AIMS_Orchestration
    logical_id: 1d2f3e4a-9b6c-4f1a-b0e2-7c8d5a4f1b3e
```

| Field | Type | Required | Validation | Decision |
|-------|------|----------|------------|----------|
| `local_path` | `Path` (string) | yes | Forward-slash path relative to the manifest file. | D-06 |
| `type` | `str` | yes | One of the canonical PascalCase types from `fabric_cicd.constants.ItemType` (e.g. `Notebook`, `DataPipeline`, `SemanticModel`, `Report`, `SparkJobDefinition`). Folder-less types (see section 6) trigger a routing override. | D-06 / D-07 |
| `target_folder` | `str` | no (default `/`) | Forward-slash path; leading `/` optional; `/` = workspace root. Validated against folder-name banned-char rules + 10-level depth cap (Constraint 4 below). | D-06 |
| `display_name` | `str` | yes | Validated against folder-name banned-char rules + 256-char cap. | D-06 |
| `logical_id` | `str \| None` | no (default `None`) | UUID4 string; minted by the packager + persisted to the sidecar manifest (`<source-dir>/.sigantry/<type>-ids.json`) when absent so re-runs are stable. | D-11..13 |

## 5. Validation rules

The pre-flight validator in `sigantry_core.sync.manifest.SyncManifest` enforces:

| Rule | Failure mode |
|------|--------------|
| **Folder depth cap** -- 10 levels per Council D #3. | `ManifestValidationError`: violation `target_folder_depth_exceeded` per offending item. |
| **Banned characters** in folder names -- `~"#.&*:<>?/{|}`, no leading/trailing spaces, no C0/C1 controls. | `ManifestValidationError`: violation `target_folder_banned_chars` or `display_name_banned_chars`. |
| **Schema-version major mismatch** -- e.g. `"2.0.0"`. | `ManifestValidationError`: violation `schema_version_major_mismatch`. |
| **Unknown item type** -- not a member of `fabric_cicd.constants.ItemType`. | `ManifestValidationError`: violation `unknown_item_type`. |
| **Folder-less type with non-root `target_folder`** | WARNING (not error) -- engine forces `target_folder = "/"`. See section 6. |
| **Duplicate `logical_id`** across items | `ManifestValidationError`: violation `duplicate_logical_id`. |
| **Missing required field** (`local_path`, `type`, `display_name`) | `ManifestValidationError`: violation `missing_required_field`. |

Each violation entry carries `{rule, item_index, field, value, message}` -- the CLI surfaces them inline (`sigantry sync apply` exits 1 with a Rich-rendered list).

### Example -- valid manifest

```yaml
schema_version: "1.0.0"
items:
  - local_path: notebooks/orchestrate.ipynb
    type: Notebook
    target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2
    display_name: orchestrate
folders: []
```

Validation passes; no warnings.

### Example -- invalid manifest (depth cap)

```yaml
schema_version: "1.0.0"
items:
  - local_path: notebooks/x.ipynb
    type: Notebook
    target_folder: a/b/c/d/e/f/g/h/i/j/k   # 11 levels -- exceeds the 10-level cap
    display_name: x
```

```text
ManifestValidationError: 1 violation
  - rule=target_folder_depth_exceeded item_index=0 field=target_folder
    value="a/b/c/d/e/f/g/h/i/j/k" message="folder path exceeds 10-level cap"
```

## 6. Folder-less item types (D-07 / SYNC-06)

Three Fabric item types cannot carry a `folderId` (Council D constraint #4):

- `Dataflow` (Gen2)
- streaming semantic models
- streaming dataflows

The validator emits a `WARNING` and forces `target_folder = "/"` regardless of what the manifest declares. The engine proceeds with `exit_code = 0`. A worked example:

```yaml
schema_version: "1.0.0"
items:
  - local_path: dataflows/ingest_orders.json
    type: Dataflow
    target_folder: pipelines/ingest   # WILL BE OVERRIDDEN to /
    display_name: ingest_orders
```

```text
WARNING: Dataflow 'ingest_orders' cannot be assigned a folder; will be placed at workspace root.
```

## 7. Worked example -- the AIMS 9-notebook canonical fixture

The user's primary use case (CONTEXT.md "Specific Ideas") -- 9 raw `.ipynb` files at `/home/sanmi/Documents/HS2/HS2_PROJECTS_2025/1_AIMS_LOCAL_2026/notebooks/` syncing into the `COE_F_ManagedData` workspace at `AIMS/01_NOTEBOOKS_AIMS_2026_V2/`:

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

After the first `sigantry sync apply --manifest sync.yml --workspace-id <coe-guid>`, the staging tempdir produced by `NotebookPackager` looks like:

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

The packager persists `logical_id` values in `notebooks/.sigantry/notebook-ids.json` -- check this file into git so re-deploys from another machine produce stable identifiers.

## 8. Cross-references

- [`apply.md`](../runbooks/sync/apply.md) -- `sigantry sync apply` operator runbook (with the same 9-notebook fixture).
- [`pull.md`](../runbooks/sync/pull.md) -- `sigantry sync pull` IaC-fication workflow.
- [`folder-preservation.md`](../runbooks/sync/folder-preservation.md) -- the `folders[]` preservation set + Council D #5.
- [`snapshot-freshness.md`](../runbooks/sync/snapshot-freshness.md) -- INTROSPECT-03 / D-10 TTL'd cache discipline.
- [`scheduled-drift.md`](../runbooks/drift-detection/scheduled-drift.md) -- ADO + GHA scheduled drift-check templates.
- [`api-stability.md`](api-stability.md) -- Fabric REST endpoint stability matrix (Folders endpoint Preview status).
- Microsoft Learn: [Items - List Items](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items) (GA), [Folders REST API](https://learn.microsoft.com/en-us/rest/api/fabric/core/folders) (Preview), [Notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition).
