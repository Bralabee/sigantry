# Workspace Bootstrap — Operator Runbook (Phase 13.5 / BOOTSTRAP-XX)

**Audience:** Platform operators standing up a fresh Fabric workspace from
a YAML manifest.

**Status:** Phase 13.5 — landed v3.0.x post-merge. Closes the
config-driven workspace materialisation gap that was deferred from the
v3.0 milestone per Council E's idempotency-gap finding.

**Cross-references:**

- Pattern source: [`docs/RELATED-WORK.md`](../RELATED-WORK.md) §6
  recommendation 5 — modelled on `usf_fabric_cli_cicd`'s
  `scaffold --brownfield --templatise --as-stage` semantics, validated
  against live customer workspaces.
- Numbered medallion folder convention: §4 item 2 of the same doc.
- Stage-marker regex: §4 item 5.
- jsonschema validation: §4 item 8.

---

## 0. Decision matrix -- which workspace verb am I looking for?

`sigantry workspace bootstrap` is the **greenfield** verb -- it provisions a NEW workspace from scratch. Once the workspace exists, the brownfield sync verbs (`apply` / `pull` / `diff` / `snapshot`) and the publish verb (`deploy run`) take over. [ADR-0012](../decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) formalises that boundary; the table below is the operator's quick reference. Pay particular attention to the **first-time item creation** column -- that's the most common misread, and it's why bootstrap alone never makes a notebook appear in the workspace.

| You want to ... | Verb | Touches workspace? | First-time item creation? |
|---|---|---|---|
| Provision a NEW workspace from scratch (workspace + capacity bind + folders + Git connect + initialize) | **`sigantry workspace bootstrap`** | yes -- creates the workspace and its folder topology | **NO** -- bootstrap creates folders, never items; see section 1.1 |
| Plan + reconcile folder topology against an existing workspace; move existing items into the manifest's `target_folder` paths | **`sigantry sync apply`** | yes -- creates / moves folders + relocates existing items | **NO** -- new items are staged locally but NOT published; see [`runbooks/sync/apply.md` section 1.1](sync/apply.md#11-what-sync-apply-does-not-do) |
| Mirror an existing workspace into a local IaC tree (`sync.yml` + sources) so future runs are no-op idempotent | **`sigantry sync pull`** | no -- read-only | n/a |
| Deploy first-time items + parameterise per environment (DEV/PREPROD/PROD) + write a `DeployRecord` for audit | **`sigantry deploy run`** | yes -- runs `fabric-cicd publish_all_items` | **YES** |
| Compare a manifest against a live workspace and report drift | **`sigantry diff`** | no -- read-only | n/a |
| Capture a workspace's current state for diffing later | **`sigantry sync snapshot`** | no -- read-only | n/a |

A typical greenfield onboarding runs `bootstrap` once (create the workspace), then `deploy run` to publish first-time items, then schedules `diff` on a cron for ongoing drift detection. `sync apply` enters the picture later when the manifest's folder topology evolves.

---

## 1. What it does

A single command:

```bash
sigantry workspace bootstrap workspace.yml
```

runs the **5-call REST sequence** against your Fabric tenant:

1. **Workspace** — probe by name, create if missing.
2. **Capacity** — verify binding matches manifest, rebind if mismatched.
3. **Folders** — verify every desired folder exists, create missing ones.
4. **Git** — bind the workspace to a Git repo (idempotent
   disconnect-before-reconnect on target mismatch).
5. **Initialize** — initialise the Git connection so the next deploy can
   commit.

Every step is **probe-before-act**: the toolkit inspects current state,
no-ops when already converged, and only POSTs when state diverges from
the manifest. Re-running on a partly-bootstrapped workspace MUST converge
— this is the load-bearing property and is verified by the integration
tests + the live UAT (see §6 below).

A `BootstrapRecord` lands in `~/.sigantry/audit/bootstraps.jsonl` after
every successful run. Records carry an `audit_hash` (SHA-256 over
canonical JSON) so a verifier can prove tamper-evidence later via
`record.verify_hash()`.

### 1.1. What `workspace bootstrap` does NOT do

This is the boundary `workspace bootstrap` enforces -- ADR-0012
formalises the verb landscape; this fence consolidates the negative
claims partially captured in section 7 (`Out of scope`). `bootstrap`'s
remit is **provision the empty workspace shell + folder topology + Git
wiring**, no further. Specifically:

