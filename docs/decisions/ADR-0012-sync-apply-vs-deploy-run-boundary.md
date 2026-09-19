# ADR-0012 -- `sync apply` vs `deploy run` boundary

- **Status:** Accepted
- **Date:** 2026-05-01
- **Milestone:** v3.0.x (operator-experience hardening; surfaced by 2026-05-01 brownfield live test)
- **Deciders:** platform team
- **Context:** Operators running their first `sigantry sync apply` against a brownfield workspace consistently mis-read the success line as "items deployed". They aren't. The engine packages items into a tempdir staging tree, then calls `reconcile_folders_from_repo` which only does folder topology + existing-item placement. The actual `publish_all_items` step lives in `sigantry deploy run`. This split has been latent since Phase 13 shipped; the 2026-05-01 live test against `COE_F_SBDEVOPS_POC` (workspace `effa6941-...`) made the gap concrete: a fresh project folder was created, the seed notebook was packaged, and zero items appeared in the workspace -- a confused-operator failure mode worth preventing in code.

## Decision

The boundary is **deliberate** and **stays where it is**. `sigantry sync apply` is the workspace-folder-topology + existing-item-placement engine; `sigantry deploy run` is the first-time-publish engine. The two compose by the operator running them in sequence (apply first, then deploy run); they are not merged into one command.

To make the boundary visible at the moment of confusion, three artefacts ship in v3.0.x:

1. A **decision matrix** at the top of `docs/runbooks/sync/apply.md` (§0) listing all five sync verbs (apply / pull / diff / snapshot / deploy run) with a "first-time item creation" column.
2. A **"What `sync apply` does NOT do"** subsection (§1.1) explicitly enumerating the boundaries -- no item creation, no item rename, no orphan deletion-without-flag, no OneLake writes -- with code-level proof (zero `publish_all_items` references in `sigantry_core/sync/apply.py` or `sigantry_core/workspace/reconciler.py`).
3. A **conditional CLI trailer** in the `sync apply` success path that fires when `items_packaged > 0 AND folders_created > 0 AND items_moved == 0` (the signature of "first-time project setup, items staged but not published"). Suppressed on idempotent re-runs (`folders_created == 0`) so it doesn't become noise. Pinned by the three-test contract in `tests/sync/test_cli_apply_boundary_hint.py`.

## Alternatives considered

| Option | Description | Why rejected |
|--------|-------------|--------------|
| **A. Merge `sync apply` and `deploy run`** | Make `sync apply` do everything: folder reconcile + first-time publish. One command, no confusion. | Breaks the **read-only-by-default** invariant (CLAUDE.md). `sync apply` against an unfamiliar workspace would create + publish items in one pass, removing the operator's chance to review the staging tree first. Also conflates the audit story: `DeployRecord` is the single source-of-truth for "what was published"; if `sync apply` wrote DeployRecords, every folder-shuffle would generate a record, polluting the ledger. The two operations have different audit semantics and that's load-bearing for compliance. |
| **B. Make `sync apply` fail on first-time-deploy intent** | If the manifest contains items not in the workspace, `sync apply` raises with "use `deploy run`". | Hostile UX -- the operator's intent is "make my workspace match this manifest", and refusing on a half-match is more frustrating than the current "do as much as I can" semantics. Also breaks the dry-run preview (operators want to see the planned folder topology even when items don't yet exist). |
| **C. Merge via `sync apply --with-publish` opt-in flag** | Keep current behaviour; add a flag that opts into a `publish_all_items` follow-up. | **Implemented in Phase 17 (PUBLISH-01..06) -- see [ADR-0013](ADR-0013-sync-publish-parameters-resolution.md).** The flag opts into a `publish_all_items` follow-up after the folder reconcile, emits ONE combined `DeployRecord` (`release_id` prefix `sync-publish-<TS>`, `provider="sync-engine-publish"` encoded inside `test_evidence`), and hard-fails when `--params parameters.yml` is omitted. Default behaviour without the flag is byte-identical to v3.0 (SemVer-safe). The original deferral rationale (parameters.yml resolution rule needed a phase + ADR) is closed by ADR-0013. |
| **D. Documentation only (no CLI trailer)** | Update apply.md; let operators find it. | Insufficient -- the test that surfaced this issue happened with the runbook open. Operators read CLI output before they read runbooks. The trailer is the one place the message reaches them at the moment of need. |
| **E. Documentation + trailer (chosen)** | The combination above. | Cheapest visible-where-it-matters fix without committing to the v3.x feature work yet. |

