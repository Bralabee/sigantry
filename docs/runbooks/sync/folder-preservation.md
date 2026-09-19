# Folder preservation -- `folders[]` and `--unpublish-orphans`

**Phase 13 (Council D constraint #5) + audit-2026-05-05 wire-through.** Operator-facing reference for the manifest's `folders[]` preservation set and how it interacts with `sigantry sync apply --unpublish-orphans`.

## 1. Overview

By default, `sigantry sync apply` is **purely additive** -- it creates folders and moves items, but **does not delete any folders or items** even if they are absent from the manifest. Operators who want to clean up workspace folders that have drifted away from the manifest must opt in with `sigantry sync apply --unpublish-orphans` (default off, SemVer-safe).

When `--unpublish-orphans` is set, the reconciler computes the orphan-folder list (workspace folders that are not required by any manifest item), filters it through the manifest's `folders[]` preservation set, and deletes the remainder. The preservation filter is **inclusive of ancestors** -- declaring `/raw/UI-Created` as preserved automatically keeps `/raw` alive too, because leaf-first deletion of the parent would otherwise drop the protected child.

`--with-publish` (Phase 17 / SYNC-PUBLISH) is a separate flag that drives `fabric-cicd.publish_all_items` for first-time item creation. fabric-cicd 1.0.x has its own internal `_unpublish_folders` behaviour during publish; that behaviour is **not** filtered by `manifest.folders[]` today. If you need to publish first-time items AND protect UI-created folders simultaneously, the safest order is: `sync apply --with-publish` for the items, then a separate `sync apply --unpublish-orphans` pass to enforce the preservation contract over the post-publish workspace state. (Or skip `--unpublish-orphans` entirely if you don't need cleanup.)

## 2. Why this matters

If an operator created a folder via the Fabric portal UI -- e.g. an analyst manually created `/raw/UI-Created` to drop ad-hoc files into -- and that folder is NOT declared in the manifest's `folders[]` preservation set, then a future `sync apply --unpublish-orphans` run will delete it because the staging tree (built from `sync.yml`'s `items[]`) doesn't reach into that path.

The deletion is non-recoverable without a workspace restore. **If you intend to use `--unpublish-orphans`, treat the preservation set as a hard constraint, not a nice-to-have.** A pure `sync apply` (no `--unpublish-orphans`) leaves these folders untouched regardless.

## 3. The manifest preservation set

Declare any folder paths you want preserved in the manifest's `folders[]` array:

```yaml
schema_version: "1.0.0"
items:
  - {local_path: notebooks/orchestrate.ipynb, type: Notebook, target_folder: AIMS, display_name: orchestrate}
folders:
  - /raw/UI-Created
  - /curated/Adhoc
  - AIMS/02_NOTEBOOKS_AIMS_2026_V2_ARCHIVE  # leading slash optional
```

Each entry is a forward-slash path (leading `/` optional). The engine retains the folder even if it's empty. Validation rules (depth cap, banned chars) apply to entries the same way they do to `items[].target_folder`.

The `folders[]` field is `additionalProperties: false`-clean -- only the listed paths are preserved. Anything else the engine considers "unowned" by the manifest gets the auto-cleanup treatment.

## 4. Worked example

Workspace state before the run:

```text
/
  AIMS/
    01_NOTEBOOKS_AIMS_2026_V2/
      orchestrate (Notebook)
  raw/
    UI-Created/                     <-- analyst created via Fabric UI; empty
  curated/
    Adhoc/                          <-- analyst created via Fabric UI; empty
  archive/                          <-- leftover from a previous experiment; empty
```

`sync.yml`:

```yaml
schema_version: "1.0.0"
items:
  - {local_path: notebooks/orchestrate.ipynb, type: Notebook, target_folder: AIMS/01_NOTEBOOKS_AIMS_2026_V2, display_name: orchestrate}
folders:
  - /raw/UI-Created
  - /curated/Adhoc
```

After `sigantry sync apply` (no `--unpublish-orphans`):

```text
/
  AIMS/
    01_NOTEBOOKS_AIMS_2026_V2/
      orchestrate (Notebook)
  raw/
    UI-Created/                     <-- untouched (additive default)
  curated/
    Adhoc/                          <-- untouched
  archive/                          <-- untouched (additive default)
```

After `sigantry sync apply --unpublish-orphans` (opt-in cleanup pass):

```text
/
  AIMS/
    01_NOTEBOOKS_AIMS_2026_V2/
      orchestrate (Notebook)
  raw/
    UI-Created/                     <-- preserved (in folders[])
  curated/
    Adhoc/                          <-- preserved (in folders[])
                                    <-- /archive/ is GONE (orphan-cleanup)
```

The `/archive/` folder was orphan and not in `folders[]`, so the reconciler's leaf-first orphan-deletion sweep removed it. `/raw/UI-Created` and `/curated/Adhoc` were filtered out of the deletion list by the preservation set; their parents `/raw` and `/curated` are kept alive transitively (ancestor-inclusive filtering).

## 5. Verification

Run `sigantry sync apply --dry-run --unpublish-orphans` and inspect the printed plan. The plan structure includes a `delete_folders[]` array; entries in `folders[]` (and their ancestors) MUST NOT appear there:

```bash
sigantry sync apply --manifest sync.yml --workspace-id <id> --dry-run --unpublish-orphans
```

If you see a folder you wanted preserved in the deletion list, add it to `folders[]` and re-run the dry-run. Iterate until the deletion plan only contains paths you genuinely want gone.

The plan currently prints to the Rich console only. Structured `--output json` for `sync apply` (deletion-plan inclusive) is not yet implemented; track an enhancement in [`V3.X-ROADMAP.md`](../../operator/V3.X-ROADMAP.md) if you need a CI-gateable JSON shape.

The contract is pinned by these falsifiability tests in `tests/sigantry_core/workspace/test_reconciler.py`:

- `test_normalise_preserve_set_expands_ancestors_and_strips_slashes`
- `test_plan_preserve_paths_excludes_exact_orphan_folder`
- `test_plan_preserve_paths_excludes_ancestor_of_preserved`
- `test_plan_preserve_paths_is_no_op_when_include_orphans_false`
- `test_reconcile_folders_from_repo_threads_preserve_paths_through`

## 6. Cross-references

- [`apply.md`](apply.md) -- `sigantry sync apply` runbook.
- [`pull.md`](pull.md) -- `sync pull` emits an empty `folders[]` by default; operators add UI-created paths post-pull before committing the manifest.
- [`../../reference/sync-schema.md`](../../reference/sync-schema.md) -- `folders[]` field schema.
- [`../../reference/api-stability.md`](../../reference/api-stability.md) -- the Folders REST endpoints involved are Preview.
- `sigantry_core/workspace/reconciler.py::_normalise_preserve_set` -- the helper that turns operator-supplied paths into the ancestor-inclusive exclusion set.
- `fabric-cicd` source -- the `_unpublish_folders` routine in `fabric_workspace.py` is invoked internally when `sync apply --with-publish` runs `publish_all_items`; that path is **not** filtered by `manifest.folders[]` today.
