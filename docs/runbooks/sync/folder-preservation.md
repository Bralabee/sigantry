# Folder preservation -- `folders[]` and `--unpublish-orphans`

**Phase 13 (Council D constraint #5) + audit-2026-05-05 wire-through.** Operator-facing reference for the manifest's `folders[]` preservation set and how it interacts with `sigantry sync apply --unpublish-orphans`.

## 1. Overview

By default, `sigantry sync apply` is **purely additive** -- it creates folders and moves items, but **does not delete any folders or items** even if they are absent from the manifest. Operators who want to clean up workspace folders that have drifted away from the manifest must opt in with `sigantry sync apply --unpublish-orphans --force` (default off, SemVer-safe; without `--force` a real run is refused before it touches the workspace).

When `--unpublish-orphans` is set, the reconciler computes the orphan items (workspace items whose `(display name, type)` is absent from the manifest) and the orphan folders (workspace folders no manifest item needs), filters both through the rules below, and deletes the remainder.

**What a forced cleanup keeps:**

- **A declared folder and its contents.** A folder listed in `folders[]` is kept with everything below it: its subfolders, empty or not, and every item in them, whatever the item's type (#101).
- **The ancestors of a declared folder, as folders.** Declaring `/raw/UI-Created` keeps `/raw` alive too, because leaf-first deletion of the parent would otherwise drop the protected child. The ancestor's other contents were never declared: an orphan notebook sitting directly in `/raw` is still deleted, and so is an empty sibling folder such as `/raw/old`.
- **Every item of a type sync cannot create.** `sync apply` can package only the types in `PACKAGER_REGISTRY` (`sigantry_core/sync/packagers/__init__.py`): Notebook, DataPipeline, SemanticModel, Report and SparkJobDefinition. A Lakehouse, Warehouse, SQLDatabase, Environment or any other type can never be in the staging tree, so its absence says nothing and it is never deleted (#102).
- **Any folder that still holds a kept item, and its ancestors**, since Fabric does not delete a folder that is not empty.

**What a forced cleanup deletes:** an orphan item of one of the five types above that sits outside every declared folder, and an orphan folder that none of the rules above keeps, an empty one included.

The dry run lists both: each item and folder it would delete, with the folder each item is in (`Notebook: Adhoc (in raw)`), then each item it keeps and why (`Lakehouse: Sales (in old) -- kept: sync cannot create this type`, `Notebook: Adhoc (in raw/UI-Created) -- kept: in a folder that folders[] preserves`). Read it before confirming with `--force`.

`--with-publish` (Phase 17 / SYNC-PUBLISH) is a separate flag that drives `fabric-cicd.publish_all_items` for first-time item creation. fabric-cicd 1.0.x has its own internal `_unpublish_folders` behaviour during publish; that behaviour is **not** filtered by `manifest.folders[]` today. If you need to publish first-time items AND protect UI-created folders simultaneously, the safest order is: `sync apply --with-publish` for the items, then a separate `sync apply --unpublish-orphans` pass to enforce the preservation contract over the post-publish workspace state: first with `--dry-run` to read what it would delete (section 5), then with `--force` to delete it. (Or skip `--unpublish-orphans` entirely if you don't need cleanup.)

## 2. Why this matters

If an operator created a folder via the Fabric portal UI -- e.g. an analyst manually created `/raw/UI-Created` to drop ad-hoc files into -- and that folder is NOT declared in the manifest's `folders[]` preservation set, then a future `sync apply --unpublish-orphans --force` run will delete it because the staging tree (built from `sync.yml`'s `items[]`) doesn't reach into that path.

The deletion is non-recoverable without a workspace restore. **If you intend to use `--unpublish-orphans`, treat the preservation set as a hard constraint, not a nice-to-have.** A pure `sync apply` (no `--unpublish-orphans`) leaves these folders untouched regardless.

## 3. The manifest preservation set

Declare any folder paths you want preserved in the manifest's `folders[]` array:

```yaml
schema_version: "1.0.0"
items:
  - {local_path: notebooks/orchestrate.ipynb, type: Notebook, target_folder: Orders, display_name: orchestrate}
folders:
  - /raw/UI-Created
  - /curated/Adhoc
  - Orders/02_Notebooks_Archive  # leading slash optional
```

Each entry is a forward-slash path (leading `/` optional). The engine retains the folder even if it's empty. Validation rules (depth cap, banned chars) apply to entries the same way they do to `items[].target_folder`.

The `folders[]` field is `additionalProperties: false`-clean -- only the listed paths (with everything below them) and their ancestors are preserved. Any other folder the engine considers "unowned" by the manifest gets the auto-cleanup treatment, unless it still holds an item the cleanup keeps (section 1).

## 4. Worked example

Workspace state before the run:

```text
/
  Orders/
    01_Notebooks/
      orchestrate (Notebook)
  raw/
    UI-Created/                     <-- analyst created via Fabric UI
      scratch (Notebook)            <-- analyst's ad-hoc notebook; not in sync.yml
  curated/
    Adhoc/                          <-- analyst created via Fabric UI; empty
  archive/                          <-- leftover from a previous experiment
    old_etl (Notebook)              <-- not in sync.yml
  Sales (Lakehouse)                 <-- not in sync.yml (no manifest can list one)
```

`sync.yml`:

```yaml
schema_version: "1.0.0"
items:
  - {local_path: notebooks/orchestrate.ipynb, type: Notebook, target_folder: Orders/01_Notebooks, display_name: orchestrate}
folders:
  - /raw/UI-Created
  - /curated/Adhoc
```

After `sigantry sync apply` (no `--unpublish-orphans`):

```text
/
  Orders/
    01_Notebooks/
      orchestrate (Notebook)
  raw/
    UI-Created/                     <-- untouched (additive default)
      scratch (Notebook)
  curated/
    Adhoc/                          <-- untouched
  archive/                          <-- untouched (additive default)
    old_etl (Notebook)
  Sales (Lakehouse)
```

After `sigantry sync apply --unpublish-orphans --force` (opt-in cleanup pass; without `--force` a real run is refused):

```text
/
  Orders/
    01_Notebooks/
      orchestrate (Notebook)
  raw/
    UI-Created/                     <-- preserved (in folders[])
      scratch (Notebook)            <-- kept: in a folder that folders[] preserves
  curated/
    Adhoc/                          <-- preserved (in folders[])
  Sales (Lakehouse)                 <-- kept: sync cannot create this type
                                    <-- /archive/ and old_etl are GONE (orphan-cleanup)
```

`old_etl` is a Notebook outside every declared folder, so it was deleted; `/archive/` was then empty, orphan and not in `folders[]`, so the leaf-first folder sweep removed it. `/raw/UI-Created` and `/curated/Adhoc` were kept by the preservation set, `scratch` with its folder; their parents `/raw` and `/curated` are kept alive transitively (ancestor-inclusive filtering). `Sales` is a Lakehouse, a type `sync apply` cannot create, so it is kept wherever it sits; had it been inside `/archive/`, that folder would have been kept too.

## 5. Verification

Run `sigantry sync apply --dry-run --unpublish-orphans` and read the lists it prints: every workspace item (type and name) and every folder it would delete, then every item it keeps and why. A path in `folders[]` (or an ancestor of one, or a folder below one) MUST NOT appear in the deletion list, and nor may any item you did not expect to lose. A Lakehouse or Warehouse appears only in the kept list. A dry run needs no `--force`:

```bash
sigantry sync apply --manifest sync.yml --workspace-id <id> --dry-run --unpublish-orphans
```

If you see a folder you wanted preserved in the deletion list, add it to `folders[]` and re-run the dry-run. Iterate until the deletion plan only contains paths you genuinely want gone.

The plan currently prints to the Rich console only. Structured `--output json` for `sync apply` (deletion-plan inclusive) is not yet implemented; open an issue if you need a CI-gateable JSON shape.

The contract is pinned by these falsifiability tests in `tests/sigantry_core/workspace/test_reconciler.py`:

- `test_normalise_preserve_set_expands_ancestors_and_strips_slashes`
- `test_plan_preserve_paths_excludes_exact_orphan_folder`
- `test_plan_preserve_paths_excludes_ancestor_of_preserved`
- `test_plan_preserve_paths_is_no_op_when_include_orphans_false`
- `test_reconcile_folders_from_repo_threads_preserve_paths_through`
- `test_plan_orphan_item_in_a_preserved_folder_is_kept` (#101)
- `test_plan_preserved_folder_keeps_its_whole_subtree`
- `test_plan_ancestor_of_a_preserved_folder_does_not_keep_its_other_items`
- `test_plan_unmanaged_type_is_kept_and_its_folders_survive` (#102)
- `test_plan_without_managed_types_deletes_no_item`

and, through the real reconciler with only HTTP faked, `tests/sync/test_apply_unpublish_force.py::test_forced_apply_through_the_real_reconciler_deletes_only_the_managed_orphans`, which asserts the exact DELETE requests a forced run sends.

## 6. Cross-references

- [`apply.md`](apply.md) -- `sigantry sync apply` runbook.
- [`pull.md`](pull.md) -- `sync pull` emits an empty `folders[]` by default; operators add UI-created paths post-pull before committing the manifest.
- [`../../reference/sync-schema.md`](../../reference/sync-schema.md) -- `folders[]` field schema.
- [`../../reference/api-stability.md`](../../reference/api-stability.md) -- the Folders REST endpoints involved are Preview.
- `sigantry_core/workspace/reconciler.py::_normalise_preserve_set` -- the helper that turns operator-supplied paths into the ancestor-inclusive exclusion set.
- `fabric-cicd` source -- the `_unpublish_folders` routine in `fabric_workspace.py` is invoked internally when `sync apply --with-publish` runs `publish_all_items`; that path is **not** filtered by `manifest.folders[]` today.
