# Sigantry — Product Brief

**Status:** Draft v0.2 (positioning refreshed 2026-06-11 against [LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md); originally v0.1, 2026-04-24, milestone v3.0 Phase 10)
**Maintainer:** platform team
**Licence:** Apache-2.0

## The one-sentence product

**Sigantry** is an Apache-2.0 open-source **governance, audit, and rollback layer on top of Microsoft's official Fabric tooling**: version-controlled artefacts, traceable releases tied to work items, test-gated promotion across dev → preprod → prod, drift detection, and an immutable audit record — on Azure DevOps or GitHub, in under 15 minutes of setup.

It is deliberately NOT "a Fabric automation toolkit". Automation primitives (item deploy, CRUD, one-command publish) are owned by Microsoft's officially supported stack — `fabric-cicd`, the `fab` CLI, the Terraform provider. Sigantry wraps that stack and adds the layer none of it provides: tamper-evident deploy ledgers, rollback to a prior release, scheduled drift detection, destructive-op gating, and work-item traceability. The 2026-06-11 ecosystem survey ([LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md) §4) verified that no official or open-source tool offers any of them.

## Problem

Microsoft Fabric is an excellent data platform. The *delivery* surface around it is a mess:

- Deploying Fabric artefacts is bespoke to every tenant. `fabric-cicd` is the baseline, but every org layers parameter wrangling, workspace management, RBAC, sensitivity labels, and environment promotion on top of it in different ways.
- A Fabric change has no audit trail back to why it happened. A pipeline deploys a lakehouse; nobody can answer "which ticket authorised this, what tests passed, who approved the production promotion" without manually cross-referencing commit logs, ADO pipeline runs, and Teams messages.
- The test story between environments is "run a notebook after the deploy and hope". Smoke tests, integration tests, and gated approvals are not first-class stages.
- Rollback is "git revert and redeploy and cross fingers", which breaks when item `logicalId`s have drifted between releases.
- Drift between live Fabric state and the source-of-truth repo accumulates silently. Someone clicks a thing in the Fabric portal; two weeks later the repo and the workspace disagree.
- Team-level standards (branching strategy, PR review checklists, semantic-model diff visibility) have to be reinvented per team.

The symptoms show up in every Fabric shop of non-trivial size: release managers can't answer "what shipped when, authorised by whom, with what tests", and platform leads spend more time on glue than on the platform.

## Ideal customer profile

| Dimension | Profile |
|-----------|---------|
| Platform | Microsoft Fabric (any capacity class) |
| CI system | Azure DevOps, GitHub, or both |
| Team size | 3-50 data engineers + 1-5 platform engineers |
| Maturity | Beyond "single workspace with ad-hoc pipelines"; has multiple environments (dev/preprod/prod) or is about to |
| Sponsor | Head of data, platform lead, or principal data engineer with CI/CD pain |
| Tech posture | Comfortable installing OSS + editing YAML + running Python tooling; not a full-managed-vendor preference |
| Industry | Regulated sectors (finance, healthcare, public sector) tend to value the audit plane most; tech/SaaS tend to value the dual-CI parity |

**Why HS2 is customer #1:** HS2 is the reference implementation. Sigantry grew out of HS2's internal toolkit (v1.0 → v2.0.1) and HS2's feedback continues to shape the agnostic base.

**Why JToye Digital is customer #2:** JToye Digital validates the "works at any organisation" claim. Phase 16 delivers a `sigantry-jtoye` plugin and an acceptance test on a JToye tenant with JToye's own repo.

## The wedge — work-item traceability + deploy audit

Nothing off-the-shelf in the Microsoft Fabric ecosystem cleanly links a Fabric deployment back to an ADO work item or a GitHub issue with an immutable audit record. (Re-verified 2026-06-11 against the official and open-source landscape — [LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md) §4 found no incumbent for the audit ledger, rollback, drift detection, or the TMDL PR-bot.)

Sigantry's wedge is a stable `WorkItemProvider` protocol seam (shipping with ADO and GitHub implementations) plus a `DeployRecord` schema persisted to a non-pluggable audit plane, plus a `sigantry release record` CLI that writes a structured comment back to each linked work item.

A release manager opens work item `AB#1234`; they see a comment with: `workspace, release_id, fabric_items_changed[], test_evidence, approver, audit_hash`. They click the audit hash; they see the immutable audit log entry. They can diff against the previous release. They can roll back.

This is the single capability that makes Sigantry worth adopting over "ADO + fabric-cicd + glue". Everything else is supporting infrastructure.

## Why we win

