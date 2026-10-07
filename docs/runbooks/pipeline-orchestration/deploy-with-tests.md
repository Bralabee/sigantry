# Sigantry deploy with tests -- pipeline orchestration runbook

Operator-facing reference for the Sigantry
five-stage pipeline templates (ADO + GHA), approval-gate setup, rollback
workflow, and the canonical pitfalls to avoid.

## 0. Decision matrix -- which deploy verb am I looking for?

`sigantry deploy run` is the **publish** verb -- it deploys first-time items and parameterises per environment. It writes no `DeployRecord`; the release is recorded separately by `sigantry release record`. The dual-CI templates this runbook documents wrap that verb in a five-stage `approval -> deploy -> smoke -> integration -> promote` flow with platform-native approval gates, where `promote` runs `sigantry release record`. [ADR-0012](../../decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) formalises the boundary between `deploy run` and the sync verbs (`apply` / `pull` / `diff` / `snapshot`); [ADR-0013](../../decisions/ADR-0013-sync-publish-parameters-resolution.md) covers the `parameters.yml` resolution rule and the `sync apply --with-publish` composite verb.

| You want to ... | Verb | Touches workspace? | First-time item creation? |
|---|---|---|---|
| Deploy first-time items + parameterise per environment (DEV/PREPROD/PROD); writes no `DeployRecord` (this runbook's verb; record the release with `sigantry release record`) | **`sigantry deploy run`** | yes -- runs `fabric-cicd publish_all_items` | **YES** |
| Plan + reconcile folder topology against an existing workspace; move existing items into the manifest's `target_folder` paths | **`sigantry sync apply`** | yes -- creates / moves folders + relocates existing items | **NO** -- new items are staged locally but NOT published; see [`../sync/apply.md` section 1.1](../sync/apply.md#11-what-sync-apply-does-not-do) |
| Mirror an existing workspace into a local IaC tree (`sync.yml` + sources) so future runs are no-op idempotent | **`sigantry sync pull`** | no -- read-only | n/a |
| Compare a manifest against a live workspace and report drift | **`sigantry diff`** | no -- read-only | n/a |
| Capture a workspace's current state for diffing later | **`sigantry sync snapshot`** | no -- read-only | n/a |
| Provision a NEW workspace from scratch (workspace + capacity bind + folders + Git connect + initialize) | **`sigantry workspace bootstrap`** | yes -- creates the workspace and its folder topology | **NO** -- bootstrap creates folders, never items |

If you want folder reconcile + first-time item publish in a single verb, ADR-0013's `sigantry sync apply --with-publish` composes the two cleanly. The five-stage pipeline this runbook documents is the per-environment promote story; `--with-publish` is the operator-side one-shot story.

## 1. Overview

Sigantry ships a pair of pipeline templates that implement
`approval -> deploy -> smoke -> integration -> promote` with platform-native
approval gates. The approval gate runs first, so the environment's
reviewers clear the deploy before anything is published; the smoke and
integration tests then gate the `promote` stage, which records the release
with the approver read from the platform's own approval record (GitHub: the
run's review history; ADO: the pipeline's requester, overridable by the
`approver` parameter):

- ADO: `templates/stages/sigantry-cd.yml` -- a stage-list
  template that consumer pipelines compose via
  `stages: - template: stages/sigantry-cd.yml@<tag>`.
- GHA: `.github/workflows/sigantry-cd.yml` -- a reusable
  workflow that consumer repos call via `workflow_call:` or trigger via
  `workflow_dispatch:`.

Both halves are kept in semantic parity by `scripts/ci/check-dual-ci-parity.py`.
The five named stages, the parameter set, the approval-gate
mechanism, and the `sigantry release record` invocation all match by basename.

