# Architecture & Process Reference (v3)

**Status:** Code-verified as of 2026-04-24 against `master` @ `4d8c665`. Last patched 2026-04-24 to correct `purview/` and `pipelines/` subpackage descriptions (both are placeholders — see `docs/reference/scope.md` §2.1).
**Freshness (2026-08-13):** the artefact versions in §1 were re-measured and corrected. Everything else on this page — in particular the §4 validation table — still records what was checked on 2026-04-24 and has NOT been re-verified since; §4 row 20 (test counts) is known to be superseded. Treat §4 as a dated record of method, not as current numbers.
**Source of truth:** the repo itself. When this doc and code disagree, the code wins — update this doc.
**Supersedes:** ad-hoc diagrams in prior handoff threads.

This document is the authoritative architecture + process reference for the HS2 DataOps + Fabric programmatic toolkit. Every claim below has been validated against a file read, a grep, or a `pytest --collect-only` run. The validation table at the end records exactly how each claim was verified — read that before challenging any statement here.

If you're about to propose a change, extend a seam, or ingest a new requirement, start here. Do not re-probe "unknown" questions that have already been answered below; the validation notes record what was actually checked.

---

## 1. System boundaries

Three Python packages + two PowerShell modules ship from this monorepo:

| Artefact | Path | Version | Role |
|---|---|---|---|
| `sigantry-core` | `sigantry_core/` | 3.4.0 | Agnostic base. 11+ protocol seams (deploy profile, DQ gate, telemetry sink, auth provider, runbook registry, capacity policy, work-item provider, notification sink, secret store, approval gate, PR-review bot), registry, config, dispatchers, HTTP client, CLI, governance audit (now including `emit_deploy_record`), testing doubles. |
| `sigantry-hs2` | `sigantry-hs2/` | 3.2.1 | HS2 plugin (formerly `fabric-dataops-toolkits-hs2` v1.0.0). Six entry-point registrations, one per seam, plus a livecheck CLI, Bicep defaults, and HS2 docs. |
| `sigantry-jtoye` | `sigantry-jtoye/` | 3.2.1 | Second-customer reference plugin (Phase 16). Registers a notification sink + work-item provider. |
| `Sigantry` (pwsh) | `Sigantry/` | 3.0.0 | Generic PowerShell helpers (formerly `Fabric/` v1.0.0): `Get-FabricToken`, `Get-FabricTenantSetting`. |
| `SigantryHs2` (pwsh) | `SigantryHs2/` | 3.0.0 | HS2 plugin module (formerly `Hs2Fabric/` v2.0.0). `RequiredModules=Sigantry`; re-exports as `Get-Hs2Fabric*` with HS2 defaults. |

Infra-as-code shipped but deployed externally: `bicep/` (Log Analytics, DCR, DCE, Action Groups, Teams Logic App, alerts), `templates/` (ADO YAML — environments, extends, jobs, stages, steps).

Dependency direction is **one-way**: consumer repos depend on `sigantry_core` and optionally the HS2 plugin; never the reverse. Plugins depend on base; base never imports plugin code. Enforced by the banned-api grep gate in `tests/prereqs/test_phase8_banned_apis.py`.

---

