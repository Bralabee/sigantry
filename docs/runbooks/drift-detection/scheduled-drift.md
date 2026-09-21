# Scheduled drift-check -- ADO + GHA templates

**Phase 13 (DRIFT-03 / D-27..30).** Operator-facing reference for the scheduled drift-detection templates that ship under the dual-CI parity lint.

## 0. Decision matrix -- which drift verb am I looking for?

The drift-check templates wrap `sigantry diff`, which is one verb in a five-verb sync surface. [ADR-0012](../../decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) formalises the boundary; this table is the operator's quick reference. Pay particular attention to the **first-time item creation** column -- that's the most common misread, and the reason a "drift detected" alert often resolves to a missing `deploy run`, not a `sync apply`.

| You want to ... | Verb | Touches workspace? | First-time item creation? |
|---|---|---|---|
| Compare a manifest against a live workspace and report drift (this runbook's verb) | **`sigantry diff`** | no -- read-only | n/a -- diff never publishes |
| Plan + reconcile folder topology against an existing workspace; move existing items into the manifest's `target_folder` paths | **`sigantry sync apply`** | yes -- creates / moves folders + relocates existing items | **NO** -- new items are staged locally but NOT published; see [`../sync/apply.md` section 1.1](../sync/apply.md#11-what-sync-apply-does-not-do) |
| Mirror an existing workspace into a local IaC tree (`sync.yml` + sources) so future runs are no-op idempotent | **`sigantry sync pull`** | no -- read-only | n/a |
| Deploy first-time items + parameterise per environment (DEV/PREPROD/PROD) + write a `DeployRecord` for audit | **`sigantry deploy run`** | yes -- runs `fabric-cicd publish_all_items` | **YES** |
| Capture a workspace's current state for diffing later | **`sigantry sync snapshot`** | no -- read-only | n/a |

`sigantry diff` is the **detection** verb. The scheduled-drift templates run it on a cron and pipe the report to a notification sink, but they never auto-remediate -- remediation is an operator decision (see section 1.1).

## 1. Overview

Sigantry ships a dual-CI pair of cron-driven templates that invoke `sigantry diff` on a schedule and post the result to a notification sink:

- ADO: `templates/schedules/drift-check.yml` -- a stage-list template that consumer pipelines compose via `template:` reference.
- GHA: `.github/workflows/drift-check.yml` -- a reusable workflow consumed via `workflow_dispatch:` or `workflow_call:`.

Both halves are kept in semantic parity by `scripts/ci/check-dual-ci-parity.py` (Plan 10-06). The DRIFT-03 pair is the second non-exception entry under the parity lint after Phase 12's `sigantry-cd.yml`.

The flow on each scheduled tick:

1. Authenticate via `DefaultAzureCredential` (ADO: WIF service connection; GHA: OIDC).
2. Run `sigantry diff -e <env> --workspace-id <id> --manifest <path> --output json --fail-on-drift > drift.json`.
3. If exit code != 0, run the notification step (`condition: failed()` ADO / `if: failure()` GHA).
4. Upload `drift.json` as a build artefact for post-mortem.

### 1.1. What `sigantry diff` (and the scheduled drift-check templates) does NOT do

This is the load-bearing boundary the scheduled drift-check templates enforce -- ADR-0012 formalises the verb landscape; this fence calls out the negative claims operators read from "drift detection" most often. `sigantry diff`'s remit is **detection only**, never remediation. Specifically:

- **It does not auto-remediate drift.** When the diff reports added / removed / modified entries, the templates emit a notification and exit non-zero. No POST / PATCH / DELETE is issued. Remediation requires an explicit operator follow-up via `sync apply` (folder + existing-item placement) or `deploy run` (first-time publish). The CLI surfaces this as a one-line trailer when drift is detected and human output is selected (D-19-05).
- **It does not write to the workspace.** Diff snapshots the workspace via two paginated REST reads and compares against a local manifest. There is no write-side surface; the only network failures are read-quota throttles.
- **It does not detect content-level drift.** Diff keys on `(logical_id, display_name, item_type, target_folder)` per the snapshot schema. Two notebooks with identical metadata but different cell content register as `unchanged`. Use the per-type packagers (Notebook / DataPipeline / etc.) and a separate content-comparison tool if cell-level drift is required.
- **It does not handle Native-Git-Sync race conditions.** If the workspace's `gitConnection.sync_state` is anything other than `Synced` at run time, the underlying snapshot read may return inconsistent state. Native Git Sync rewrites folder GUIDs (D-10) without rewriting `logical_id`, so the diff key choice is robust to that case -- but interleaved REST writes from a concurrent `sync apply` run can still produce false-positive drift entries. Serialise drift-check vs. apply runs (the dual-CI templates do not enforce this; the operator cron schedule does).

**For drift remediation, run `sigantry sync apply` (folder reconcile only) or `sigantry deploy run` (first-time publish). The drift-check pipeline never auto-remediates.**

## 2. Setup

### ADO

1. Add the schedule template to your consumer pipeline:

   ```yaml
   # azure-pipelines-drift.yml (consumer)
   schedules:
     - cron: '0 6 * * *'   # daily 06:00 UTC
       displayName: Daily drift check
       branches:
         include:
           - master
       always: true        # run even if no commits

   stages:
     - template: schedules/drift-check.yml@sigantry-templates
       parameters:
         workspaceId: '<coe-guid>'
         manifestPath: 'fabric-iac/sync.yml'
         serviceConnection: 'sigantry-wif-prod'
         environment: 'prod'
         notificationSink: 'teams'
   ```

2. Add the WIF service connection to the ADO project (Phase 5 Plan 05-01 covers setup).

3. Configure Teams / Slack / SMTP secrets as ADO library variables (see section 4).

### GHA

1. Add the workflow to your consumer repo:

   ```yaml
   # .github/workflows/drift-check.yml (consumer)
   name: Drift check
   on:
     schedule:
       - cron: '0 6 * * *'   # daily 06:00 UTC
     workflow_dispatch:        # operator-triggered for testing

   jobs:
     drift:
       uses: org/sigantry-templates/.github/workflows/drift-check.yml@<tag>
       with:
         workspaceId: '<coe-guid>'
         manifestPath: 'fabric-iac/sync.yml'
         environment: 'prod'
       secrets: inherit
   ```

2. Configure `azure/login@v2` OIDC at the federated-credential level (subject = `repo:org/repo:environment:prod`).

3. Configure Teams / Slack / SMTP secrets as repo secrets (see section 4).

## 3. Configuration -- parameter reference

Both halves accept the same input set (with intentional per-platform divergences for `serviceConnection` ADO-only):

| Parameter | Required | Default | Purpose |
|-----------|----------|---------|---------|
| `workspaceId` | yes | -- | Target Fabric workspace GUID |
| `manifestPath` | yes | -- | Path to `sync.yml` (relative to repo root) |
| `serviceConnection` (ADO only) | yes | -- | WIF service connection name |
| `environment` | yes | -- | Environment label (parameters.yml key) |
| `notificationSink` | no | `teams` | One of `teams`, `slack`, `email` |
| `cron` | no (consumer-side) | `0 6 * * *` | Cron expression -- daily at 06:00 UTC |

Override per-environment in your consumer's parameters or per-run via the platform's pipeline / workflow inputs.

## 4. Notification sink configuration -- env vars (D-30)

The notification sink is selected by `SIGANTRY_NOTIFICATION_SINK` (env var read by the scheduled CI step). Each sink reads its own per-impl secret. Configure these as ADO library variables (mark them secret) or GHA repo / org secrets.

| Sink | `SIGANTRY_NOTIFICATION_SINK` | Required env vars |
|------|------------------------------|-------------------|
| Teams | `teams` | `SIGANTRY_TEAMS_WEBHOOK` -- the incoming-webhook URL from your Teams channel connector. |
| Slack | `slack` | `SIGANTRY_SLACK_WEBHOOK` -- the incoming-webhook URL from your Slack app. |
| Email | `email` | `SIGANTRY_SMTP_HOST`, `SIGANTRY_SMTP_PORT`, `SIGANTRY_SMTP_USER`, `SIGANTRY_SMTP_PASSWORD`, `SIGANTRY_SMTP_FROM`, `SIGANTRY_SMTP_TO` |

The webhook URLs encode the secret in the URL itself (no separate `Authorization` header), which is why notification sinks carry a documented carve-out from the project's "one HTTP client" rule -- see [`../../reference/api-stability.md`](../../reference/api-stability.md) section 4.

The sink stand-ins live in `sigantry_core/sync/notifications.py` and are deliberately thin -- a Phase 16 SEAM expansion replaces them with a pluggable `NotificationSink` Protocol seam shipping operator-supplied implementations via entry points.

## 5. Cron tuning + rate-limit guidance

The recommended default is **daily at 06:00 UTC** (`0 6 * * *`). Reasons:

- Drift detection is a low-urgency signal -- a workspace that drifted at 03:00 doesn't need to be detected before the operator's first coffee.
- Each run snapshots the workspace via two paginated REST calls. Sub-hourly is throttle-risky on busy workspaces.
- The notification surface is synchronous -- a 5-minute cron would stack notifications faster than operators can triage.

Sub-hourly cron is **discouraged**. If you need rapid drift detection, prefer event-driven approaches (e.g. Native Git Sync webhook -> on-push drift check) over cron.

Microsoft Fabric's REST throttling surface is documented in [Fabric throttling docs](https://learn.microsoft.com/en-us/fabric/admin/admin-overview). The list-folders + list-items pair is read-only and falls under the standard read-quota; no special accommodation needed for daily cron.

## 6. Verification

### Manual trigger (GHA)

```bash
gh workflow run drift-check.yml \
  --repo org/repo \
  -f workspaceId=<coe-guid> \
  -f manifestPath=fabric-iac/sync.yml \
  -f environment=prod
```

Then `gh run list --workflow=drift-check.yml --limit 1` to monitor; download the `drift-json` artefact to inspect.

### Manual trigger (ADO)

```bash
az pipelines run \
  --name drift-check \
  --branch master \
  --variables workspaceId=<coe-guid> manifestPath=fabric-iac/sync.yml environment=prod
```

Then inspect the run via `az pipelines runs show --id <run-id>`; download the `drift-json` artefact from the build details page.

### Expected output

- Clean state: exit code `0`. The notification step is skipped (it gates on `failure()`). The `drift.json` artefact carries `{schema_version: "1.0.0", added: [], removed: [], modified: [], unchanged: [...]}`.
- Drift detected: exit code `1`. The notification step runs and posts to the configured sink. The `drift.json` artefact carries non-empty `added` / `removed` / `modified` arrays.
- Operational error (auth failure, workspace not found): exit code `2`. The notification step runs (it's gated on `failure()` which covers both `1` and `2`), but the message body explains the operational nature; operator should investigate the underlying error before treating it as drift.

## 7. Troubleshooting

| Symptom | Likely cause | Remediation |
|---------|--------------|-------------|
| Exit `2` on every run -- "auth failure" | WIF service connection (ADO) or OIDC federated credential (GHA) misconfigured. | Re-run `sigantry doctor` against the runner's auth chain. The ADO service connection's federated credential subject must match the schedule pipeline's run identity. |
| Exit `2` on every run -- "workspace not found" | The workspace GUID changed (e.g. workspace recreated) or the runner identity lacks workspace-level access. | Validate via `sigantry workspace get <id>` from the runner; grant `Member` or `Contributor` to the runner identity. |
| Notifications never arrive -- sink configured | Webhook URL revoked, SMTP creds expired, or the notify step skipped because the diff was clean. | Inspect the run logs for the notify-step skip condition. Verify the sink's webhook responds to a manual POST. |
| Drift fires daily on a "stable" workspace | Native Git Sync is rewriting folder GUIDs (D-10). Folder GUIDs differ between snapshots even though paths are stable. | The diff JSON should NOT key on folder GUIDs -- it keys on `logical_id`. If you see folder-GUID-driven drift, file a regression. |
| Exit `1` on every run with the same drift entries | Manifest is genuinely out of sync with the workspace. | Run `sigantry sync pull --into /tmp/snapshot`; diff against the manifest; either reconcile via `sync apply` or update the manifest. |

## 8. Cross-references

- [`../sync/apply.md`](../sync/apply.md) -- `sigantry sync apply` runbook (drift remediation typically ends in an apply).
- [`../sync/pull.md`](../sync/pull.md) -- `sync pull` for re-IaC-fying drifted state.
- [`../sync/snapshot-freshness.md`](../sync/snapshot-freshness.md) -- TTL'd cache discipline (snapshots are per-invocation).
- [`../../reference/sync-schema.md`](../../reference/sync-schema.md) -- the manifest contract.
- [`../../reference/api-stability.md`](../../reference/api-stability.md) -- Fabric REST stability matrix + the notification webhook carve-out.
- [`../../reference/dual-ci-strategy.md`](../../reference/dual-ci-strategy.md) -- the parity rules the drift-check pair satisfies.
- Phase 12 [`../pipeline-orchestration/deploy-with-tests.md`](../pipeline-orchestration/deploy-with-tests.md) -- the dual-CI pattern that the drift template mirrors.