The `promote` stage runs `sigantry release record`, which appends a
`DeployRecord` to the configured `--audit-dir` (default `.sigantry/audit/`)
on the runner, verifies the integrity chain with `sigantry release verify`,
uploads the audit ledger artifact, and comments on the linked work items
(the audit jsonl is the deploy ledger).
The templates pass `--fabric-items` (from input or discovered from `--source`),
recording the items deployed so that a rollback can restore them.
`sigantry release list / show / diff` read the ledger;
`sigantry deploy run --rollback --to-release <id>` publishes the items a record
names whose type is in `--item-types`, through `fabric-cicd`'s `items_to_include`
selective publish, with their content read from `--source`.

### 1.1. What `deploy run` (and the dual-CI pipeline templates) does NOT do

This is the boundary the five-stage pipeline enforces -- ADR-0012 formalises the verb landscape; this fence calls out the negative claims operators read from "deploy with tests" most often. `deploy run`'s remit is **publish first-time items + parameterise per environment**; the pipeline's `promote` stage adds the `DeployRecord` through `sigantry release record`. Specifically:

- **It does not handle workspace bootstrap.** `deploy run` assumes the target workspace already exists with the correct capacity binding and folder topology. For greenfield workspace creation, use `sigantry workspace bootstrap` first; only then point the deploy pipeline at the new workspace GUID.
- **It does not handle folder reconcile alone.** If the manifest's folder topology diverges from the workspace, `fabric-cicd publish_all_items` will create new folders implicitly as it publishes items into them, but it will not move existing items between folders or delete orphans. For folder reconcile of an established workspace, use `sigantry sync apply` (folder topology + existing-item placement only, no publish). The `sync apply --with-publish` composite verb (ADR-0013) handles both in one step when first-time setup needs both.
- **It does not roll back across workspaces.** `sigantry deploy run --rollback --to-release <id>` publishes again the recorded items of the types in `--item-types` against the **same** workspace the release was produced from. Cross-workspace rollback (DEV -> PROD or PROD -> DEV) is rejected; `rollback_to_release` asserts `record.workspace == workspace` and raises `ValueError` on mismatch (see section 6 for the parameters.yml interaction that motivates the rejection).
- **It does not handle content drift.** The five-stage pipeline runs on operator triggers (commit / dispatch / promote). It does not detect drift between scheduled runs. For ongoing drift detection, schedule `sigantry diff` via the dual-CI templates documented in [`../drift-detection/scheduled-drift.md`](../drift-detection/scheduled-drift.md).

**For greenfield workspace creation, use `sigantry workspace bootstrap`. For folder topology changes, use `sigantry sync apply`.**

## 2. Operator setup -- ADO side

1. Create an ADO environment with required reviewers attached:
   - Navigate: ADO project -> Project settings -> Environments -> New environment.
   - Name: e.g. `fabric-test` (per-env convention from
     `templates/stages/cd-test.yml`).
   - Add resource: Generic resource is sufficient (no Kubernetes / VM / etc.).
   - Approvals and checks -> Approvals -> Add the named reviewer(s).

2. Enable the **Exclusive Lock** check (it prevents two concurrent
   runs from racing through the approval gate):
   - Approvals and checks -> Add check -> Exclusive lock.
   - This serialises runs targeting the environment so the ledger stays
     in causal order.

3. Confirm the workload-identity-federation service connection is granted
   access to the environment. The deploy, integration and promote stages run
   under it (`AzureCLI@2`); the integration stage also installs `pytest` and
   the consumer repo's `[dev,test]` extras before the `integrationCommand`.

## 3. Operator setup -- GHA side

1. Create a GitHub repo environment with required reviewers:
   - Navigate: Repo -> Settings -> Environments -> New environment.
   - Name: e.g. `production`.
   - Required reviewers -> Add the named reviewer(s).

2. The Sigantry GHA workflow declares the protected environment
   (`ghApprovalEnvironment`) on the **`approval` job only**. This is
   intentional: a single approval prompt covers the workflow. The `deploy`,
   `integration` and `promote` jobs declare `environment: <environment>`
   (the `environment` input, the parameters.yml key) so their OIDC token
   subject is `repo:<org>/<repo>:environment:<environment>`; create that
   GitHub environment too, **without required reviewers**, or your
   reviewers will be prompted three more times. `promote` reads the
   approver from the run's approval record for `ghApprovalEnvironment`;
   a reviewer on the deploy environment would not be the one recorded.