- **It does not deploy items.** No notebook, pipeline, semantic model,
  or report leaves your local filesystem during bootstrap. The 5-call
  sequence covers workspace + capacity + folders + Git connect +
  initialize; first-time item creation is a separate verb. Use
  `sigantry deploy run` (or `sigantry sync apply --with-publish` per
  [ADR-0013](../decisions/ADR-0013-sync-publish-parameters-resolution.md))
  after bootstrap completes.
- **It does not run multi-stage Dev+Test+Prod orchestration.** Bootstrap
  creates a single workspace per invocation. Stage-marked workspaces
  (`stage: DEV` / `TEST` / `PROD`) are independent runs against the same
  manifest with the marker varied. The "onboard" verb that wraps the
  full Dev+Test+Prod+Pipeline lifecycle is deferred (section 7).
- **It does not handle branch-isolated feature-workspace lifecycle.**
  `stage: FEATURE` creates a feature-named workspace, but bootstrap will
  not auto-create one on push or auto-destroy one on PR merge. Feature
  workspace lifecycle automation is out of scope (section 7); the
  Council E recommendation is to wire it as a CI workflow that calls
  `bootstrap` and `workspace delete` directly.
- **It does not register pipeline users (Power BI API).** Pipeline user
  management uses the Power BI API surface (gotcha #7) which bootstrap
  intentionally does not touch. Use the Power BI portal or the
  `pbi-tools` CLI for pipeline user wiring after bootstrap completes.

**For first-time item creation in the new workspace, use `sigantry deploy run` after bootstrap completes.**

---

## 2. Authoring `workspace.yml`

Minimum viable manifest (fresh workspace, no Git):

```yaml
schema_version: "1.0"
workspace:
  name: my-workspace
  capacity_id: "0749b635-c51b-46c6-948a-02f05d7fe177"
folders:
  blueprint: minimal_starter
git:
  enabled: false
```

Full surface (every key documented):

```yaml
schema_version: "1.0"      # locked at "1.0" for the v3.0.x line

workspace:
  name: my-workspace                            # display name; required
  description: "Phase 13.5 demo workspace"      # optional, <=1000 chars
  capacity_id: "<fabric-capacity-guid>"         # required GUID

  # Stage marker -- one of DEV / TEST / PREPROD / PROD / FEATURE / NONE.
  # When stage_marker_in_name=true the name is prefixed with [DEV] / [TEST]
  # / etc. FEATURE additionally requires `feature_branch` and produces
  # "[F] <branch> <name>" per the convention validated in
  # docs/RELATED-WORK.md §4 item 5.
  stage: DEV
  stage_marker_in_name: false
  feature_branch: my-feature-branch     # required only when stage=FEATURE
  domain_id: null                       # optional Fabric domain

folders:
  # Choose ONE of:
  #   blueprint: <named layout from sigantry_core.workspace.blueprints>
  #   list: ["folder one", "folder two", ...]
  blueprint: minimal_starter
  # OR:
  # list:
  #   - "000 Orchestrate"
  #   - "100 Ingest"

git:
  enabled: false                         # required key; false = no Git wiring
  # When enabled=true ALL of the following are required:
  # provider: ado
  # organization_name: myorg
  # project_name: myproj
  # repository_name: myrepo
  # branch_name: main
  # directory_name: fabric_items
  # git_connection_id: <connection guid>
  # force_reconnect: false               # opt-in destructive disconnect
                                          # when bound to a different target
```

### Available blueprints

The `folders.blueprint` key references the catalog in
`sigantry_core/workspace/blueprints.py`:

| Name | Folders |
|---|---|
| `minimal_starter` | `000 Orchestrate / 100 Ingest / 200 Store / 300 Prepare / 400 Model / 500 Visualize / 999 Libraries / Archive` |
| `medallion` | (alias for `minimal_starter`) |

Numbered prefixes guarantee Fabric UI top-to-bottom ordering matches
pipeline-flow order.

---

## 3. Running it

### 3.1 Authenticate

The toolkit uses `DefaultAzureCredential`. Any of these is fine:

```bash
# Service principal env vars (CI default).
export AZURE_CLIENT_ID=<sp-app-id>
export AZURE_CLIENT_SECRET=<sp-secret>
export AZURE_TENANT_ID=<tenant-guid>

# OR az login on a workstation:
az login
```

### 3.2 Dry-run first (always)

```bash
sigantry workspace bootstrap workspace.yml \
  --tenant-id <tenant-guid> \
  --dry-run \
  --operator alice@example.invalid
```

Reads current tenant state, reports which steps WOULD fire, does NOT
POST anything, does NOT append to `bootstraps.jsonl`. Output is JSON
on stdout with `step_outcomes` showing per-step decisions.

### 3.3 Real run

```bash
sigantry workspace bootstrap workspace.yml \
  --tenant-id <tenant-guid> \
  --operator alice@example.invalid
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Bootstrap succeeded (or every step was already-converged) |
| 1 | Runtime error from underlying primitive (network / auth / Fabric API) |
| 2 | `workspace.yml` failed JSONSchema validation OR cross-field constraint |

### 3.4 Idempotent re-run

Re-running the same manifest against the same tenant is the load-bearing
property: every step must report `already-converged`. Verified live for
all 5 steps; see §6.

---

## 4. Reading the audit log

```bash
tail -1 ~/.sigantry/audit/bootstraps.jsonl | python -m json.tool
```

The last line carries the JSON shape of `BootstrapRecord`:

```json
{
  "workspace_id": "<fabric-workspace-guid>",
  "workspace_name": "my-workspace",
  "stage": "DEV",
  "capacity_id": "<fabric-capacity-guid>",
  "blueprint": "minimal_starter",
  "folders_created": ["000 Orchestrate", "100 Ingest", ...],
  "folders_present": ["000 Orchestrate", ..., "Archive"],
  "git_target": null,
  "step_outcomes": {
    "workspace": "created",
    "capacity": "already-converged",
    "folders": "created",
    "git": "skipped",
    "initialize": "skipped"
  },
  "operator": "alice@example.invalid",
  "audit_hash": "<sha256-hex>",
  "created_at": "2026-04-30T10:30:00.000Z"
}
```

To verify the hash:

```bash
python -c "
import json
from pathlib import Path
from sigantry_core.workspace.records import BootstrapRecord
last = Path.home().joinpath('.sigantry/audit/bootstraps.jsonl').read_text().splitlines()[-1]
r = BootstrapRecord(**json.loads(last))
print('audit_hash matches:', r.verify_hash())
"
```

`True` is the cryptographic guarantee the line has not been tampered
with after-the-fact.

---

## 5. Tear-down

```bash
sigantry workspace delete <workspace-guid> --force --runbook-id <ticket>
```

Use `--force` per the destructive-op gate. The toolkit's
`delete_workspace` function accepts `pbi_fallback=True` (gotcha #6 fix —
Fabric `DELETE /v1/workspaces/{id}` intermittently returns
`UnknownError`; the PBI fallback at
`https://api.powerbi.com/v1.0/myorg/groups/{id}` is more reliable).

---

## 6. Live verification (Phase 13.5 closure)

Verified live 2026-04-30 against the JToye Trial capacity:

- Workspace `sigantry-jtoye-bootstrap-uat` (id `ab12a30c-1248-4658-a690-9708472421a2`)
  on capacity `0749b635-c51b-46c6-948a-02f05d7fe177` (UK South).
- 8-folder `minimal_starter` blueprint materialised in pipeline-flow
  order; `folders_present` matches the catalog exactly.
- Idempotent re-run reported `workspace=already-converged`,
  `capacity=already-converged`, `folders=already-converged`.
- Both audit-log lines (first run + re-run) verify under
  `record.verify_hash() == True`.
- Tear-down via `delete_workspace(force=True, pbi_fallback=True)`:
  succeeded on first attempt — no `UnknownError` retry needed for this
  run, but the fallback path is wired and tested.

---

## 7. Out of scope (deferred to later v3.x phases)

- Multi-stage Dev+Test+Prod+Pipeline orchestration ("onboard" verb).
- Branch-isolated `feature-workspace` lifecycle (auto-create on push,
  auto-destroy on PR merge).
- Pipeline user management (Power BI API surface — gotcha #7).
- Native deployment-pipeline integration (gotchas #4, #5).
- Larger blueprint catalog (`medallion` is currently an alias; the full
  set from `usf_fabric_cli_cicd` — `data_mesh_domain` / `data_science` /
  `compliance_regulated` / etc. — lands as v3.x.y follow-ups when an
  operator asks for one).

---

*Runbook authored: 2026-04-30 — Phase 13.5 BOOTSTRAP-XX closure.*
