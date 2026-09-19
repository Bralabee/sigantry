# Runbooks (template)

The base `sigantry-core` package ships no concrete runbooks.
Plugin packages register a `RunbookRegistry` implementation under the
`sigantry.runbook_registries` entry-point group and
expose URL mappings via `.fabric-dataops.toml`:

```toml
[runbooks]
registry = "your_registry"

[runbooks.your_registry]
runbook_map = { pipeline_run_failed = "https://your-runbook-url" }
```

See the consumer plugin package shipped alongside this base for a
production example with an alert catalogue pre-mapped.

## How to write a runbook

Runbooks follow this structure:

1. **Trigger** - which alert / symptom kicks this off.
2. **Triage** - the first five minutes of detection steps.
3. **Mitigation** - bring the system to a stable state.
4. **Resolution** - permanent fix.
5. **Follow-up** - post-incident tasks.

Keep runbooks short (2-3 pages). Link to dashboards, runbooks
referenced by chained alerts, and any automation that partially
resolves the incident.

## Shipping your own runbooks

1. Author `.md` runbook files under `<your-plugin>/docs/runbooks/`.
2. Implement a `RunbookRegistry` subclass mapping alert name -> URL.
3. Register the class under
   `sigantry.runbook_registries` in your plugin's
   `pyproject.toml`.
4. Wire via `.fabric-dataops.toml` (see above).

## Built-in runbooks

The base package ships a small set of operator-facing runbooks for the
seams it owns directly (no plugin required):

- [Work-Item Traceability -- Comment Rendering](work-item-traceability/comment-rendering.md) -- Phase 11 (TRACE-06): how the structured release-record comment renders on ADO + GitHub, and how to verify byte-equality across providers.
- [Pipeline Orchestration -- Sigantry deploy with tests](pipeline-orchestration/deploy-with-tests.md) -- Phase 12 (PIPELINE-01..05): operator-facing reference for the ADO + GHA five-stage pipeline templates (deploy / smoke / integration / approval / promote), approval-gate setup, rollback workflow, and the canonical Pitfalls 5 / 8 / 9.

## Sync (Phase 13)

Manifest-driven local <-> Fabric sync engine. Each runbook covers one
operator-facing surface:

- [Sync -- `sigantry sync apply`](sync/apply.md) -- Phase 13 (SYNC-04 / SYNC-06 / INTROSPECT-01): operator setup, command reference, configuration, the canonical 9-notebook AIMS use case, troubleshooting, known limitations.
- [Sync -- `sigantry sync pull`](sync/pull.md) -- Phase 13 (SYNC-05): IaC-fy an existing workspace, round-trip preservation invariant (D-22), `--force` semantics.
- [Sync -- Folder preservation (`folders[]` and `_unpublish_folders`)](sync/folder-preservation.md) -- Phase 13 (Council D #5): how the manifest's `folders[]` set protects operator-created paths from `fabric-cicd`'s auto-cleanup.
- [Sync -- Snapshot freshness (TTL'd cache discipline)](sync/snapshot-freshness.md) -- Phase 13 (INTROSPECT-03 / D-10): "Native Git Sync + REST writes can change folder GUIDs between snapshots; trust the live API, never a previous snapshot file."

## Drift detection (Phase 13)

Scheduled CI templates that compare a manifest against a live workspace
and post the diff to a notification sink:

- [Drift detection -- Scheduled drift-check](drift-detection/scheduled-drift.md) -- Phase 13 (DRIFT-03 / D-27..30): operator setup of the ADO + GHA cron templates, parameter reference, `SIGANTRY_NOTIFICATION_SINK` configuration, cron tuning, troubleshooting.