3. The GHA equivalent of the exclusive lock: the workflow already carries
   `concurrency: { group: deploy-${{ inputs.environment }}, cancel-in-progress: false }`
   on the `promote` job. Two near-simultaneous releases queue rather
   than reorder. No additional configuration required.

## 4. Pipeline parameter reference

Both halves accept the same parameter surface, with intentional
per-platform divergences for authentication (the parameters marked ADO or
GHA below). GHA signs in with `azure/login` through OIDC (no secret: a
federated credential on the app registration, subject
`repo:<org>/<repo>:environment:<environment>`, one credential for the three
jobs that reach Fabric);
ADO uses the workload-identity service connection.

| Parameter | Required | Default | Purpose |
|-----------|----------|---------|---------|
| `workspaceId` | yes | -- | Target Fabric workspace GUID |
| `serviceConnection` (ADO only) | yes | -- | WIF service connection name |
| `azureClientId` (GHA only) | yes | -- | App registration (client) id for the OIDC login |
| `azureTenantId` (GHA only) | yes | -- | Entra tenant id for the OIDC login |
| `sigantryVersion` | no | `''` (latest) | Pins `pip install sigantry==<version>` in every stage |
| `environment` | yes | -- | Deploy env (parameters.yml key) |
| `sourceDir` | no | `fabric_items/` | Repo path to .platform items |
| `parametersPath` | no | `parameters.yml` | Override path |
| `itemTypes` | no | `Lakehouse,Environment,Notebook,DataPipeline` | fabric-cicd scope |
| `smokeCommand` | no | `sigantry doctor` | Pluggable hook (section 5) |
| `integrationCommand` | no | `python -m pytest tests/integration/ -m sigantry_pipeline -v` | Pluggable hook (section 5) |
| `adoApprovalEnvironment` (ADO) | yes | -- | ADO env with required reviewers |
| `ghApprovalEnvironment` (GHA) | yes | -- | GitHub env with required_reviewers |
| `releaseId` | no | `$(Build.BuildId)` (ADO) / `${{ github.run_id }}` (GHA) | See section 7 (collision guidance) |
| `workItems` | no | `''` | Comma-separated WI ids |
| `approver` | no | `$(Build.RequestedForEmail)` (ADO) / the run's approval record for `ghApprovalEnvironment` (GHA) | Recorded on the DeployRecord. GHA: the record wins; this input is the fallback when no record is found, then the run actor, each with a warning |

Override per-env in `parameters.yml` (consumer-repo-owned) or per-run
via the platform's pipeline/workflow inputs.

## 5. Smoke + integration command override recipe

The `smokeCommand` and `integrationCommand` parameters
are pluggable hooks with sensible defaults. Override via `parameters.yml`:

```yaml
# parameters.yml
smoke_command: 'sigantry doctor && sigantry workspace list --workspace-id $WS_ID'
integration_command: 'pytest tests/contract/ -m post_deploy -v'
```

Or via pipeline inputs (ADO):

```yaml
# consumer pipeline
stages:
  - template: stages/sigantry-cd.yml@sigantry-templates
    parameters:
      smokeCommand: 'sigantry doctor && curl -s https://my-app/health'
      integrationCommand: 'python -m pytest tests/post-deploy/ -v'
```

Or via workflow inputs (GHA `workflow_dispatch:` or `workflow_call:`):

```yaml
# caller workflow
permissions:
  contents: read
  id-token: write   # the reusable workflow's azure/login needs it from the caller too
  actions: read     # and the approval-record lookup
jobs:
  deploy:
    uses: org/sigantry-templates/.github/workflows/sigantry-cd.yml@<tag>
    with:
      workspaceId: '<workspace-guid>'
      environment: 'prod'
      ghApprovalEnvironment: 'prod-approval'
      azureClientId: '<app-registration-client-id>'
      azureTenantId: '<tenant-id>'
      sigantryVersion: '1.0.1'
      smokeCommand: 'sigantry doctor && curl -s https://my-app/health'
```

