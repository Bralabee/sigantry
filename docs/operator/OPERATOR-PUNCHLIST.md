# OPERATOR PUNCHLIST — close v3.0

> Maintainer-side closure landed in `v3.0.0-alpha.4`. This doc enumerates every action that must be taken by an operator with credentials / org-admin / tenant-admin access I do not have. Each gate has the **exact command**, the **prerequisite credential**, and a **verification step** so success is unambiguous.

> **Freshness review (2026-05-07, audit-2026-05-07 Wave 1):** Reviewed against PRs #67-#82 landed on master since this doc was last touched (2026-05-01). The kept-gate list (A2, A3, A4, C1-C4, D, F1-F4, G1) is **credential / live-tenant bound** and is unaffected by post-2026-05-01 maintainer-side PRs. PR #70 (Phase 17 SYNC-PUBLISH) and PR #73 (Phase 19 OPERATOR-DOC-HARMONISATION) are v3.1-track work and ride post-v3.0.0 ship per V3.X-ROADMAP.md (which now marks both phases SHIPPED). PR #72 (rbac-audit 401 handling), #76 (sdist allow-list), #77 (audit-record `moved_items`), #78 (ruff-format), #81 (User Guide PDF) are housekeeping and do not affect critical-path gates. Conclusion: the punchlist below is **still accurate** as of 2026-05-07; no gate has changed status.