## Rationale

1. **Read-only-by-default is non-negotiable.** Sigantry's project contract (CLAUDE.md "Conventions" §read-only-by-default) is that destructive ops require explicit opt-in. First-time-publish is destructive in the sense that it materialises new state in the workspace; folding it silently into `sync apply` violates the contract.
2. **Audit-record cleanliness.** `DeployRecord` is the single ledger entry that traces a workspace mutation back to a work item, a release id, and a test-evidence bundle. Folder reshuffles do not deserve a DeployRecord; they have their own (lighter-weight) `provider="sync-engine"` record. Merging the two would muddy the audit reasoning at exactly the moment compliance teams need it sharp.
3. **Composability is a feature.** Operators value being able to run `sync apply` repeatedly during folder-design iteration without provoking deploys. Once the topology is right, they switch to `deploy run` for the publish. The two-step workflow IS the design; option A would remove that ergonomic.
4. **The trailer fires only when it would help.** It's gated on the exact field signature of the confusing case. On idempotent re-runs, it's suppressed (operators have already seen it). On runs where existing items were moved, it's suppressed (no first-time-deploy concern). This precision keeps it from becoming noise.
5. **Cost is bounded.** This ADR's combined fix (docs + trailer + 3 unit tests + this doc) is ~250 LOC across five files. No new code paths, no breaking changes, no migration burden. Ships in the v3.0.x line without re-opening any phase.

## Consequences

- `docs/runbooks/sync/apply.md` carries §0 (decision matrix), §1.1 (what-it-does-not), and §1.2 (trailer reference). Operators encountering the boundary find it in either the doc or the CLI output, not by reading source.
- The `SyncApplyReport` schema is unchanged (no new fields, no SemVer bump). The trailer is purely a CLI presentation concern.
- `tests/sync/test_cli_apply_boundary_hint.py` pins the trailer's three behavioural cases (fires on first-time setup; suppressed on idempotent re-run; suppressed when existing items were moved). A regression where the trailer either never fires or fires on every run gets caught at CI time.
- The `--with-publish` opt-in (Option C) was IMPLEMENTED in Phase 17 (closed 2026-05-01). It is additive: `sync apply` keeps its current default behaviour; the flag opts into a follow-up `publish_all_items` invocation. The `parameters.yml` resolution rule + `--environment` requirement are codified in [ADR-0013](ADR-0013-sync-publish-parameters-resolution.md). Operator-facing reference: [`docs/runbooks/sync/apply.md`](../runbooks/sync/apply.md) §1.3.
- No impact on `sync pull` / `sync snapshot` / `diff`: those verbs are read-only and the boundary applies only to the write side.
- The boundary trailer at `sigantry_core/sync/cli.py:198-216` (D-26-bis) is now CONDITIONALLY suppressed when `--with-publish` is set (D-17-08). The original three-test trailer contract at `tests/sync/test_cli_apply_boundary_hint.py` is extended with a 4th case `test_trailer_suppressed_when_with_publish_set`, preserving existing semantics for the default path and adding the new suppression invariant.

## Cross-references

- [`docs/runbooks/sync/apply.md`](../runbooks/sync/apply.md) §0, §1.1, §1.2 -- operator-facing surface.
- [`sigantry_core/sync/apply.py`](../../sigantry_core/sync/apply.py) -- `apply_sync` returns a `SyncApplyReport` with `items_packaged` (local stage count) and `items_moved` (existing-item reparenting count); both fields documented in the dataclass docstring.
- [`sigantry_core/sync/cli.py`](../../sigantry_core/sync/cli.py) -- `apply_cmd` carries the conditional trailer (search for the `D-26-bis` comment block).
- [`sigantry_core/workspace/reconciler.py`](../../sigantry_core/workspace/reconciler.py) -- `reconcile_folders_from_repo`: zero references to `publish_all_items` / `FabricWorkspace` / `fabric_cicd`. This is the load-bearing absence that makes the boundary real.
- [`tests/sync/test_cli_apply_boundary_hint.py`](../../tests/sync/test_cli_apply_boundary_hint.py) -- the three-test trailer contract.