## 6. Rollback workflow

Rollback publishes again, through fabric-cicd, the item names a recorded
release lists whose type is in `--item-types`. The record holds no item
content and no commit:
the content comes from `--source`, so check out the source of the release
you are restoring before step 3. A record that names no items makes
the rollback refuse to proceed (exiting 1). The pipeline templates
pass `--fabric-items`, recording the items deployed.

```bash
# 1. Inspect what's in the ledger.
sigantry release list --limit 10
sigantry release show R-prod-2026-04-26-1 --json | jq

# 2. Diff two releases to confirm the rollback target.
sigantry release diff R-prod-2026-04-26-1 R-prod-2026-04-27-1 --json | jq

# 3. Roll back. With ./fabric_items checked out at the source of
#    R-prod-2026-04-26-1, the CLI publishes again that release's recorded
#    items of the types in --item-types against the same workspace
#    (cross-workspace rollback is rejected).
sigantry deploy run --rollback --to-release R-prod-2026-04-26-1 \
  --rollback-force \
  --workspace-id <ws-guid> \
  --source ./fabric_items \
  --environment prod \
  --item-types Notebook,Lakehouse,Environment,DataPipeline

# 4. Verify a NEW DeployRecord landed for the rollback action (the CLI
#    writes it, not the rollback function).
sigantry release list --limit 5
# The topmost record carries release_id "rollback-of-R-prod-2026-04-26-1-<ISO_TS>".
```

`parameters.yml` substitution interaction: `items_to_include`
matches against the SOURCE-TREE-AS-AT-DEPLOY-TIME logical names, but
`parameters.yml` may rewrite them per environment. Cross-environment
rollback (DEV -> PROD) is NOT supported. The `rollback_to_release`
function asserts `record.workspace == workspace` and raises ValueError
on mismatch.

## 7. release_id collision guidance

`$(Build.BuildId)` (ADO) and `${{ github.run_id }}` (GHA)
are unique within their own scope but NOT globally unique across multiple
consumers writing to the same `~/.sigantry/audit/deploys.jsonl` (e.g. an
ops engineer running `sigantry release record` locally alongside CI runs).

Two mitigations:

1. **Composite default.** Override the template default in your consumer:

   ```yaml
   # ADO consumer
   releaseId: '${{ parameters.environment }}-$(Build.BuildId)'

   # GHA consumer
   releaseId: '${{ inputs.environment }}-${{ github.run_id }}-${{ github.run_attempt }}'
   ```

2. **Local runs.** Always pass `--release-id <unique>` explicitly to
   `sigantry release record` for laptop runs. Never rely on the default.

Defect signal: `sigantry release show <id>` raises
`ValueError: Ledger has 2 records for release_id 'XYZ'`.

## 8. Verifying a run

After a successful promotion (or rollback), inspect the audit jsonl:

```bash
# Most-recent first.
sigantry release list --limit 5

# Show one record fully.
sigantry release show R-prod-2026-04-27-1 --json | jq

# Diff two recent releases.
sigantry release diff R-prod-2026-04-26-1 R-prod-2026-04-27-1 --json \
  | jq '.added[].fabric_item_id'

# SemVer-committed JSON schema:
#   {release_a, release_b, added, removed, unchanged}
# Each entry: {logical_name, item_type, fabric_item_id}
# Adding a new top-level or per-entry key requires a SemVer-minor bump.
```

The audit jsonl lives at `~/.sigantry/audit/deploys.jsonl` (mode 0o600,
fsync'd, dir 0o700). Override via `--audit-dir
<path>` for hermetic CI runners (e.g. mounting Azure Files).

---

Cross-references:
- `templates/stages/sigantry-cd.yml` and `.github/workflows/sigantry-cd.yml` (the YAML pair).
- `sigantry_core/release/cli.py` (`sigantry release list / show / diff`).
- `sigantry_core/deploy/rollback.py` and `tests/deploy/test_rollback.py` (`sigantry deploy run --rollback`).