> **2026-06-11 operator rescope (v3.0.0 ship decision):** the operator waived
> A2/A3/A4 and C1-C4 as v3.0.0 ship blockers ("the A and C issues can be
> addressed later"). v3.0.0 final ships via GitHub Release wheel assets;
> PyPI / PowerShell Gallery publish (F1-F3) remains gated on the A-gates and
> rides post-ship. The A/C gate content below is unchanged and stays tracked
> as post-ship work -- only the blocking status moved. The naming decision is
> resolved the same day: the project keeps "Sigantry".

## Critical path for v3.0.0 ship (HS2-only — 2026-05-01 rescope)

**Operating principle:** if it works against HS2 it works against any operator's tenant. The "test against external org" gates (sigantry GitHub org, JToye org, separate demo Fabric tenant) cannot be exercised against creds that do not yet exist; they are deferred from v3.0.0 and ride post-ship as the adoption story unfolds. v3.0.0 ships when HS2-side gates close + final tag publishes.

**Kept gates (the actual v3.0.0 critical path):**

```
A2 (PYPI_API_TOKEN secret)
A3 (HS2-tenant OIDC federated cred)
A4 (HS2 SPN grants on COE_F_ManagedData + ADO project)
  ↓
C1 (Phase 13 Test 5 — scheduled drift workflow against HS2)
C2 (Phase 13 Test 6 — Teams notification sink E2E with HS2 webhook)
C3 (Phase 12 Tests 2+3 — ADO + GHA approval walkthroughs against HS2)
C4 (Phase 12 Test 4 — rollback drill against COE_F_SBDEVOPS_POC; HS2 SPN already proven there)
  ↓
D (UAT tally update in PRODUCT-BRIEF + CHANGELOG)
  ↓
F1 → F2 → F3 → F4 (final ship: PyPI + PowerShell Gallery + milestone archive)
  ↓
G1 (remove PR #60 if-gate once GHA healthy — gated on external timing)
```

**Deferred gates (do NOT block v3.0.0):**

| Gate | Why deferred to post-v3.0.0 |
|---|---|
| **A1** (`sigantry` GitHub org create) | Org doesn't exist; no business pull until adoption proves the v3.0.0 design. Bralabee namespace stands in if a public surface is needed before the org is created. |
| **B1** (mirror `templates/starter/` → `sigantry/sigantry-starter`) | Depends on A1; same deferral. Mirror parity is already CI-gated locally via `scripts/export-starter.py --dry-run`; the actual public mirror is post-ship. |
| **B2** (mirror `templates/demo/` → `sigantry/demo-sigantry`) | Same as B1; depends on A1. The CI-side closure of DEMO-01 + DEMO-02 is already verified locally. |
| **B3** (JToye org repo provisioning) | JToye org creds don't exist; the maintainer-equivalent run already shipped (`Bralabee/sigantry-jtoye-uat` evidence in HANDOFF.md "Phase 16 / SEAM-06 live evidence"). Replay against `jtoye/` org happens when JToye admin is available. |
| **C5** (Phase 14 fresh-laptop adopter walkthrough) | Depends on B1 (public starter mirror). The PR-bot live half (Phase 14 Test 2) was already proven against `Bralabee/sigantry-jtoye-uat` PR #2 in HANDOFF "Re-run sweep against JToye creds" -- only the adopter-walkthrough sub-test is deferred. |
| **C6** (demo Fabric tenant + Remotion mp4) | Demo tenant doesn't exist yet; provisioning is operator-bound and non-trivial. Local Remotion build (`scripts/remotion/`) already proven via `npm run lint -> exit 0` in 15T3 pre-flight. mp4 capture happens post-tenant-provisioning. |
| **E** (USPTO + WIPO trademark UI re-check) | Web-UI human work; rides post-ship. Programmatic surface (PyPI / npm / domain / GitHub) re-checks happen as part of F1's verification step. |

**Net effect of the rescope:** ~10 of the original 20 gates are kept on the v3.0.0 critical path. The other 10 are deferred to V3.X-ROADMAP.md or to "post-ship adoption" where they belong.

When the kept gates close, tag `v3.0.0` final and run gate **F1** to publish. The deferred gates re-surface naturally as adoption demands them.

---

## Section A — GitHub org + secrets (unblocks Section B/C/F)

### A1 — Create the `sigantry` GitHub organisation [DEFERRED — post-v3.0.0]
- **Status:** Not on v3.0.0 critical path. Deferred to post-ship; see [V3.X-ROADMAP.md](V3.X-ROADMAP.md) "Cleanup carry-forward" for the cross-reference. Bralabee/* stands in for any public surface needed pre-org-creation.
- **Why blocked at API**: GitHub does not expose org-creation via REST. Web UI only.
- **Action**: open https://github.com/account/organizations/new in a browser, name it `sigantry`, choose the free tier, set `bralabala@gmail.com` (Bralabee) + a backup admin as Owners.
- **Verify**: `gh api orgs/sigantry --jq '.login'` returns `sigantry` (will require `gh auth refresh -h github.com -s admin:org` after to manage repos under it).

### A2 — Add `PYPI_API_TOKEN` as a repo secret on `Bralabee/fabric_dataops`
- **Why blocked**: `gh secret list` on this repo currently returns empty; the `release-alpha.yml` publish job requires this secret.
- **Action**:
  ```bash
  # 1. Create a project-scoped token at https://pypi.org/manage/account/token/
  #    (scope it to project `sigantry-core` after the first manual upload, or
  #    to "Entire account" until then).
  # 2. Set the secret:
  gh secret set PYPI_API_TOKEN --repo Bralabee/fabric_dataops --body "<paste-token-here>"
  ```
- **Verify**: `gh secret list --repo Bralabee/fabric_dataops` shows `PYPI_API_TOKEN`.

### A3 — Add the GHA OIDC federated credential for the `sigantry-cd` workflow (V3-RISK-3 partial closure)
- **Why blocked**: needs Azure Entra App Registration admin (`Application.ReadWrite.OwnedBy` minimum) on the JToye and HS2 tenants.
- **Action**:
  ```bash
  # JToye tenant (already has SPN per .env.live AZURE_CLIENT_ID):
  az ad app federated-credential create --id "$AZURE_CLIENT_ID" --parameters '{
    "name": "github-fabric_dataops-master",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:Bralabee/fabric_dataops:ref:refs/heads/master",
    "audiences": ["api://AzureADTokenExchange"]
  }'
  # Repeat for: ref:refs/heads/master (master branch), environment:prod, environment:staging,
  # and pull_request (if you want PR-level integration runs).
  ```
- **Verify**: `az ad app federated-credential list --id "$AZURE_CLIENT_ID" --query '[].name'` lists the four `github-...` entries.

### A4 — Grant the JToye SPN access to `COE_F_ManagedData` + `COE Fabric AIMS` ADO project (V3-RISK-3 closure)
- **Why blocked**: needs HS2 tenant admin to add the SPN as Member on the Fabric workspace + grant `vso.work_write` on the ADO project.
- **Action (Fabric)**: open `app.fabric.microsoft.com` → workspace `COE_F_ManagedData` → Manage access → Add → search for the SPN's display name → role `Member`.
- **Action (ADO)**: `az devops user add --user-principal "$AZURE_CLIENT_ID@<tenant-domain>" --license-type stakeholder --org "https://dev.azure.com/HS2-DataAndAnalytics"` then grant `vso.work_write` via the project's Permissions UI.
- **Verify**: re-run `pytest -m integration tests/integration/workitems/test_live_ado_round_trip.py` with `.env.live` SPN active (no `# B-MODE# ` prefix); expect PASS.

## Section B — Public scaffolding mirrors (Phase 14 Test 1 + Phase 15 Test 1)

These two gates depend on **A1** (sigantry org existing). Until A1, push to `Bralabee/sigantry-starter` and `Bralabee/demo-sigantry` as a stand-in (the export-script parity invariants are namespace-agnostic).

### B1 — Mirror `templates/starter/` to public `sigantry/sigantry-starter` [DEFERRED — post-v3.0.0]
- **Status:** Not on v3.0.0 critical path. Depends on A1; CI-side closure of STARTER-01 already verified locally via `scripts/export-starter.py --dry-run -> exit 0 + 3 OK lines`.
- **Pre-flight (already green)**: `python scripts/export-starter.py --dry-run` → exit 0 + 3 OK lines.
- **Action**:
  ```bash
  cd /tmp
  gh repo create sigantry/sigantry-starter --public --template-public --description "Greenfield Sigantry adoption template — fork to bootstrap your own Fabric DataOps repo"
  git clone --depth 1 git@github.com:sigantry/sigantry-starter.git
  cd sigantry-starter
  cp -a /mnt/Storage/Insync/olusanmi_18th@hotmail.com/OneDrive/Documents/HS2/HS2_PROJECTS_2025/3_DATAOPS_FABRIC_2026/templates/starter/. .
  git add .
  git commit -m "Initial mirror from Bralabee/fabric_dataops@$(git -C /mnt/Storage/Insync/olusanmi_18th@hotmail.com/OneDrive/Documents/HS2/HS2_PROJECTS_2025/3_DATAOPS_FABRIC_2026 rev-parse HEAD)"
  git push origin main
  ```
- **Verify**: `gh repo view sigantry/sigantry-starter --json visibility,isTemplate --jq '.'` shows `{"isTemplate": true, "visibility": "PUBLIC"}`. Plus take a screenshot of the public repo home for `14-HUMAN-UAT.md` Test 1 evidence.

### B2 — Mirror `templates/demo/` to public `sigantry/demo-sigantry` [DEFERRED — post-v3.0.0]
- **Status:** Not on v3.0.0 critical path. Depends on A1; CI-side closure of DEMO-01 already verified via `scripts/export-demo.py --dry-run -> exit 0 + 5 OK lines`.
- **Pre-flight (already green)**: `python scripts/export-demo.py --dry-run` → exit 0 + 5 OK lines.
- **Action**: same shape as B1 but `--target-public` (no `--template-public`) and source dir is `templates/demo/`.
- **Verify**: `gh repo view sigantry/demo-sigantry --json visibility --jq '.visibility'` → `PUBLIC`. Operator clones the new public repo and runs `docs/demo/QUICKSTART.md` end-to-end (15 min) — that closes Phase 15 Test 4.

### B3 — JToye org repo provisioning (Phase 16 Tests 1 + 2) [DEFERRED — post-v3.0.0]
- **Status:** Not on v3.0.0 critical path. Maintainer-equivalent already shipped at `Bralabee/sigantry-jtoye-uat`; the JToye-side replay is operator-bound and rides post-v3.0.0.
- **Action**: with JToye org-admin web access, create `jtoye/sigantry-jtoye` (private), then in a JToye-internal checkout: `cp -a sigantry-jtoye/. .` from this repo + commit + push. Then provision JToye-real-services `NotificationSink` + `WorkItemProvider` impls per the `sigantry-jtoye/sigantry_jtoye/` reference shape.
- **Verify**: maintainer-equivalent already done as `Bralabee/sigantry-jtoye-uat` (HANDOFF "Phase 16 / SEAM-06 live evidence" block, audit_hash `aa8c0aa9...3e31052`); JToye replay follows the same procedure.

## Section C — Live-tenant operator tests (V3-RISK-1/2 closure)

### C1 — Phase 13 Test 5 (scheduled drift-check workflow trigger)
- **Pre-requisites**: `SIGANTRY_TEAMS_WEBHOOK` repo secret (or `SIGANTRY_SLACK_WEBHOOK` / `SIGANTRY_SMTP_*` set; pick one notification sink).
- **Action**:
  ```bash
  gh secret set SIGANTRY_TEAMS_WEBHOOK --repo Bralabee/fabric_dataops --body "<paste-incoming-webhook-url>"
  gh workflow run drift-check.yml --repo Bralabee/fabric_dataops \
    -f workspace_id=<jtoye-trial-workspace-guid> \
    -f manifest_path=fabric-iac/sync.yml \
    -f environment=prod
  gh run watch
  ```
- **Verify**: workflow run reaches the `drift_check` job green; if no drift, `notify` job is skipped (correct); if drift, `notify` job posts to Teams. `drift.json` artefact present in the run.

### C2 — Phase 13 Test 6 (Teams notification sink end-to-end)
- **Action**: inject synthetic drift, re-run C1, check the Teams channel for the formatted post. Or run locally:
  ```bash
  set -a; source .env.live; set +a
  export SIGANTRY_NOTIFICATION_SINK=teams
  python -m sigantry_core.sync._notify_main --from <synthetic-drift.json>
  ```
- **Verify**: Teams channel shows the drift summary card.

### C3 — Phase 12 Tests 2 + 3 (ADO + GHA pipeline UI walkthroughs)
- **Pre-requisite**: an ADO project with the SPN-bound service connection + a reviewer environment configured.
- **Action**: trigger the 5-stage `sigantry-cd.yml` template via `az pipelines run` (ADO half) or `gh workflow run` (GHA half) against a JToye Trial workspace; click through the manual approval at the `approval` stage; verify the `promote` stage runs only after approval.
- **Verify**: 1 screenshot of the ADO/GHA approval gate (with reviewer name + timestamp visible) per phase; landed in `12-HUMAN-UAT.md` Test 2/3 evidence.

### C4 — Phase 12 Test 4 (rollback drill CLI)
- **Pre-requisite**: a deploy ledger with at least 2 deploy records on the same workspace.
- **Action**:
  ```bash
  set -a; source .env.live; set +a
  AUDIT_DIR=$(mktemp -d)
  # Run Test 1 first to seed the ledger:
  sigantry deploy run --params parameters.yml --workspace-id "$WSID" --environment DEV \
    --source ./fabric_items --audit-dir "$AUDIT_DIR"
  # Edit something, deploy again to seed release B:
  sigantry deploy run --params parameters.yml --workspace-id "$WSID" --environment DEV \
    --source ./fabric_items --audit-dir "$AUDIT_DIR"
  # Diff the two:
  sigantry release diff $(ls "$AUDIT_DIR/deploys/" | head -1) $(ls "$AUDIT_DIR/deploys/" | tail -1) --json
  # Rollback to the first:
  sigantry deploy run --rollback --to-release $(ls "$AUDIT_DIR/deploys/" | head -1) \
    --workspace-id "$WSID" --audit-dir "$AUDIT_DIR"
  # Confirm rollback record:
  sigantry release list --audit-dir "$AUDIT_DIR" | tail -3
  ```
- **Verify**: `release list` shows three records; the third has `outcome=rolled-back` and `to_release_id` matching record #1.

### C5 — Phase 14 Tests 2/3/4 (PR-bot live PRs + fresh-laptop adopter walkthrough) [PARTIAL — adopter walkthrough DEFERRED]
- **Status:** PR-bot live PRs already proven against `Bralabee/sigantry-jtoye-uat` PR #2 (HANDOFF "Re-run sweep against JToye creds"). Adopter-walkthrough sub-test is deferred -- depends on B1 (public starter mirror).
- **Action**: after B1, fork `sigantry/sigantry-starter` to a clean test repo, run through README from scratch on a different machine (or fresh container), open a PR that touches a `*.tmdl`, `**/.platform`, or `*.Lakehouse/**` file, watch the PR-bot post the diff comment.
- **Verify**: PR carries the diff comment; fresh-laptop walkthrough completes in ≤15 min.

### C6 — Phase 15 Tests 1-4 (demo Fabric tenant + Remotion mp4) [DEFERRED — post-v3.0.0]
- **Status:** Not on v3.0.0 critical path. Demo tenant doesn't exist; mp4 capture is post-ship adoption work. Local Remotion build proven via `cd scripts/remotion && npm run lint -> exit 0` in 15T3 pre-flight (HANDOFF).
- **Pre-requisite**: a separate demo Fabric tenant + capacity (NOT shared with JToye/HS2).
- **Action**:
  ```bash
  # Provision tenant + capacity per docs/runbooks/demo-tenant-operator.md
  # Set: SIGANTRY_DEMO_TENANT_ID/WORKSPACE_ID/CAPACITY_ID/FABRIC_TOKEN
  # Then run the 15-minute QUICKSTART end-to-end:
  cd /tmp && git clone https://github.com/sigantry/demo-sigantry  # after B2 lands
  cd demo-sigantry
  pip install "sigantry-core>=3.0"
  sigantry config validate parameters.yml
  sigantry sync apply --manifest sync.yml --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID"
  sigantry diff --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID" --manifest sync.yml
  # Capture 5 PNGs from the live demo workspace UI per scripts/remotion/README.md:
  cd <fabric-dataops>/scripts/remotion
  npm ci && npm run render
  # Promote the resulting out/walkthrough.mp4 to a public GitHub Release on demo-sigantry.
  ```
- **Verify**: walkthrough.mp4 plays end-to-end; demo workspace items match `templates/demo/sync.yml`; `sigantry diff` exits 0 (no drift).

## Section D — UAT closure tally update

After C1-C6 close, update `docs/PRODUCT-BRIEF.md` "Status" + this repo's CHANGELOG `[Unreleased]` block to record each Test as PASS with link to evidence (workflow run id, screenshot path, audit hash). Then move the `[Unreleased]` block under a `## [3.0.0] - YYYY-MM-DD` heading.

## Section E — Trademark + domain final re-checks (V3-RISK-1) [PARTIAL — programmatic surfaces in F1; USPTO/WIPO DEFERRED]

USPTO + WIPO web-UI re-checks are deferred to post-v3.0.0. The programmatic surfaces (PyPI / npm / domain / GitHub) get re-checked automatically as part of F1's verification step — list below describes them in case you want to run them ahead of the publish.

- **PyPI**: `curl -sI https://pypi.org/pypi/sigantry-core/json` (must return 404 today; will return 200 after F1 publish — that's success).
- **npm**: `curl -sI https://registry.npmjs.org/sigantry-core` (must return 404 — we're not publishing to npm).
- **Domain**: `dig sigantry.com sigantry.io sigantry.dev +short` (any IP returned = squatted; empty = available; OK either way unless a competing data product appears).
- **GitHub**: `gh repo view sigantry --json owner --jq '.'` (must return 404 today; will return owner=`sigantry` after A1).
- **USPTO + WIPO**: web-UI search at https://tmsearch.uspto.gov and https://branddb.wipo.int (operator must do; flag any conflict for legal review before F1).

## Section F — Final ship

### F1 — Publish `sigantry-core 3.0.0` to PyPI
- **Pre-requisites**: A2 (PYPI_API_TOKEN), all of Section C green, all of Section E re-confirmed clear.
- **Action**:
  ```bash
  # 1. Tag final on master:
  cd /mnt/Storage/Insync/olusanmi_18th@hotmail.com/OneDrive/Documents/HS2/HS2_PROJECTS_2025/3_DATAOPS_FABRIC_2026
  git fetch github master
  git checkout master && git reset --hard github/master
  git tag -a v3.0.0 -m "Sigantry 3.0.0 — operator UAT closure"
  git push github v3.0.0
  # 2. The release-alpha.yml workflow auto-triggers on `refs/tags/v*`; watch it:
  gh run watch
  # 3. Verify PyPI:
  curl -sI https://pypi.org/pypi/sigantry-core/3.0.0/json
  pip install sigantry-core==3.0.0  # in a fresh virtualenv
  ```
- **Verify**: `pip show sigantry-core` reports version 3.0.0 with License: Apache-2.0; `python -c "import sigantry_core; print(sigantry_core.__version__)"` echoes 3.0.0.

### F2 — Publish `sigantry-hs2` and `sigantry-jtoye` to PyPI
- Same shape as F1 against `sigantry-hs2/pyproject.toml` and `sigantry-jtoye/pyproject.toml`. Tags: `hs2-v3.0.0` and `jtoye-v3.0.0` (per the existing `release-alpha.yml` matrix).
- **Verify**: `pip install sigantry-hs2 sigantry-jtoye` resolves; `sigantry doctor` lists the new plugins.

### F3 — Publish `Sigantry` and `SigantryHs2` to PowerShell Gallery
- **Pre-requisite**: PSGallery API key as a secret (`POWERSHELL_GALLERY_API_KEY`); `gh secret set` similar to A2.
- **Action**: tag `ps-v3.0.0` and let the existing publish workflow run (or `Publish-Module -Path ./Sigantry -NuGetApiKey "$KEY"` from a Windows runner).

### F4 — Archive the v3.0 milestone in `.planning/`
- `mv .planning/milestones/v3.0-* .planning/milestones/archive/v3.0-shipped/` per `gsd-complete-milestone` skill.
- Bump `.planning/STATE.md` to next-milestone-pending.

## Section G — Post-ship cleanup

### G1 — Remove the PR #60 `if:` gate once GHA is healthy
- File: `.github/workflows/ci.yml`, the `Build wheel` job. The `if:` gate was a probe-fix during the 2026-04-30 GHA outage; once GitHub fixes their runner pool, remove the comment block + `if:` line and verify a green `Build wheel` run on a master push.

### G2 — Drop the v3.0 deprecation shims in v3.1 [CLOSED 2026-06-11]
- **Status:** Closed by the v3.1.0 SHIM-DROP release PR: `shim/Fabric/`, `shim/fabric-dataops-toolkits/`, `shim/fabric-dataops-toolkits-hs2/` and `tests/shim/` removed; first-party legacy entry-point tables dropped from both pyprojects; CI/Makefile shim installs removed; banned-API `shim` exclusion removed so re-introduction trips the gate. The registry's legacy-group dual-read is retained as a third-party grace window (removal: V3.X-ROADMAP v3.2 LEGACY-SURFACE-DROP).

### G3 — Resolve the ghost-runs glitch [CLOSED 2026-05-12]
- **Status:** Closed by removing `${{ github.run_id }}` and `${{ github.actor }}` from the workflow_dispatch / workflow_call **input default values** in `.github/workflows/sigantry-cd.yml` (lines 43, 45, 56, 58 pre-fix). GitHub's workflow parser rejects the `github.*` context inside `default:` slots -- defaults are evaluated at parse time, before the `github` context is bound -- causing the workflow to fail registration with the error `Unrecognized named-value: 'github'. Located at position 1 within expression: github.run_id` (revealed via a manual `gh workflow run` dispatch attempt 2026-05-12).
- **Why the earlier hypotheses were wrong:**
  - **B (create the referenced environments) -- WRONG.** Environments are resolved per-invocation via `${{ inputs.X }}`; their existence at registration time doesn't matter. Created 5 generic environments (`dev`, `preprod`, `prod`, `preprod-approval`, `prod-approval`) as a side-investigation and they did not affect the registration.
  - **(rename alone) -- INSUFFICIENT.** A first rename `sigantry-deploy-with-tests.yml` -> `sigantry-cd.yml` allocated a new workflow id (275279663), but the new id immediately also mis-registered as path-as-name because the underlying parse error was still present.
- **The real fix:** removed the `${{ github.* }}` expressions from input defaults (defaults now empty strings); moved the `github.run_id` / `github.actor` fallback into the promote job's `env:` block using the `${{ inputs.X != '' && inputs.X || github.Y }}` pattern. Verified live via `gh workflow run` against the new branch returning the run record (no longer the parse error).
- **Side effects:** the GHA file was renamed (`sigantry-cd.yml`), and the ADO half was renamed in lockstep to preserve dual-CI parity basename matching (`templates/stages/sigantry-cd.yml`). Tests + 7 doc cross-refs updated. CHANGELOG `[Unreleased]` records the closure. The pre-existing 100+ failed-run records against the old workflow ids 267540691 and 275279663 are bulk-deleted via `gh api` post-final-merge.

---

## Quick-reference: which gate needs which credential

| Credential / access | Gates it unlocks |
|---|---|
| GitHub web UI org-admin (one-time, web only) | A1, B1, B2, B3, C5 |
| `admin:org` scope on `gh` (`gh auth refresh -s admin:org`) | A1 follow-on management; A4 ADO user provisioning |
| PyPI account with project-create rights | A2, F1, F2 |
| Azure Entra App admin on JToye + HS2 tenants | A3, A4 |
| HS2 Fabric tenant + ADO admin | A4, C1-C4 against HS2 workspaces |
| Demo Fabric tenant (separate provisioning) | C6 |
| Teams workspace admin (incoming-webhook URL) | C1, C2 |
| PowerShell Gallery account | F3 |

When all of Section A + B + C + E close, run F1-F4 in order.
