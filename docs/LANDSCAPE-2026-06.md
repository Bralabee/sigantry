# Landscape Review — Sigantry vs Official + Open-Source Fabric Tooling

**Surveyed:** 2026-06-11 (web research against learn.microsoft.com, blog.fabric.microsoft.com,
PyPI, and GitHub; three parallel research passes covering official tooling, community tooling,
and the raw Fabric API surface).
**Sigantry version reviewed:** `3.0.0rc1` (master, post-PR-#102).
**Companion document:** [`RELATED-WORK.md`](RELATED-WORK.md) benchmarks against one proprietary
sibling tool; this document benchmarks against the official Microsoft and open-source ecosystem.
**Addenda:** §8 re-verification (2026-06-17); §9 re-survey (2026-08-24, sigantry-core 3.4.1 —
verdict stands, one §8 citation corrected).

Claim labels used throughout:

- `[VERIFIED]` — fetched from a primary source (Microsoft docs, official blog, PyPI, the
  project's own GitHub repo) during the 2026-06-11 survey; URL given.
- `[VENDOR-BLOG]` — sourced from a third-party conference recap or consultancy blog, not
  Microsoft; treat as directionally correct, not authoritative.
- `[UNVERIFIED]` — could not be confirmed during the survey; stated explicitly.

---

## 1. Executive verdict

Sigantry's wrapper-first architecture ("wrap upstream, don't DIY") was **vindicated** by the
2026 platform wave: the deploy engine it wraps became officially Microsoft-supported, so the
toolkit inherited a support tailwind rather than a maintenance liability. Measured against the
ecosystem as of June 2026, the surface splits into three tiers:

1. **Wrapped convenience surface (~40% of CLI verbs)** — thin REST verb mappings (workspace /
   capacity / git CRUD) now duplicated by GA, SLA-backed official tools. Correct engineering
   for operator convenience and uniform auth/audit; zero *standalone* adoption value.
2. **Uncontested original work (~35%)** — drift detection, deploy rollback, TMDL-aware
   PR-review bot, hash-chained audit ledgers. **No official or open-source equivalent was
   found for any of these** during targeted searching.
3. **Under active erosion (~25%)** — the sync/publish engine and parts of the governance
   export surface, where FabCon 2026 (March) announcements introduced native primitives that
   narrow (but do not close) the gap.

The defensible product identity is the **governance, audit, and traceability layer on top of
official tooling** — not "a Fabric automation toolkit", which is now a crowded, Microsoft-owned
lane.

---

## 2. Official Microsoft tooling — state of play

### 2.1 fabric-cicd (the deploy engine Sigantry wraps)

- `[VERIFIED]` Current version **1.1.0** (2026-05-27), MIT, Python 3.9-3.13.
  https://pypi.org/project/fabric-cicd/
- `[VENDOR-BLOG]` At FabCon 2026 (March) fabric-cicd was elevated from community project to an
  **officially supported, Microsoft-backed tool** with committed roadmap ownership.
  Official confirmation: https://blog.fabric.microsoft.com/en-us/blog/announcing-official-support-for-microsoft-fabric-cicd-tool/ `[VERIFIED]`
- `[VERIFIED]` Notable 1.x changes (changelog,
  https://microsoft.github.io/fabric-cicd/latest/changelog/):
  - **1.0.0 (2026-04-20), breaking:** explicit token credentials required; **hard delete**
    (permanent item removal); `get_changed_items()` for caller-driven selective deploys.
  - **1.1.0 (2026-05-27):** dynamic replacement variables for workspace display names;
    private-link workspace FQDN config; DataBuildToolJob item type.
- `[VERIFIED]` Still does NOT do (per official docs): workspace creation/provisioning, rollback
  ("redeploy a previous commit yourself"), commit-diff incremental deploys.
  https://microsoft.github.io/fabric-cicd/latest/

**Sigantry impact:** the `>=1.0,<2.0` pin remains current. Two version-drift chores before UAT
closure: (a) re-check whether the PR #54 `$ENV:` tempfile substitution
(`sigantry_core/deploy/parameters.py`) is still required against 1.1.0's dynamic replacement
variables; (b) reconcile 1.0.0's hard-delete semantics against `--unpublish-orphans` and the
`folders[]` preservation contract.

### 2.2 Fabric CLI (`fab`)

- `[VERIFIED]` **GA since May 2025**, open-sourced October 2025, current version **1.6.1**
  (2026-04-29), MIT. https://github.com/microsoft/fabric-cli
- `[VERIFIED]` **v1.5.0 (2026-03-12) embeds fabric-cicd as `fab deploy`** — one-command
  workspace deployment from the official CLI.
  https://github.com/microsoft/fabric-cli/releases
- `[VERIFIED]` Surface: filesystem-style navigation, item/workspace CRUD, OneLake file + table
  ops, job control, raw REST via `fab api` with JMESPath, SPN / managed-identity /
  federated-token auth.

**Sigantry impact:** any pitch of Sigantry as "easier Fabric automation" is dead — `fab` owns
that lane with an SLA. Sigantry's CRUD verbs remain justified only as governed convenience
(single HTTP client, retry/token policy, destructive-op gates, audit emission — `fab rm` writes
no ledger entry; `sigantry workspace delete` does).

### 2.3 Terraform provider for Microsoft Fabric

- `[VERIFIED]` GA since v1.0.0 (2025-03-31); current **v1.11.0 (2026-06-09)**, monthly cadence.
  https://github.com/microsoft/terraform-provider-fabric/releases
- `[VERIFIED]` Coverage: workspace lifecycle + identity, workspace RBAC, Git integration
  (ADO + GitHub), **folders (v1.4.0)**, deployment pipelines, domains, gateways/connections,
  **tenant settings as code (v1.8.0)**, OneLake data-access security (v1.11.0), wide item-type
  coverage.
- `[VERIFIED]` **No Variable Library resource yet** (open issue):
  https://github.com/microsoft/terraform-provider-fabric/issues/515
- `[UNVERIFIED]` Whether a capacity *resource* (vs data source) exists; historically capacity
  creation lives in `azurerm`/`azapi`. Provider license not re-confirmed (typically MPL-2.0).

**Sigantry impact:** Terraform is the heavyweight incumbent against `workspace bootstrap`.
Sigantry's niche is the operator-driven, stateless (no state file), single-verb, audited
idempotency flow. The docs currently say nothing about when to choose which — see
recommendation 5.

### 2.4 Native platform features (FabCon 2026 wave)

- `[VERIFIED]` **Bulk Export and Import Item Definition APIs (Preview, March 2026)** —
  batch export/import of item definitions, long-running async, SPN + managed-identity auth,
  dependency-ordered import; positioned explicitly for CI/CD and workspace cloning.
  https://blog.fabric.microsoft.com/en-US/blog/public-apis-bulk-import-and-export-items-definition-preview/
  This is the closest platform-native analog to `sigantry sync apply --with-publish` to date.
- `[VERIFIED]` **Branched workspaces / selective branch-out (Preview)** — feature-workspace
  lifecycle with selective item branching and dependency pull-in.
  https://learn.microsoft.com/en-us/fabric/cicd/git-integration/branched-workspace
- `[VENDOR-BLOG]` **Git diff comparison** — item-level and file-level diffs before
  commit/update, announced at FabCon 2026.
- `[VERIFIED]` **Variable Libraries are GA** (overview doc carries no preview banner; full REST
  API incl. `updateDefinition` + active value-set switching), with a new **connection-reference
  variable type** (March 2026).
  https://learn.microsoft.com/en-us/fabric/cicd/variable-library/variable-library-overview
- `[VERIFIED]` **Tenant settings are now read AND write via API** (update endpoints plus
  capacity/workspace/domain-level delegated override CRUD; 25 req/min rate limit; SPN/MI
  supported for most admin APIs).
  https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/update-tenant-setting
- `[VERIFIED]` **Fabric MCP servers** — remote Core MCP server (Preview; workspace/item/
  permission/folder management as typed MCP tools, Entra-authenticated, audit-logged) and a
  local GA open-source server.
  https://learn.microsoft.com/en-us/rest/api/fabric/articles/mcp-servers/what-is-fabric-mcp-server
- `[VERIFIED]` **Deployment pipeline APIs**: selective deploy by item ID exists, but **no
  built-in change detection between stages** and no rollback verb; 300-item deploy cap;
  Dataflows unsupported via the Fabric pipeline API.
  https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/pipeline-automation-fabric
- `[VERIFIED]` Git integration providers remain **Azure DevOps, GitHub, GitHub Enterprise
  (cloud) only** — no GitLab/Bitbucket; GitHub still requires PAT credentials for stored
  connections.
  https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration
- `[VERIFIED]` **No native drift detection feature exists.** Docs, roadmap search, and FabCon
  recaps all came up empty; environment-drift mitigation is still framed as "use Variable
  Libraries + deployment rules". Closest primitives: `git/status` + the new Git diff UI.
- `[VERIFIED]` **No native workspace-template / blueprint bootstrap feature exists** beyond the
  Create Workspace API, Terraform, and Extensibility Toolkit lifecycle notifications (Preview).

### 2.5 Other official tooling

- `[VERIFIED]` **semantic-link-labs 0.15.1** (2026-05-19) — capacity create/suspend/resume,
  workspace + deployment-pipeline management, semantic-model lifecycle, SPN support; notebook-
  first. https://pypi.org/project/semantic-link-labs/
- `[VERIFIED]` **fabric-toolbox** (Fabric CAT team, MIT, ~816 stars) — FUAM unified admin
  monitoring, cost analysis, CI/CD accelerators, assessment tool.
  https://github.com/microsoft/fabric-toolbox
- `[VERIFIED]` **skills-for-fabric** (announced ~June 2026) — agent-oriented skill packs for
  GitHub Copilot / Claude / CLI. https://github.com/microsoft/skills-for-fabric

---

## 3. Open-source / community tooling — state of play

| Tool | Maintainer / activity | Relevance to Sigantry |
|---|---|---|
| `msfabricpysdkcore` 0.3.2 (2026-05-13) `[VERIFIED]` | Single maintainer (DaSenf1860), 37 stars, tracks new Fabric APIs within weeks. https://github.com/DaSenf1860/ms-fabric-sdk-core | Sigantry's pin `>=0.3.1,<0.4` is current. Bus-factor-of-one risk is mitigated by wrapping. |
| `FabricTools` 0.32.0 (2026-05-06) `[VERIFIED]` | dataplat org (multi-maintainer), explicitly "public preview - not for production". https://github.com/dataplat/FabricTools | PowerShell-side overlap with the `Sigantry` PS module; neither is a differentiator. |
| FabricPS-PBIP / pbi-tools `[VERIFIED]` | Sample-grade / ~17 months without a release respectively; being structurally obsoleted by native PBIP/PBIR/TMDL formats. | Legacy; not a competitive concern. |
| fabric-cicd marketplace wrappers `[VERIFIED]` | ChantifiedLens ADO extension v0.2.7 (2026-05-19, ~30 installs) + matching GitHub Action; both Preview-stage. | Confirms the "thin wrapper around fabric-cicd" lane is commodity. |
| `bennyaustin/fabric-accelerator` v4.0 `[VERIFIED]` | 148 stars / 207 forks; metadata-driven medallion ELT + Bicep + GHA. https://github.com/bennyaustin/fabric-accelerator | Different shape (ELT framework, not governance toolkit); no manifest sync or audit layer. |
| `pyfabricops` 0.6.0 (2026-05-07) `[VERIFIED]` | Single maintainer, 43 stars, YAML-based CI/CD wrapper over Fabric REST. https://github.com/alisonpezzott/pyfabricops | Closest community analog in spirit; far smaller surface, no audit/governance layer. |
| Tabular Editor 2 / ALM Toolkit `[VERIFIED]` | TE2 in maintenance mode; ALM Toolkit does TMDL diffs but is GUI-only, no headless PR mode. | Leaves the headless TMDL-diff-in-PR lane open. |
| dbt-fabric 1.10.0 + native "dbt job" item (Preview) `[VERIFIED]` | Microsoft-maintained; dbt becoming a first-class Fabric workload. https://learn.microsoft.com/en-us/fabric/data-factory/dbt-job-overview | Adjacent DataOps path, not overlapping the governance surface. |

**Verified open lanes (no incumbent found, official or community):** Fabric-native drift
detection (snapshot/diff), YAML-manifest workspace bootstrap as a packaged product, TMDL-aware
PR-review bots, deploy rollback, and integrity-checked audit-ledger tooling. Absence-of-evidence
caveat: a private or unindexed equivalent could exist; none is discoverable.

---

## 4. Capability-by-capability verdict

| Sigantry capability | Official equivalent | OSS equivalent | Verdict |
|---|---|---|---|
| Workspace / capacity / git / variable-library CRUD verbs | `fab` CLI 1.6.1 (GA, SLA) | msfabricpysdkcore, FabricTools, pyfabricops | **Commodity.** Keep for governed convenience; never market standalone. |
| `deploy run` (wraps fabric-cicd) | fabric-cicd 1.1.0, officially supported; `fab deploy` | Two marketplace wrappers | **Commodity wrapper, correct architecture.** Upstream support posture strengthened. |
| `workspace bootstrap` (YAML blueprint, probe-before-act, `BootstrapRecord`) | Terraform provider v1.11.0 | None packaged | **Contested.** Differentiate on stateless audited idempotency vs state-file IaC. |
| `sigantry diff` (drift detection) | **None (verified absent)** | **None found** | **Uncontested differentiator.** |
| `deploy run --rollback --to-release` | **None** (no rollback verb anywhere in the platform) | None found | **Uncontested differentiator.** |
| `pr-bot` (TMDL + Lakehouse metadata PR diffs) | ALM Toolkit (GUI-only) | **No headless TMDL PR-bot found** | **Uncontested differentiator.** |
| Audit ledgers (`DeployRecord` / `BootstrapRecord` hash chains), destructive-op gates, release traceability | Purview audit log (observation only; no deploy-time ledger, no verify-without-trust) | None found | **Uncontested differentiator — the moat.** |
| `sync apply/pull` (manifest workspace-as-code) | **Bulk Export/Import APIs (Preview)** | fabric-accelerator (different shape) | **Under erosion** on the publish half; manifest/folder-reconcile/preservation half remains unique. |
| `tenant-settings export`, `rbac-audit`, `label-sync` | Tenant-settings write APIs; Terraform v1.8 tenant settings; admin `bulkSetLabels` | FUAM (monitoring only) | **Partially eroded.** Raw primitives now exist; reconciliation + audit framing remains. |
| Plugin seams (11 protocols, 17 plugins) | Nothing comparable | Nothing comparable | **Differentiated architecture**; value realises with multiple customers. |

---

## 5. New API surfaces with no Sigantry verb yet

All `[VERIFIED]` against Microsoft docs during the survey:

1. **Bulk Export/Import item definition APIs** (Preview) — candidate alternative backend for
   the sync-publish path (see [ADR-0012](decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md)).
2. **Connection-reference variables** in Variable Libraries (March 2026).
3. **Workspace outbound-access-protection / Outbound Gateway Rules API** — notable: when
   workspace private links are enabled these rules are API-only (no portal UI), a natural fit
   for a governance toolkit.
4. **Delegated tenant-setting override CRUD** at capacity / workspace / domain level.
5. **Workspace-level surge protection** (Preview) — `[UNVERIFIED]` whether settable via public
   REST API.

---

## 6. Recommendations (priority order)

1. **Reposition the narrative** in [`PRODUCT-BRIEF.md`](PRODUCT-BRIEF.md) and the sales
   collateral: governance, audit, and rollback layer *on top of* official tooling — not an
   automation toolkit *beside* it. The FabCon 2026 wave is tailwind, not threat, under that
   framing.
2. **Recheck the fabric-cicd 1.1.0 delta before UAT closure:** `$ENV:` substitution necessity,
   hard-delete interplay with orphan handling, and whether `get_changed_items()` enables a
   selective-deploy story the deployment-pipeline API still lacks.
3. **Spike wrapping the Bulk Export/Import APIs** as an alternative sync-publish backend
   (v3.x candidate; revisit ADR-0012/0013 boundary reasoning against them).
4. **Double down on the uncontested four** — drift detection, rollback, TMDL PR-bot, audit
   ledger. Each was verified to have no official or open-source competitor as of 2026-06-11.
5. **Add a Terraform-vs-bootstrap positioning section** to [`../CONSUMING.md`](CONSUMING.md):
   Terraform for fleet provisioning / state-managed IaC; `workspace bootstrap` for
   operator-driven, audited, stateless idempotency. Evaluators will ask.
6. **Do not expand thin CRUD verbs** — that lane is permanently lost to `fab`.
7. **Watch items:** branched workspaces (Preview) likely removes the need to build a
   feature-workspace lifecycle (the gap flagged in [`RELATED-WORK.md`](RELATED-WORK.md) section 2);
   MCP/skills signal automation UX moving toward AI agents, which increases (not decreases)
   the need for an audit layer.

---

## 7. Caveats

- FabCon 2026 recap claims marked `[VENDOR-BLOG]` come from consultancy blogs (Lytix, Inviso,
  Purple Frog, VNB), not Microsoft; primary-source confirmation was obtained where stated.
- `[UNVERIFIED]` items: Terraform capacity-resource existence and provider license; Variable
  Library formal GA date (inferred from the absence of a preview banner); `fab` CLI REPL /
  AI-agent layer details; surge-protection API settability; Bulk API rate limits.
- The survey was a one-day, three-pass web sweep. Percentages in section 1 are judgment calls
  over the CLI verb inventory in [`CAPABILITIES.md`](CAPABILITIES.md), not measured counts.
- Ecosystem velocity is high (monthly Terraform releases, ~6-weekly fabric-cicd releases);
  re-run this survey before the next milestone's planning cycle.

---

## 8. Re-verification — 2026-06-17 (primary sources)

A targeted re-check of the three deploy-mechanics "deltas" most often cited as why the toolkit
exists. The §1 executive verdict and §4 capability table **stand**; this sharpens three rows.

- **Notebook env/lakehouse re-binding — now mostly Microsoft's.** `fabric-cicd` parameterization
  + deployment-pipeline **default-lakehouse rules** + Git **auto-binding** rebind notebooks
  across stages, though auto-binding is *same-workspace only* and currently buggy (adds the new
  lakehouse without removing the old — [fabric-cicd #311](https://github.com/microsoft/fabric-cicd/issues/311)).
  Residual toolkit value: cross-workspace + REST-path correctness **today**, not durable.
  `[VERIFIED]` [Notebook source control & deployment](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-source-control-deployment).
- **Environment custom-library (wheel) management — covered by the Git-item model.** Environment
  Git integration versions custom libraries (`Libraries/CustomLibraries`, add/delete files) and
  deployment pipelines deploy them. The toolkit's `env reconcile` only adds value for the
  REST/external-feed-wheel workflow (a workflow choice, not a platform gap).
  `[VERIFIED]` [Environment Git integration & deployment pipeline](https://learn.microsoft.com/en-us/fabric/data-engineering/environment-git-and-deployment-pipeline).
- **DQ-gate-blocks-promotion — natively achievable.** Azure DevOps / GitHub **deployment gates +
  environment checks** run any custom check (e.g. a DQ suite via Azure Function / HTTP / pipeline
  step) and block promotion on failure — the same mechanism the toolkit's `ApprovalGate` seam
  already *wraps*. So this is differentiated **packaging**, not a unique capability.
  `[VERIFIED]` [Azure DevOps deployment gates](https://learn.microsoft.com/en-us/azure/devops/pipelines/release/approvals/gates?view=azure-devops).
- **Audit — Purview is broader than previously framed.** "All Microsoft Fabric user activities
  are logged" in Purview, **including REST API operations**, not just deployment-pipeline runs.
  The toolkit's `DeployRecord` differentiates only on being an *integrity-checked, deploy-scoped,
  tooling-owned* provenance ledger — the §4 "moat" row holds, but narrowed to that shape.
  `[VERIFIED]` [Track user activities](https://learn.microsoft.com/en-us/fabric/admin/track-user-activities).

**Net:** the four uncontested originals in §1/§4 (drift detection, rollback, headless TMDL
PR-bot, integrity-checked ledger) remain the durable identity; the deploy-mechanics deltas are
weaker than they looked and are best handled by **delegating to `fabric-cicd`** rather than
maintaining parallel code. Recorded in [`reference/scope.md`](reference/scope.md) §7.

---

## 9. Re-survey — 2026-08-24 (the §7 "re-run before the next planning cycle")

Method: version numbers, dates, release bodies, and issue states read from the **GitHub REST
API and PyPI JSON** (not rendered pages); Microsoft Learn pages fetched directly. The Fabric
blog returns HTTP 403 to automated fetch, so blog-only claims below rest on the
`MicrosoftDocs/fabric-docs` what's-new file and are the weakest-sourced items; everything else
is primary. The §1 executive verdict and the §4 capability table **stand, strengthened**.

### 9.1 Upstream releases since §8

- `[VERIFIED]` **fabric-cicd 1.2.0 (2026-06-30) and 1.3.0 (2026-08-10)** — bulk publish mode
  (single bulk-import API call, opt-in via `enable_bulk_publish`), Map / Paginated Report /
  MirroredDatabase support, dynamic `find_value`, retry-duration env var, opt-in local file
  logging, owner-only POSIX file perms. **No 2.0 exists** (no milestones, no v2 branch, no
  breaking changes in any 1.x release body since 1.0.0), so the `>=1.0,<2.0` pin is not under
  pressure. https://github.com/microsoft/fabric-cicd/releases
- `[VERIFIED]` **Fabric CLI 1.7.0 (2026-08-19)** — experimental `deploy --bulk_publish`, new
  `bulk-export`, environment definition support. `fab deploy` is now a documented first-class
  path (Learn local-deployment tutorial updated 2026-07-22) and inherits fabric-cicd's
  semantics wholesale. https://github.com/microsoft/fabric-cli/releases
- `[VERIFIED]` **Terraform provider** v1.12.0 (2026-07-08) → v1.13.0 (2026-08-17), monthly
  cadence unchanged. https://github.com/microsoft/terraform-provider-fabric/releases
- `[VERIFIED]` Platform, June–August: GitHub Enterprise Cloud data-residency Git integration
  (GA, June); branched workspaces / selective branching / in-portal compare-changes still
  **Preview** (July); no August feature summary published as of the survey date. The June
  "Approval Activity" is a Data Factory *pipeline activity* (human-in-the-loop inside a data
  pipeline run), **not** a deployment approval gate.

### 9.2 The four uncontested rows — re-tested, all still uncontested

- **Drift detection:** fabric-cicd's own request (#490) was closed 2025-10-14 **"due to
  aging"**; maintainer guidance is "redeploy on a schedule". The in-portal git compare is
  interactive and human-driven, not headless/scheduled. Still no product, official or OSS.
- **Rollback:** nothing anywhere in the platform; the deployment-pipelines rollback Idea
  remains an open community idea, not a roadmap item.
- **Integrity-checked audit ledger** ([threat model](reference/audit-ledger-threat-model.md))**:**
  Purview still records *that operations happened*, not what
  a release contained; fabric-cicd 1.2.0 made its local logging **opt-in** — upstream moved
  toward *less* default audit, not more.
- **Destructive-op gating:** upstream moved the opposite direction — `enable_hard_delete`
  bypasses the workspace recycle bin and destructive semantic-model schema changes became
  allowable; dry-run/validate-only (#984) is still an open request. Headless TMDL PR-review
  remains an empty lane (TMDL View on the web is browser editing, not CI-side review).

### 9.3 Correction to §8

§8's citation of **fabric-cicd #311** as the same-workspace auto-binding bug was **wrong**:
#311 is a `parameter.yml` usage question, opened 2025-05-21, closed 2025-06-06, label
`question` (verified via the GitHub issues API). The underlying "auto-binding leaves stale
bindings" claim is therefore downgraded to `[UNVERIFIED]` — not disproven. The practical
consequence is unchanged: **no fabric-cicd fix for binding-wipe-on-republish has shipped or is
in flight** (#932 was closed `completed` with parameterization guidance and a post-close user
report that DEV environment IDs still deploy; #1058, open, shows a failed DataPipeline publish
still mutates the target item), and Variable-Library-in-Notebooks (GA December 2025) is
authored opt-in configuration that never *restores* wiped metadata — so `set-binding` stays
necessary.

### 9.4 §6 recommendations — status

Recommendation 2 (the version-drift chores) was executed 2026-08-24: fabric-cicd lock bumped
1.1.0 → 1.3.0 (repo PR #179; surgical `--upgrade-package`, pin unchanged, urllib3 to its new
2.7.0 floor). The delta audit re-confirmed all three §8-era findings against the installed
1.3.0 via the contract canaries in
`tests/sigantry_core/deploy/test_fabric_cicd_contract.py`: `$ENV:` substitution stays
toolkit-side (upstream's flag-gated path still reads only env vars literally named
`$ENV:VAR`), the D-17-09 double-flag gate is unchanged, and `enable_hard_delete` remains
opt-in with `sigantry_core` never setting it. Recommendation 3 (bulk export/import spike) got
cheaper: 1.2.0's `enable_bulk_publish` is now a shipped, flag-gated backend candidate for the
sync-publish path rather than a raw-API wrap. Recommendations 1, 4–7 unchanged.

**Next re-run:** after **FabCon Europe (Barcelona, 2026-09-28 → 10-01)** — the next likely
announcement wave; also watch fabric-cicd #984 (validate-only — would erode the *Proposed*
ADR-0015 preflight) and #1058 (failed publish mutates target).
