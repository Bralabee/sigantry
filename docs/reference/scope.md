# Scope — what the toolkit actually delivers (v3, clean rewrite)

**Verified against:** `master @ 4d8c665` on 2026-04-24, and cross-checked against `docs/reference/architecture.md`. Seam counts + §7 positioning updated `feat/plugin-config-parity @ be0d6c2` on 2026-06-17.
**Supersedes:** all prior drafts of this file. If earlier text is cited anywhere, treat it as void.

Every claim below carries a confidence label:

- **VERIFIED** — a file read, grep, or command output backs it. A citation is given.
- **ASSUMPTION** — reasoned from evidence but not directly checked. Caller should confirm before acting.
- **OPEN** — a strategic decision, not a fact. No answer in code.

If you see an unlabelled claim, I missed a label — flag it.

---

## 1. What the toolkit IS, today

### 1.1 Fabric control-plane primitives  — **VERIFIED**

- Eleven protocol seams: the six v2 seams `DeployProfile`, `DataQualityGate`, `TelemetrySink`, `AuthProvider`, `RunbookRegistry`, `CapacityPolicy`, plus the five v3 seams `WorkItemProvider`, `PrReviewBot`, `NotificationSink`, `SecretStore`, `ApprovalGate`. `sigantry_core/protocols.py`; reference impls ship in core (`workitems/`, `notifications/`, `secrets/`, `approval_gates/`, `pr_bot/providers/`). Full catalogue in [`seams.md`](seams.md).
- Fabric REST workspace CRUD + items + folders + capacity assignment. `sigantry_core/workspace/` (files: `core.py`, `items.py`, `folders.py`, `capacity.py`, `cli.py`).
- Fabric REST client package enforcing CLIENT-01 (only client/** may import httpx). `sigantry_core/client/` with `FabricRestClient`, `FabricArmRestClient`, `PowerBiRestClient`, `PurviewRestClient` (see §2.1 caveat on Purview).
- Deploy orchestration via `fabric-cicd` 1.x with 5 scope filters (`item_name_exclude_regex`, `folder_path_exclude_regex`, `folder_path_to_include`, `items_to_include`, `shortcut_exclude_regex`). `sigantry_core/deploy/core.py` (+ PR #25 merged 2026-04-23).
- Fabric Environment wheel sync. 3-step REST: `POST /staging/libraries`, `POST /staging/publish`, `GET /staging/libraries` verify. `sigantry_core/deploy/environment.py:75-141`.
- Post-sync folder reconciler. `plan_reconcile` + `apply_reconcile` with optional orphan cleanup. `sigantry_core/workspace/reconciler.py`.
- Destructive-op audit decorator. Enforces `force=True` and (for capacity pause/resume only) `runbook_id`. Emits stdlib `logger.info` — not `TelemetrySink`. `sigantry_core/governance/audit.py:53-100`.
- Top-level Typer CLI with 12 subapps. `sigantry_core/cli.py` (`grep -c "app.add_typer" == 12`).

### 1.2 Plugin infrastructure  — **VERIFIED**

- Entry-point-based registry with 11 plugin groups (canonical `sigantry.<seam>`); first-wins on duplicates; per-plugin import error captured on `PluginInfo`, not raised. `sigantry_core/registry.py`.
- TOML + `FDT_` env-var config loader. `sigantry_core/config.py`.
- `FabricDataOps` front door with 3 behaviour methods (`deploy`, `run_dq_gate`, `emit`). Supports direct-DI and `from_config(...)`. `sigantry_core/api.py`.
- `doctor` CLI surfaces registered plugins and import failures. `sigantry_core/doctor.py`.
- pytest11 entry point autoregisters contract fixtures on install. `pyproject.toml [project.entry-points.pytest11]`.
- In-memory test doubles. `sigantry_core/testing/doubles.py`, `sigantry_core/testing/fixtures.py`.

### 1.3 HS2 reference plugin — **VERIFIED**

`sigantry-hs2` v3.0 (formerly `fabric-dataops-toolkits-hs2` v1.0.0; renamed in v3.0 per ADR-0011) registers one class per seam:

- `AimsDeployProfile` (deploy_profiles: `aims`) — `sigantry-hs2/sigantry_hs2/deploy/aims_profile.py`
- `DqFrameworkGate` (dq_gates: `dq_framework`) — lazy-imports `dq_framework`, only usable inside Fabric notebook runtime (wraps `mssparkutils`). Local pytest cannot exercise it.
- `LogAnalyticsSink` (telemetry_sinks: `log_analytics`) — Azure Monitor DCR/DCE; HS2 stream names owned here.
- `Hs2EntraGroupAuth` / `Hs2TeamsRunbookRegistry` / `Hs2CapacityPolicy` (last one is a read-only skip by default unless caller injects `plan_fn`/`apply_fn`).
- Livecheck CLI `sigantry-hs2-livecheck`.
- Live-testing guide at `sigantry-hs2/docs/live-testing.md`.

### 1.4 Observability wiring — **VERIFIED**

- 6 bicep modules in `bicep/modules/`: `log-analytics.bicep`, `data-collection.bicep`, `dcr-telemetry.bicep`, `action-group.bicep`, `alerts.bicep`, `teams-logic-app.bicep` (+ `teams-logic-app-definition.json`).
- HS2 defaults in plugin bicep param file. `sigantry-hs2/bicep/hs2-defaults.bicepparam` (per CHANGELOG).

### 1.5 PowerShell surface — **VERIFIED**

- `Sigantry/` generic module v3.0 (formerly `Fabric/` v1.0.0; renamed in v3.0 per ADR-0011) exports `Get-FabricToken`, `Get-FabricTenantSetting`. `Sigantry/Sigantry.psd1`.
- `SigantryHs2/` plugin v3.0 (formerly `Hs2Fabric/` v2.0.0) `RequiredModules = Sigantry`, re-exports as `Get-Hs2FabricToken` / `Get-Hs2FabricTenantSetting`. `SigantryHs2/SigantryHs2.psd1`.
- pwsh 7.4 only (Core).

### 1.6 ADO pipeline templates — **VERIFIED**

Files under `templates/`:

- `stages/`: `ci.yml`, `cd-dev.yml`, `cd-test.yml`, `cd-prod.yml`, `approval-gate.yml`, `validate-fabric-items.yml`.
- `steps/`: `fabric-deploy.yml`, `fabric-validate.yml`, `fabric-vl-apply.yml`, `fabric-git-commit.yml`, `post-pr-comment.yml`.
- `jobs/`, `environments/`, `extends/`, `parameters.example.yml`.

No `preprod` stage. No ADO work-item validation step. No ADO branch-policy step.

---

## 2. What the toolkit IS NOT

### 2.1 Placeholders mistaken for features — **VERIFIED**

- `sigantry_core/purview/__init__.py` — literal contents: `"""sigantry_core.purview - placeholder. Populated in a later phase."""`. No implementation. The HTTP stub in `sigantry_core/client/purview.py` is the only real code and its own docstring records Purview as "v2 deferred" (PURVIEW-01..03 per REQUIREMENTS).
- `sigantry_core/pipelines/__init__.py` — literal contents: `"""sigantry_core.pipelines - placeholder. Populated in a later phase."""`. No implementation.
- **Correction owed to `architecture.md`:** its §"Base subpackages" currently lists "purview/ Purview scan/lineage wrappers" and "pipelines/ Fabric DataPipeline helpers". Both claims are wrong. To be fixed next time `architecture.md` is edited.

### 2.2 Absent capabilities (no code exists) — **VERIFIED** by grep returning zero hits

| Concern | Evidence of absence |
|---|---|
| Work-item traceability (ADO Boards, Jira, GitHub Issues) | No `WorkItemTracker` protocol, no ADO Boards module, no commit-trailer validator. `grep -rn "WorkItemTracker\|work_item" sigantry_core/` returns nothing. |
| Data lineage (OpenLineage or similar) | No `LineageEmitter` protocol. `grep -rn "LineageEmitter\|lineage_emitter" sigantry_core/` returns nothing. |
| Data contracts / schema registry | No seam; `DataQualityGate` is a runner, not a contract store. |
| Orchestration push (Airflow, Prefect, Dagster) | No seam; `pipelines/` is a placeholder. |
| Cross-cloud cost / FinOps | `CapacityPolicy` is Fabric-capacity-shaped only. |
| Drift / SLA / freshness monitoring | Not modelled. |
| ADO branch-policy declarative apply | No CLI subapp for ADO policy; `grep "ado" sigantry_core/cli.py` returns nothing. |
| `cd-preprod.yml` stage | Not in `templates/stages/` (verified by `ls`). |
| Non-Fabric deploy targets | `DeployProfile` is abstract; concrete `deploy/core.py` uses `fabric-cicd` and `client/fabric.py`. A Databricks or Snowflake consumer adds a new client + profile; it does not extend what's there. **ASSUMPTION** that such a consumer would fork heavily — never attempted. |

### 2.3 Scope boundaries declared elsewhere — **VERIFIED**

`CLAUDE.md` §"Out of Scope" explicitly excludes: Fabric replacement, custom lineage engine, DQ rule redefinition, Power BI user authoring, cross-cloud abstraction, on-prem migration, Python DSL over Fabric APIs, web portal, hand-rolled Git sync, real-time preventive policy, custom auth broker, auto-remediating control plane. Those are project-level decisions, not accidental gaps.

---

## 3. Is the coupling to DQ and AIMS architectural? — **VERIFIED: No.**

- Base package `sigantry_core/` contains zero HS2/AIMS/DQ strings. Enforced by `tests/prereqs/test_phase8_banned_apis.py`.
- Plugin is a sibling wheel registered via 6 entry points in `sigantry-hs2/pyproject.toml [project.entry-points."sigantry.*"]`. The parallel `[project.entry-points."fabric_dataops_toolkits.*"]` shim tables were dropped from first-party packages in v3.1 per ADR-0011; the core registry still dual-reads those six legacy groups for third-party plugins (removal tracked as V3.X-ROADMAP LEGACY-SURFACE-DROP). Uninstalling the plugin leaves the base functional; the base never imports plugin code.
- The perception of coupling comes from narrative in `CLAUDE.md`, `README.md`, `PROJECT.md`, `HANDOFF.md` — which lean on DQ + AIMS as worked examples. That is a documentation choice, not an architecture fact.

**Action recommended (not decided):** next docs pass, reframe DQ + AIMS as "consumer projects #1 and #2", list other consumers (this toolkit, Praveen's case) as peers. Docs only; zero code changes. **OPEN** — awaiting go-ahead.

---

## 4. Praveen's three requirements, mapped to code

### R1. CI/CD for Fabric artefacts across dev / preprod / prod with test gates — **Partial**

- Dev and prod stages ship as `templates/stages/cd-dev.yml` and `cd-prod.yml` — **VERIFIED**.
- Intermediate stage exists as `cd-test.yml` — **VERIFIED**. Whether "test" and "preprod" are the same conceptual environment under different names, or genuinely two distinct environments (test → preprod → prod), is an **OPEN** consumer-side decision. If the same: Praveen's R1 is covered with a rename. If distinct: a new `cd-preprod.yml` is new work.
- Pre-deploy artefact validation: `templates/stages/validate-fabric-items.yml` + `templates/steps/fabric-validate.yml` — **VERIFIED**.
- Human approval gate: `templates/stages/approval-gate.yml` — **VERIFIED**.
- DQ gate inline in a pipeline: **NOT available**. `DqFrameworkGate` runs only inside the Fabric notebook runtime (`mssparkutils` at module scope, per `sigantry-hs2/sigantry_hs2/dq/dq_framework_gate.py`). A notebook-deployed check exists at `sigantry-hs2/notebooks/dq_gate_livecheck.py`. "DQ must pass before promote" is possible via a notebook run + success marker; it is not a pipeline-inline step.

### R2. Work-item traceability linking Fabric changes to ADO work items — **Gap**

- No code for ADO Boards integration, no `WorkItemTracker` seam, no commit-trailer / PR-body validator — **VERIFIED** by grep (§2.2).
- Closest existing concept: `runbook_id` kwarg on `destructive_op` — incident IDs, not work items. Re-using that field for work items would conflate two concerns. Don't.

### R3. Standardised workflows — branching, PR reviews, release approvals — **Partial**

- Pipeline-side: approval gates shipped (`approval-gate.yml`) — **VERIFIED**.
- Branch-policy side: no toolkit code or template for declarative ADO branch policy (required reviewers, build validation, work-item-link requirement, min-approvers) — **VERIFIED** by grep.
- Branching strategy (trunk-based, gitflow, etc.) is a repo-policy choice the toolkit does not enforce today and — **OPEN** — arguably should not (it is consumer-specific).

---

## 5. Fit for genuinely different future use cases — **ASSUMPTION**

The seam set (six v2 seams chosen against v1 requirements in Phases 0–7 and productized in Phase 8; five v3 seams added in Phases 11/14/16) gives the reasoned fit:

- Consumer swapping existing seam implementations (e.g. Datadog `TelemetrySink`) — fits.
- Consumer adding configuration to an existing seam via `from_settings(cfg)` — fits.
- Concerns that don't map to any existing seam (work-item tracking, lineage, orchestration, contracts) — **require new seams**. Each new seam is public API; each bumps the library's minor version; each needs its own contract-test harness.
- Non-Fabric engines (Databricks, Snowflake) — **do not fit without forking**. `client/`, `workspace/`, `deploy/core.py` are Fabric REST end-to-end.

Confidence is ASSUMPTION not VERIFIED because "fit" is a design judgement that can only be tested by trying. Track record so far (Phase 8 refactor) suggests the seam set is sound for Fabric-shaped problems.

---

## 6. Strategic decision not yet made — **OPEN**

Is HS2's ambition:

- (A) a **Fabric programmatic toolkit** — current code, current seams, extend conservatively; or
- (B) a **DataOps platform** where Fabric is one adapter among several — requires seam review before any new seam lands, and a longer roadmap including lineage / orchestration / contracts?

Neither answer is in the code. Both are defensible. The answer decides which seams get added next and which do not. **Do not commit to a Phase 10 design until this is settled.**

---

## 7. Positioning vs Microsoft native tooling — **VERIFIED 2026-06-17**

The authoritative ecosystem survey is [`../LANDSCAPE-2026-06.md`](../LANDSCAPE-2026-06.md);
the product framing is [`../PRODUCT-BRIEF.md`](../PRODUCT-BRIEF.md). This section records the
one-paragraph verdict and a 2026-06-17 re-verification of the three deploy-mechanics deltas.

**Verdict.** The toolkit's deploy engine *wraps* Microsoft's official `fabric-cicd`
(`pyproject.toml`: `fabric-cicd>=1.0,<2.0`; `sync apply` calls `fabric_cicd.publish_all_items`).
It is **not** a Fabric automation toolkit — that lane is now owned by GA, SLA-backed Microsoft
tooling (`fabric-cicd`, the `fab` CLI, the Terraform provider). The defensible identity is the
**governance, audit, and traceability layer on top of official tooling**. Concretely, most
capability is one of three tiers (per LANDSCAPE §1): commodity wrapped-convenience (correct
engineering, zero standalone value), uncontested original work (drift detection, deploy
rollback, headless TMDL PR-bot, integrity-checked audit ledger), and a thin under-erosion middle.

**Re-verification of the three "deltas" often cited as why this exists (2026-06-17, primary sources):**

| Delta | Microsoft coverage now | Residual value |
|---|---|---|
| Notebook env/lakehouse re-binding | `fabric-cicd` parameterization + deployment-pipeline default-lakehouse rules + Git auto-binding **cover it** (same-workspace; currently buggy — [fabric-cicd #311](https://github.com/microsoft/fabric-cicd/issues/311) leaves stale lakehouse entries) | Cross-workspace + REST-path correctness edge **today**; not durable |
| Environment custom-library (wheel) management | Environment **Git integration versions custom libraries** (`Libraries/CustomLibraries`, add/delete files) + deployment pipelines deploy them | Only the **REST/feed-wheel** model (a workflow choice) needs the reconcile verb |
| Deploy audit | **Purview audits all activities incl. REST ops**; deployment-pipeline history exists | Differentiates **only** on integrity-checked, deploy-scoped, tooling-owned provenance |

**Decision (positioning, not the §6 multi-engine axis).** Keep the toolkit, narrow its identity
to governance + audit + the uncontested originals, and **delegate deploy mechanics to
`fabric-cicd`** rather than maintaining parallel binding/env-library code. The single input that
could flip this to "shrink toward native" is whether a **tamper-evident provenance ledger is an
actual compliance requirement** vs Purview being sufficient (note: the shipped ledger is
integrity-checked but unkeyed and unanchored, so it does not yet meet a tamper-evidence
requirement on its own — see
[audit ledger threat model](audit-ledger-threat-model.md)) — a question for the compliance
owner, not the code.

---

## 8. Change protocol for this doc

1. If the code contradicts a claim here, **the code wins**. Update this doc; do not silently weaken.
2. When a claim's label changes (OPEN → VERIFIED, ASSUMPTION → VERIFIED) record the new evidence inline.
3. Bump the `master @ <sha>` stamp at the top on every substantive edit.
4. If a claim cannot be cited against file:line or a command output, either verify it, demote it to **ASSUMPTION**, or remove it.