1. **First-party-feeling, not vendor-locked.** Sigantry wraps Microsoft and community tools (`fabric-cicd`, `ms-fabric-cli`, `msfabricpysdkcore`, `semantic-link-labs`). It does not reimplement Fabric; it operationalises it. Customers keep their Microsoft investment. This bet compounded in March 2026 when Microsoft made `fabric-cicd` officially supported and embedded it in the `fab` CLI — Sigantry's deploy path inherited an SLA-backed engine while hand-rolled alternatives inherited a maintenance liability (see [LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md) §2.1).
2. **Dual-CI parity from day 1.** GitHub Actions and Azure DevOps templates ship in lockstep. Every feature lands in both or blocks the merge. The addressable market is split across both; one-CI products lose half of it.
3. **Pluggable seams, non-pluggable audit.** Six existing seams (Deploy, DQ Gate, Telemetry, Auth, Runbook Registry, Capacity Policy) plus five new v3 seams (WorkItem, Notification, SecretStore, ApprovalGate, PrReviewBot) let an org override without forking. The audit plane is deliberately *non-*pluggable so a misconfigured sink cannot suppress audit evidence.
4. **Built on a 124-REQ, 9-phase delivered foundation.** Sigantry didn't start from scratch. The v1.0 + v2.0 + v2.0.1 lineage shipped a tested, 1000+ pytest-passing, governance-aware base with 6 SemVer-committed seams before productisation began.
5. **Apache-2.0 with patent grant.** A toolkit that integrates multiple vendor APIs needs an explicit patent grant. MIT isn't sufficient for enterprise legal review. See [ADR-0010](decisions/ADR-0010-commercial-model.md).

## Personas — who adopts, who champions, who blocks

All three of these are involved in the buying decision. The brief addresses each explicitly.

### Platform lead (owns Fabric tenant + ADO org)

- **Top pain:** "My team keeps reinventing the deploy + audit pipeline per workspace. I can't prove compliance posture without manual archaeology."
- **Top Sigantry capability:** The non-pluggable audit plane plus the immutable `DeployRecord` written for every deploy. One query returns "what shipped to prod last quarter, authorised by which work item, with what test evidence".
- **Adoption metric:** **% of prod Fabric deploys with a linked work item and audit record.** Target: ≥95% within 60 days of adoption.

### Head of data (owns the data org, budget holder)

- **Top pain:** "Data releases feel slower and less safe than our software releases. Incidents take hours to triage because nobody can tell what changed when."
- **Top Sigantry capability:** The `deploy → smoke → integration → approval → promote` pipeline template pair with rollback-by-release-id. Incidents become "roll back to release X" instead of "find the SHA, cherry-pick, pray".
- **Adoption metric:** **Mean time to rollback (MTTR-rollback).** Target: under 5 minutes from decision to production-state-restored.

### Data engineer (writes the pipelines + notebooks)

- **Top pain:** "I spend half my week on YAML plumbing and PR-review archaeology, not on actual data work. TMDL diffs are invisible. Schema changes are caught by downstream breakage, not by review."
- **Top Sigantry capability:** The `sigantry-starter` template repo plus the PR-review bot. Starter gives you a working dev/preprod/prod pipeline in under 15 minutes. The bot posts TMDL + Lakehouse schema diffs automatically on every PR.
- **Adoption metric:** **Time-to-first-deploy on a fresh laptop.** Target: under 15 minutes, validated by an outside reviewer.

## Roadmap — v3.0 (7 phases)

| Phase | Theme | REQs | Ships |
|-------|-------|------|-------|
| 10 | Product brief + architecture refresh (this phase) | BRIEF-01..06 | This document, ADR-0010, ADR-0011, seam-map, dual-CI strategy, codebase rename |
| 11 | Work-item traceability wedge | TRACE-01..08 | `WorkItemProvider` seam, ADO + GitHub impls, `DeployRecord`, `sigantry release record` CLI |
| 12 | Pipeline test orchestration + rollback | PIPELINE-01..05 | ADO + GHA 5-stage template pair, deploy ledger, `sigantry deploy --rollback` |
| 13 | Drift detection | DRIFT-01..03 | `sigantry diff -e <env>`, scheduled drift pipelines (ADO + GHA); see [ADR-0012](decisions/ADR-0012-sync-apply-vs-deploy-run-boundary.md) for the apply-vs-deploy-run boundary surfaced by the 2026-05-01 brownfield test (PR #67). |
| 14 | Starter repo + PR-review bot | STARTER-01..07 | `sigantry-starter`, TMDL + Lakehouse diff bot (dual-CI), branching + PR-review docs |
| 15 | Public demo environment | DEMO-01..04 | Public `demo-sigantry` repos, demo Fabric tenant, Remotion-recorded walkthrough |
| 16 | Seam expansion + JToye trial | SEAM-01..06 | `NotificationSink`, `SecretStore`, `ApprovalGate` seams + `sigantry-jtoye` plugin + JToye acceptance test |

**v3.1+ deferred:** managed/hosted control plane (SaaS), non-Microsoft data platforms, per-seat licensing, customer SSO, localisation, backwards-compat shim lifetime > one minor.

## Demo

Sigantry's public demo lives at `<DEMO-URL>` (placeholder -- the
operator updates this URL after the public-mirror exercise per
[`15-HUMAN-UAT.md` Test 1](../.planning/milestones/v3.0-phases/15-public-demo-environment/15-HUMAN-UAT.md);
the trademark / domain / PyPI clearance gate at Test 0 may defer
publication until a v3.1 rename if a conflict surfaces).