## 2. Architecture diagram (v3 — corrections applied inline)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                             CONSUMER / OPERATOR SURFACE                              │
│   ADO pipelines (templates/)   │   Python `from sigantry_core import …`    │
│   Bash: `fabric-dataops …`     │   Bash: `sigantry-hs2-livecheck`                    │
│   PowerShell 7.4: `Get-Hs2Fabric*`                                                   │
└──────────┬─────────────────────────────────────────────────────────────┬─────────────┘
           │                                                             │
  ┌────────┴───────────────────┐                          ┌──────────────┴─────────────┐
  │  Python API (api.py)       │                          │  Typer CLI (cli.py)        │
  │    FabricDataOps           │  ◀── independent ──▶     │  12 subapps. Most          │
  │      .from_config()        │     peer surfaces        │  invoke v1 subpackages     │
  │      .deploy / run_dq_gate │                          │  DIRECTLY and do NOT       │
  │      / emit / close        │                          │  construct FabricDataOps.  │
  │    direct-DI or config     │                          │  `deploy` / `dq` hit       │
  │                            │                          │  dispatchers. `doctor`     │
  │    3 behaviour methods     │                          │  reads default_registry(). │
  │    3 logical dispatchers   │                          │                            │
  │    across 4 modules        │                          │  Subapps (in cli.py order):│
  │    (monitor has emit.py +  │                          │   workspace, capacity,     │
  │     dispatcher.py)         │                          │   label-sync, rbac-audit,  │
  └─────┬──────────────────────┘                          │   tenant-settings, deploy, │
        │                                                 │   fabric-item, git,        │
        │                                                 │   variable-library, env,   │
        │                                                 │   dq, doctor.              │
        │                                                 └──────────┬─────────────────┘
        │                                                            │
        │                                                            │ (bypasses seams
        │                                                            │  for workspace,
        │                                                            │  capacity, git,
        │                                                            │  env, etc.)
        ▼                                                            ▼
  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │  6 PROTOCOL SEAMS (protocols.py) — SemVer-committed plugin surface               │
  │                                                                                  │
  │  ACTIVELY dispatched:  DeployProfile · DataQualityGate · TelemetrySink           │
  │  UNDER-EXPOSED on API: AuthProvider  · RunbookRegistry · CapacityPolicy          │
  │                        (reachable via FabricDataOps attribute or v1 subpkg only) │
  │                                                                                  │
  │  + optional Closeable Protocol (runtime_checkable; duck-typed via isinstance)    │
  │                                                                                  │
  │  Value objects (frozen slotted):                                                 │
  │   DeployContext/Plan/Result · DataRef · GateResult · TelemetryEvent ·            │
  │   Secret (scrubbed __repr__/__str__) · CapacityContext/Action/ApplyResult        │
  └───┬──────────────────────────────────────────────────────────────────────┬───────┘
      │                                                                      │
      │  Registry.discover() via importlib.metadata.entry_points              │
      │  6 entry-point groups (see pyproject.toml):                           │
      │    sigantry_core.{deploy_profiles, dq_gates,                │
      │      telemetry_sinks, auth_providers, runbook_registries,             │
      │      capacity_policies}                                               │
      │                                                                      │
      │  _GROUP_TO_TOML_KEY maps each group to a TOML section:                │
      │    deploy_profiles→deploy · dq_gates→dq · telemetry_sinks→telemetry  │
      │    auth_providers→auth · runbook_registries→runbooks ·                │
      │    capacity_policies→capacity                                         │
      │                                                                      │
      │  _resolve_optional instantiation order:                               │
      │    1. Non-class (pre-built instance or factory) → return as-is        │
      │    2. impl.from_settings(cfg_dict) if defined                         │
      │    3. impl(**cfg) if [<seam>.<name>] TOML section is non-empty        │
      │    4. impl() zero-arg fallback                                        │
      │                                                                      │
      │  Entry-point import failures are RECORDED on PluginInfo.import_error │
      │  (first-wins on duplicates); doctor CLI surfaces them.                │
      ▼                                                                      │
  ┌──────────────────────────────────────────────────────────────────────────────┐  │
  │  HS2 PLUGIN (fabric-dataops-toolkits-hs2 v1.0.0 — first release)             │  │
  │                                                                              │  │
  │  aims           → AimsDeployProfile         (plan → [sync_wheel,             │  │
  │                                               deploy_workspace]; apply       │  │
  │                                               runs both verbatim)            │  │
  │  dq_framework   → DqFrameworkGate           (optional [dq] extra; lazy       │  │
  │                                               imports dq_framework at call)  │  │
  │  log_analytics  → LogAnalyticsSink          (Azure Monitor DCR/DCE;          │  │
  │                                               Custom-Hs2Deploy/FabricCapacity│  │
  │                                               /SparkLog/AlertAudit streams)  │  │
  │  hs2_entra_group→ Hs2EntraGroupAuth         (DefaultAzureCredential chain)   │  │
  │  hs2_teams      → Hs2TeamsRunbookRegistry   (alert_name → Teams runbook URL) │  │
  │  hs2            → Hs2CapacityPolicy         (READ-ONLY SKIP DEFAULT; caller  │  │
  │                                               injects plan_fn/apply_fn for   │  │
  │                                               real policy)                   │  │
  │                                                                              │  │
  │  + livecheck CLI   `sigantry-hs2-livecheck`                                  │  │
  │  + Bicep defaults  `sigantry-hs2/bicep/hs2-defaults.bicepparam`              │  │
  │  + docs/live-testing.md  (HS2_FABRIC_TEST_* env var contract)                │  │
  └──────────┬───────────────────────────────────────────────────────────────┬───┘  │
             │                                                               │      │
             ▼                                                               ▼      │
  ┌──────────────────────────────────────────────────────────────────────────────┐  │
  │  BASE SUBPACKAGES (v1 surface) — used by v2 seams AND directly by CLI        │  │
  │                                                                              │  │
  │  client/        single httpx surface. CLIENT-01 invariant: only              │  │
  │                 sigantry_core/client/** + auth/diagnose.py may     │  │
  │                 import httpx. Enforced by test_package_structure + ruff      │  │
  │                 banned-api. Ships: FabricRestClient, FabricArmRestClient,    │  │
  │                 PowerBiRestClient, PurviewRestClient + LRO polling,          │  │
  │                 pagination, tenacity retry, correlation-id logging.          │  │
  │                                                                              │  │
  │  workspace/     core (CRUD) · items (list + folder_id) · folders (v2 new:    │  │
  │                 CRUD + move_item via POST /items/{id}/move — PATCH with      │  │
  │                 folderId is rejected 400 by Fabric) · reconciler (plan-      │  │
  │                 then-apply folder hierarchy sync with optional orphan        │  │
  │                 cleanup) · capacity · cli                                    │  │
  │                                                                              │  │
  │  deploy/        orchestrator (seam-backed dispatcher) · core (deploy_        │  │
  │                 workspace → fabric-cicd 1.x) · environment (sync_wheel —     │  │
  │                 direct 3-step: POST /staging/libraries, POST /staging/       │  │
  │                 publish LRO, GET /staging/libraries verify) · variable_      │  │
  │                 library · git_integration · parameters · item_copy ·        │  │
  │                 profiles/ (empty in base — HS2 plugin ships the only one)    │  │
  │                                                                              │  │
  │  dq/            dispatcher · cli                                             │  │
  │  monitor/       emit (user entry) · dispatcher (module-default sink) ·       │  │
  │                 set_default_sink                                             │  │
  │                                                                              │  │
  │  governance/    audit.destructive_op decorator.                              │  │
  │                 Enforces:                                                    │  │
  │                   - force=True mandatory (else DestructiveOpError)           │  │
  │                   - runbook_id mandatory for {(capacity,pause),              │  │
  │                     (capacity,resume)} only (else DestructiveOpError)        │  │
  │                 Emits:                                                       │  │
  │                   - stdlib logger.info ONLY (non-pluggable audit plane).     │  │
  │                     Does NOT flow through TelemetrySink.                     │  │
  │                 + rbac · labels · tenant_settings · principal_expansion      │  │
  │                                                                              │  │
  │  capacity/      cli (plan/apply capacity actions)                            │  │
  │  auth/          token provider chain + `diagnose-auth` CLI (CLIENT-01        │  │
  │                 exception — may import httpx)                                │  │
  │  purview/       PLACEHOLDER — __init__.py only. Real code deferred to v2.   │  │
  │                 The stub HTTP client lives at client/purview.py.             │  │
  │  pipelines/     PLACEHOLDER — __init__.py only. No implementation yet.       │  │
  │  testing/       in-memory doubles + pytest11 entry-point autoregistration    │  │
  │                 (contract fixtures auto-install on                           │  │
  │                  `pip install sigantry`)                           │  │
  │                                                                              │  │
  │  Cross-cutting invariants enforced by tests/prereqs/*:                       │  │
  │   • No HS2 strings outside fabric-dataops-toolkits-hs2/                      │  │
  │     (test_phase8_banned_apis)                                                │  │
  │   • CLIENT-01: httpx confined to client/** + auth/diagnose.py                │  │
  │     (tests/sigantry_core/client/test_package_structure.py)         │  │
  │   • _version.py ↔ CHANGELOG ↔ docs banner version alignment                  │  │
  │     (test_version_alignment)                                                 │  │
  │   • ADR structure / changelog format / wiki link validity / evidence schemas │  │
  └──────────┬───────────────────────────────────────────────────────────────┬───┘  │
             │                                                               │      │
             ▼                                                               ▼      │
  ┌──────────────────────────────────────────────────────────────────────────────┐  │
  │  EXTERNAL TARGETS                                                            │  │
  │                                                                              │  │
  │   Microsoft Fabric REST · Power BI REST · Azure ARM · Purview                │  │
  │   Azure Log Analytics DCE/DCR (via Azure Monitor Ingestion from plugin)      │  │
  │   Azure Key Vault · Entra ID (via azure-identity DefaultAzureCredential)     │  │
  │   Azure DevOps Git (portal-OAuth connections only — Fabric REST does not     │  │
  │                     accept PAT creds on /v1/connections; deferred track)     │  │
  └──────────────────────────────────────────────────────────────────────────────┘  │
                                                                                    │
  PS flow (parallel surface, not shown above):                                      │
    Import-Module Hs2Fabric                                                         │
      └─ RequiredModules Fabric 1.0.0                                               │
         └─ Get-FabricToken → Az.Accounts Get-AzAccessToken per audience            │
```

---

## 3. Process diagrams (v3 — corrections applied inline)

### 3.1 `FabricDataOps.from_config(...).deploy(ctx)` — full lifecycle

```
caller ── FabricDataOps.from_config(path)
              │
              ▼
     config.load_settings(path)
       ├─ tomllib.load(TOML)
       ├─ _apply_env_overrides(SIGANTRY_*__* env)  ← env wins over TOML
       └─ ToolkitSettings(**data)                  ← pydantic-settings v2 validate
              │
              ▼
     Registry (injected or default_registry())
       └─ .discover() (idempotent under lock)
            iter 6 entry-point groups
            ep.load() per plugin → impl
            import error → PluginInfo.import_error (not raised)
              │
              ▼
     _resolve_optional(reg, group, name, settings)  ← for each of 6 seams
       1. impl not a class                → return as-is
       2. impl.from_settings(cfg)         → if classmethod defined
       3. impl(**cfg)                     → if [<seam>.<name>] non-empty
       4. impl()                          → zero-arg fallback
              │
              ▼
     FabricDataOps(auth=…, telemetry=…, deploy_profile=AimsDeployProfile(), …)
              │
              ▼
     .deploy(ctx)  ──▶  deploy.orchestrator.deploy(
                           ctx,
                           profile=self.deploy_profile,      ← wins over name
                           profile_name=self.settings.deploy.profile,
                           registry=self.registry)
                            │
                            ▼
                      plan = profile.plan(ctx)
                       └─ AimsDeployProfile.plan:
                           reads ctx.parameters for
                             wheel_path, environment_id, items_directory,
                             parameters_path, environment_key, item_types,
                             expected_sha256
                           returns DeployPlan(actions=[
                             {kind: sync_wheel, …},
                             {kind: deploy_workspace, …}
                           ])
                            │
                            ▼
                      result = profile.apply(ctx, plan)
                       └─ AimsDeployProfile.apply:
                           1. _find_action(plan, "sync_wheel")
                           2. _find_action(plan, "deploy_workspace")
                           3. client = self._client or FabricRestClient.from_defaults()
                           4. sync_wheel_fn(client, ws, env, wheel_path,
                                            expected_sha256=…)
                               └─ POST /v1/workspaces/{ws}/environments/{env}
                                        /staging/libraries   (upload)
                               └─ POST …/staging/publish     (LRO)
                               └─ GET  …/staging/libraries   (verify)
                           5. deploy_workspace_fn(ws, repo_dir, env_key,
                                                  item_type_in_scope,
                                                  parameters_path,
                                                  token_provider=None)
                               └─ fabric-cicd 1.x drives item-tree publish
                                  (5 optional scope filters: item_name_exclude_regex,
                                   folder_path_exclude_regex, folder_path_to_include,
                                   items_to_include, shortcut_exclude_regex;
                                   2 of those also flow to orphan-unpublish)
                           6. build AimsWheelUploadSnapshot from wheel_result
                           7. return DeployResult(
                                workspace_id, items_published, items_failed,
                                raw={aims_wheel, orphans_unpublished, dot_graph_path}
                              )
                            │
                            ▼
                     NOTE: AimsDeployProfile.apply does NOT emit telemetry
                           and does NOT call reconcile_folders_from_repo.
                           The caller (or a higher-level orchestrator) does
                           both of those if desired:
                             fdo.emit("deploy_completed", {...})
                             reconcile_folders_from_repo(client, ws, repo, …)
              │
              ▼ ( context-manager exit or explicit .close() )
     FabricDataOps.close()
       for seam in (auth, telemetry, dq_gate, deploy_profile, runbooks, capacity):
         if seam is None: continue
         if isinstance(seam, Closeable):   ← runtime_checkable duck-type
             seam.close()                  ← LogAnalyticsSink releases
                                             LogsIngestionClient
```

### 3.2 `FabricDataOps.run_dq_gate(suite, DataRef(…))`

```
caller ─▶ run_dq_gate(suite, data_ref, gate_name=None)
            │
            ▼
     dq.dispatcher.run_gate(suite, data_ref,
                            gate=self.dq_gate,                ← precedence
                            gate_name=self.settings.dq.gate,
                            registry=self.registry)
            │
            ▼
     DqFrameworkGate.run(suite, data_ref)
       ├─ lazy `from dq_framework.gate import run_checkpoint` (if [dq] installed)
       ├─ fallback: wrap FabricDataQualityRunner.validate_* (PR #21 fix)
       │  NOTE: FabricDataQualityRunner imports pyspark.sql.SparkSession
       │        + mssparkutils at module scope — not runnable from local pytest.
       │        Live path is `fabric-dataops-toolkits-hs2/notebooks/
       │        dq_gate_livecheck.py`. test_live_gate auto-skips without the
       │        Fabric notebook runtime.
       └─ _adapt_result(raw) → GateResult(
              suite, success, violations, evaluated, run_id
          )
```

### 3.3 `FabricDataOps.emit("deploy_started", {...})`

```
caller ─▶ emit(event_name, properties, strict=False)
            │
            ▼
     monitor.emit.emit_telemetry(
         name, props,
         sink=self.telemetry,    ← precedence: explicit > default > no-op
         strict=False)
            │
            ▼  if no sink resolvable → silent no-op (best-effort by design)
            │
            ▼
     LogAnalyticsSink.emit(TelemetryEvent(name, properties, timestamp))
       ├─ _to_row: TimeGenerated (ISO-8601 UTC, now() default)
       │          EventName, Properties,
       │          CorrelationId / Principal / Workspace convenience columns
       ├─ LogsIngestionClient.upload(
       │     rule_id=dcr_immutable_id,
       │     stream_name=Custom-Hs2{Deploy|FabricCapacity|SparkLog|AlertAudit},
       │     logs=[row])
       └─ on exception: log + swallow UNLESS strict=True (re-raise)
```

### 3.4 Destructive-op path — `delete_folder(..., force=True)` / `delete_item` / capacity pause/resume

```
caller ─▶ delete_folder(client, workspace_id, folder_id, *,
                         force=True,
                         principal="<appId-or-UPN>",
                         resource_id="<folder-id>")
            │
            ▼
     @destructive_op("folder", "delete")  wrapper
       1. assert kwargs["force"] is True           → else DestructiveOpError
       2. if (kind, action) in _REQUIRES_RUNBOOK:   (only {(capacity,pause),
             assert kwargs["runbook_id"]            (capacity,resume)} today)
                                                    → else DestructiveOpError
       3. call wrapped fn:
             DELETE /v1/workspaces/{id}/folders/{folder_id}
       4. logger.info("destructive_op", extra={
              event, resource_kind, action, resource_id, principal,
              force=True, ...
          })
          ↑ stdlib logging ONLY. Non-pluggable observation plane.
            Does NOT route through TelemetrySink.
```

### 3.5 Folder reconciler — `reconcile_folders_from_repo(...)`

```
reconcile_folders_from_repo(client, workspace_id, repo_dir,
                            apply=False, include_orphans=False,
                            unpublish_orphans=False, force=False,
                            runbook_id=None)
   │
   ├─ _scan_repo(repo_dir)             → list[RepoItem]  (walks .platform files)
   ├─ list_folders(client, ws_id)      → live folder tree
   └─ list_items(client, ws_id)        → live items (with folder_id)
   │
   ▼
plan_reconcile(repo_items, live_folders, live_items, include_orphans)
   returns ReconcilePlan(
     create_folders:   sorted parent-first (by path length)
     move_items:       dedicated POST /v1/workspaces/{id}/items/{id}/move
     unpublish_items:  items in workspace whose (displayName, type) is
                       absent from repo tree          (when include_orphans)
     delete_folders:   folders absent from repo,
                       sorted leaf-first for safe deletion
   )
   │
   ▼  apply=False → return ReconcileReport(plan_only=True)
   │
apply_reconcile(plan, apply=True, unpublish_orphans, force)
   1. create_folder(...) parent-first
   2. move_item(...)     (POST /items/{id}/move with targetFolderId)
   3. if unpublish_orphans and force:
        a. delete_item(client, ws, item_id, force=True, …)
           @destructive_op("item","delete") → logger.info audit
        b. delete_folder(... force=True …) leaf-first
           @destructive_op("folder","delete") → logger.info audit
      elif unpublish_orphans and not force:
        raise DestructiveOpError  (no mutation)
   4. Second run on clean tree ≡ no-op (idempotent)
```

### 3.6 CLI surface — `fabric-dataops workspace list ...`

```
shell ─▶ fabric-dataops workspace list --tenant-id <...>
            │
            ▼ (pyproject [project.scripts])
     sigantry_core.cli:app  (Typer, 12 subapps)
            │
            ▼  app.add_typer(workspace_app, name="workspace")
     workspace.cli.list_cmd
       ├─ FabricRestClient.from_defaults(tenant_id=…)
       │    └─ azure-identity DefaultAzureCredential chain
       ├─ paginate(GET /v1/workspaces [?roles=…])
       └─ rich.Table | json output

IMPORTANT: workspace / capacity / label-sync / rbac-audit / tenant-settings /
fabric-item / git / variable-library / env subapps bypass the seam layer
entirely — they call v1 subpackages directly. Only `deploy`, `dq`, and
`doctor` touch the dispatcher / registry layer.
```

### 3.7 PowerShell surface — `Get-Hs2FabricToken -Audience Fabric`

```
pwsh ─▶ Import-Module Hs2Fabric             (v2.0.0)
           │  RequiredModules = Fabric 1.0.0 → auto-loaded
           ▼
     Get-Hs2FabricToken -Audience Fabric
           │  (HS2 defaults applied: default tenant, default audience)
           ▼
     Get-FabricToken (generic cmdlet in Fabric module)
           ├─ Az.Accounts: Connect-AzAccount (cached or WIF)
           ├─ Get-AzAccessToken -ResourceUrl <audience-url>
           │   audiences: Fabric, PowerBI, Graph, Purview, AzureRM
           └─ return PSCustomObject { Token; ExpiresOn; Audience }
```

### 3.8 ADO pipeline surface

```
ADO stage ─▶ templates/stages/{ci,cd-dev,cd-test,cd-prod,
                               approval-gate,validate-fabric-items}.yml
              │
              ▼
     templates/jobs/{build-python,build-powershell,
                    lint-python,lint-powershell}.yml
              │
              ▼
     templates/steps/{fabric-deploy,fabric-validate,fabric-vl-apply,
                     fabric-git-commit,post-pr-comment}.yml
              │
              ├─ pip install sigantry (+ sigantry-hs2)
              ├─ sigantry-hs2-livecheck --load-env <file>
              │    env-var presence · DefaultAzureCredential · DCE reach ·
              │    sink schema
              ├─ fabric-dataops deploy / workspace / capacity / …
              └─ deploy-artefacts/ + telemetry → LogAnalyticsSink
```

---

## 4. Validation table

| # | Claim | How verified | Verdict |
|---|---|---|---|
| 1 | Six `@runtime_checkable` Protocols in `protocols.py` | File read — `DeployProfile`, `DataQualityGate`, `TelemetrySink`, `AuthProvider`, `RunbookRegistry`, `CapacityPolicy` + optional `Closeable` | Confirmed |
| 2 | Each HS2 plugin class implements its Protocol surface | grep: `AimsDeployProfile.plan/apply` · `DqFrameworkGate.run` · `LogAnalyticsSink.emit/flush/close` · `Hs2EntraGroupAuth.get_token` · `Hs2TeamsRunbookRegistry.resolve` · `Hs2CapacityPolicy.plan/apply`. Each sets `name: str = "<pyproject-ep-name>"` | Confirmed |
| 3 | Registry: six groups, lazy discover, import-error capture | Read `registry.py` — `_GROUPS` tuple has 6 entries; `discover()` holds `_lock` + `_discovered` flag (idempotent); per-EP exception recorded on `PluginInfo.import_error` (first-wins on duplicates) | Confirmed |
| 4 | `FabricDataOps` exposes only `deploy` / `run_dq_gate` / `emit` (+ `close`, `__enter__`, `__exit__`) | Read `api.py` — three behaviour methods + `from_config`. Auth / Runbooks / Capacity seams exist but aren't wrapped in behaviour methods | Confirmed (under-exposed) |
| 5 | 3 logical dispatchers across 4 modules | `find` → `monitor/dispatcher.py`, `monitor/emit.py`, `dq/dispatcher.py`, `deploy/orchestrator.py`. `monitor` has both `emit.py` (user entry) + `dispatcher.py` (default-sink registry) | Confirmed |
| 6 | `_GROUP_TO_TOML_KEY` maps 6 groups to 6 TOML sections | Read `api.py` — exactly: deploy_profiles→deploy · dq_gates→dq · telemetry_sinks→telemetry · auth_providers→auth · runbook_registries→runbooks · capacity_policies→capacity | Confirmed |
| 7 | `Closeable` is duck-typed via `isinstance(..., Closeable)` | `api.py:231: if isinstance(seam, Closeable):` — runtime_checkable protocol, no inheritance required | Confirmed |
| 8 | Typer CLI has 12 subapps, most bypass seams | `grep -c "app.add_typer" cli.py == 12`. `workspace/cli.py` imports directly from `workspace.core`/`.items`/`.capacity`, never constructs `FabricDataOps`. `deploy` / `dq` / `doctor` are the three that hit seams | Confirmed |
| 9 | `doctor` reads `default_registry().list_plugins()`, `--strict` exits 1 on import error | File read `doctor.py` | Confirmed |
| 10 | ADO templates: environments / extends / jobs / stages / steps | `find templates` — 1 env, 1 extends, 4 jobs, 6 stages, 5 steps, 1 parameters example | Confirmed |
| 11 | `destructive_op` writes via stdlib `logger.info`, not `TelemetrySink` | `audit.py:92: logger.info("destructive_op", extra={...})`. Docstring explicit: "Phase 2 correlated logger". No import of `monitor.emit` | Confirmed |
| 12 | `_REQUIRES_RUNBOOK = {(capacity, pause), (capacity, resume)}` | Read `audit.py` — frozenset with exactly those two tuples | Confirmed |
| 13 | `sync_wheel` 3-step flow: POST /staging/libraries → POST /staging/publish → GET /staging/libraries | Read `deploy/environment.py` — docstring lines 75–77 list three steps; code at lines 112/126/141 matches. Upload is direct multipart, NOT fabric-cicd | Confirmed (v2 had this partly wrong; v3 fixed) |
| 14 | `move_item` uses `POST /v1/workspaces/{id}/items/{itemId}/move` | `workspace/folders.py:114: client.send("POST", f"/v1/workspaces/{workspace_id}/items/{item_id}/move", json=body)` | Confirmed |
| 15 | `AimsDeployProfile.apply` is: sync_wheel + deploy_workspace only (no telemetry emit, no reconciler call) | Full read of `apply()` — two function calls wrapped in try/except; builds `AimsWheelUploadSnapshot` + `DeployResult`; returns. Zero telemetry calls. Zero reconciler calls | Confirmed (v2 overclaimed both) |
| 16 | `Hs2CapacityPolicy` default apply is read-only skip | Read source — when no injected `apply_fn`: `return CapacityApplyResult(applied=0, skipped=len(actions))`. Caller injects `plan_fn` / `apply_fn` for real policy | Confirmed (v2 overclaimed) |
| 17 | Banned-API + version-alignment + structure prereq tests exist | `ls tests/prereqs/` — `test_phase7_banned_apis.py`, `test_phase8_banned_apis.py`, `test_version_alignment.py`, `test_adr_structure.py`, `test_changelog.py`, `test_wiki_links.py`, `test_evidence_schemas.py`, plus 6 `.Tests.ps1` Pester files | Confirmed |
| 18 | CLIENT-01 (httpx-only-in-client) enforcement test | `tests/sigantry_core/client/test_package_structure.py` exists | Confirmed |
| 19 | `pytest11` entry point auto-registers contract fixtures downstream | `pyproject.toml [project.entry-points.pytest11] sigantry_core = "sigantry_core.testing.fixtures"` | Confirmed |
| 20 | Test counts | **SUPERSEDED — do not cite.** As measured 2026-04-24: `conda run -n sigantry-core pytest --collect-only -q` in repo root = **1057 tests** (base); same in `fabric-dataops-toolkits-hs2/` = **99 tests**; total = **1156**. Re-measured 2026-08-14 in the `fabric-dataops-toolkits` env, the whole-repo `pytest` run reports **2144 passed / 6 skipped / 17 deselected / 0 failed** (rc=0) — see CONTRIBUTING.md for the current baseline. The earlier `test_wheel_passes_twine_check` failure is cleared (env twine is now 7.0.0). | Confirmed at the time; numbers now stale |
| 21 | PowerShell: `Fabric` 1.0.0 exports `Get-FabricToken` / `Get-FabricTenantSetting`; `Hs2Fabric` 2.0.0 `RequiredModules=Fabric 1.0.0` and re-exports as `Get-Hs2Fabric*` | Read both `.psd1` files | Confirmed |
| 22 | Plugin version `1.0.0` vs base `2.0.1` | `pyproject.toml` versions + base `_version.py` | Confirmed |
| 23 | Six entry-point groups in `pyproject.toml` — all empty in base | **SUPERSEDED.** True on 2026-04-24: `[project.entry-points."fabric_dataops_toolkits.*"]` sections existed with no plugins in base. Re-checked 2026-08-13: those legacy tables were dropped in v3.1 and the base now declares 11 `sigantry.*` groups (`pyproject.toml:84-119`). | Confirmed at the time; no longer true |
| 24 | HS2 plugin registers one entry per seam | Read `fabric-dataops-toolkits-hs2/pyproject.toml` — exactly 6 `[project.entry-points."fabric_dataops_toolkits.*"]` tables, one class each | Confirmed |

---

## 5. Known unknowns (explicitly not yet verified)

These remain open. Do NOT re-probe them casually — they each have real cost or require tenant access.

- **Live integration test pass count.** HANDOFF claims 15 green against the HS2 tenant; I did not run the integration suite this session. `pytest -m integration` or `PYTEST_RUN_INTEGRATION=1 pytest tests/integration/` is the command; requires `HS2_FABRIC_TEST_*` credentials.
- **`.env.live` contents.** Not read — it's a secret. Structure documented in `fabric-dataops-toolkits-hs2/docs/live-testing.md`.
- **Every plugin's Protocol conformance at runtime.** Structural typing means `isinstance(impl, DeployProfile)` only checks method names + `name` attribute, not signatures. Contract tests via `testing.fixtures` are the canonical check.
- **Track 1** (LA emit DCR round-trip) — blocked on bicep deploy + `Monitoring Metrics Publisher` RBAC. See HANDOFF §"Deferred live-test tracks".
- **Track 2** (DQ gate live from local pytest) — architecturally deferred; the notebook path ships but a local gate test is not possible without the Fabric notebook runtime.
- **Track 3** (git connect live) — blocked on portal-OAuth Connection GUID + disposable `NotConnected` test workspace.
- **Whether `.planning/STATE.md` reflects reality.** Last updated 2026-04-22 and says Phase 08 in progress; roadmap completed Phase 9 at v2.0.1. Stale.

---

## 6. Change protocol for this doc

1. When code in any of the surfaces listed above changes, re-run the corresponding grep / read / `pytest --collect-only` from the validation table and update the row.
2. When an unknown in §5 is resolved, move it up into §4 with its verification method, and strike the §5 entry.
3. Never weaken a claim without evidence. If verification now fails, either (a) fix the code or (b) mark the row `Regression` and open a ticket — don't silently downgrade the claim.
4. This doc's "Status" line at the top records the master SHA it was verified against. Bump the SHA on every substantive update.