The demo IS Sigantry dogfooded in public:

- A public `demo-sigantry` GitHub repo + ADO project (mirrored from
  this monorepo's `templates/demo/`).
- A dedicated demo Fabric tenant populated with sample lakehouse +
  notebook + data-pipeline + semantic-model items.
- Demo CI runs `sigantry deploy` + `sigantry release record` +
  `sigantry diff --fail-on-drift` on every push -- the same
  three-command loop the wedge promises adopters.
- A 90-second mp4 walkthrough produced reproducibly by
  `scripts/remotion/`:
  [Watch the 90-second walkthrough](https://github.com/sigantry/demo-sigantry/releases/latest)
  (the link resolves once the operator publishes the first release per
  `<DEMO-URL>` Test 3 -- see also `docs/demo/walkthrough-script.md`
  for the on-page narrative).

### Try it yourself

See [docs/demo/QUICKSTART.md](demo/QUICKSTART.md) -- 15 minutes from
a fresh laptop to a green deploy + audit + diff against the demo
tenant.

### Operator runbook

See [docs/runbooks/demo-tenant-operator.md](runbooks/demo-tenant-operator.md)
for tenant provisioning, secret rotation, the Lakehouse Git
limitation, dedicated-SPN scope discipline, and mp4 re-recording
cadence.

## Pricing + support sketch (forward-looking, not binding)

Sigantry itself is **Apache-2.0 forever**. Commercial levers layered on top in v3.1+ (if at all) are candidates for future consideration, not v3.0 scope:

- **Support subscription.** Prioritised bug triage, private security-advisory channel, and a response-time SLA for named customers.
- **Hosted control plane.** A managed audit-plane + deploy-ledger UI for orgs that don't want to self-host the observation stack.
- **Premium plugins** (*possible, not decided*). Enterprise seams — e.g., lineage to an enterprise catalog, HSM-backed key management — stay out of the OSS base and ship as commercial plugins. This decision is explicitly deferred and would require revisiting [ADR-0010](decisions/ADR-0010-commercial-model.md).

None of the above is committed or funded. It's listed only so the v3.0 architecture does not paint itself into a corner for a future commercial offering.

## What Sigantry is not

- **Not a Fabric replacement.** Sigantry does not reimplement lakehouses, Spark, or Power BI.
- **Not a cross-cloud abstraction.** Fabric-only. A Snowflake or Databricks integration would be a separate product.
- **Not a catalog.** Lineage integrates with Purview; Sigantry does not store lineage itself.
- **Not a security/policy DSL.** `CapacityPolicy` and `ApprovalGate` are seams where policy lives; Sigantry does not ship a policy language.
- **Not a managed service** (in v3.0).

## Why this brief exists in Phase 10

Phase 10 is the "don't write code yet" phase. The rename, the commercial-model ADR, the dual-CI strategy doc, and the expanded seam map all need to land *before* the Phase 11 work-item-traceability wedge gets named, shaped, or tested. Lock the story on paper, then build.

---

**See also:**
- [ADR-0010 — Commercial Model (Apache-2.0 pure OSS)](decisions/ADR-0010-commercial-model.md)
- [ADR-0011 — Rename to Sigantry](decisions/ADR-0011-rename-to-sigantry.md)
- [Seam Map — 6 existing + 5 planned v3 seams](reference/seam-map.md)
- [Dual-CI Strategy — GitHub Actions + Azure DevOps parity rule](reference/dual-ci-strategy.md)
- [Protocol Reference](reference/protocols.md)

*Last updated: 2026-04-24 (milestone v3.0 Phase 10, BRIEF-01 + BRIEF-03)*
