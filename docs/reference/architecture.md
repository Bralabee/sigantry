# Architecture & Process Reference

**Status:** a dated record. §1 describes the public repository, and so do these parts of §2–§4: the consumer and plugin boxes and the vendor-import bullet in §2, the `plan` / `apply` steps in §3.1, the subapp count in §3.6, the step actions in §3.7, and rows 17 and 20 of §4. The rest of §2–§4 records what was checked on 2026-04-24 and has not been re-verified since. Its counts are known to be stale: for example, §2 and row 8 give 12 subapps where §3.6 gives 18, and §2 and row 13 give a 3-step `sync_wheel` where §3.1 gives four steps. Treat that part as a record of method, not as current numbers.
**Source of truth:** the repo itself. When this doc and code disagree, the code wins — update this doc.

This document is the architecture + process reference for Sigantry. Every claim below was validated against a file read, a grep, or a `pytest --collect-only` run when it was written. The validation table at the end records how each claim was verified.

If you're about to propose a change, extend a seam, or ingest a new requirement, start here. Do not re-probe "unknown" questions that have already been answered below; the validation notes record what was actually checked.

---

## 1. System boundaries

One Python distribution ships from this repository:

| Artefact | Path | Version | Role |
|---|---|---|---|
| `sigantry` (PyPI) | `sigantry_core/` | `sigantry_core/_version.py` | Agnostic base. 11 protocol seams (deploy profile, DQ gate, telemetry sink, auth provider, runbook registry, capacity policy, work-item provider, notification sink, secret store, approval gate, PR-review bot), registry, config, dispatchers, HTTP client, CLI, governance audit (including `emit_deploy_record`), testing doubles. |

Also in the repository, not in the wheel: the CI templates under `templates/` (ADO YAML — environments, extends, jobs, stages, steps, schedules, pr-review, plus the starter and demo scaffolds) and the reusable GitHub Actions workflows under `.github/workflows/`. Organisation-specific behaviour (a deploy profile, a DQ gate, a telemetry sink, ...) lives in plugin wheels that an organisation builds and installs itself; none ships from this repository.

Dependency direction is **one-way**: consumer repos depend on `sigantry_core` and optionally on their own plugins; never the reverse. Plugins depend on base. Base never imports a plugin by name: the registry loads plugins at run time through entry points in the `sigantry.*` groups (and the six legacy group names it still reads), or takes them by direct registration (`sigantry_core/registry.py`).

---

## 2. Architecture diagram (v3 — corrections applied inline)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                             CONSUMER / OPERATOR SURFACE                              │
│   ADO pipelines (templates/)  │   Python `from sigantry_core import …`               │
│   Bash: `sigantry …`          │   GitHub Actions (.github/workflows/)                │
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
  │  ORGANISATION PLUGINS (optional; separate wheels, none ship from this repo)  │  │
  │                                                                              │  │
  │  <name> → a DeployProfile    plan → actions; apply composes base helpers     │  │
  │                              (deploy.environment.sync_wheel,                 │  │
  │                               deploy.core.deploy_workspace)                  │  │
  │  <name> → a DataQualityGate  runs a suite from the organisation's DQ lib     │  │
  │  <name> → a TelemetrySink    e.g. Azure Monitor DCR/DCE ingestion            │  │
  │  <name> → an AuthProvider / RunbookRegistry / CapacityPolicy                 │  │
  │                                                                              │  │
  │  Declared in the plugin's own pyproject.toml under sigantry.<seam>           │  │
  │  entry-point groups; named in .sigantry.toml; resolved by the registry.      │  │
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
  │                 profiles/ (empty in base — plugins supply profiles)          │  │
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
  │   • Module-scope vendor imports only in allow-listed modules                 │  │
  │     (test_no_vendor_imports)                                                 │  │
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
     FabricDataOps(auth=…, telemetry=…, deploy_profile=<resolved plugin>, …)
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
                       └─ plugin-defined: reads ctx.parameters, returns
                          DeployPlan(actions=[...])
                            │
                            ▼
                      result = profile.apply(ctx, plan)
                       └─ plugin-defined: typically composes base helpers --
                          deploy.environment.sync_wheel(...)
                            └─ POST …/staging/libraries (upload)
                            └─ POST …/staging/publish   (starts the build)
                            └─ poll publishDetails.state until terminal
                            └─ GET  …/libraries         (verify published)
                          deploy.core.deploy_workspace(...)
                            └─ fabric-cicd 1.x drives item-tree publish
                               (5 optional scope filters: item_name_exclude_regex,
                                folder_path_exclude_regex, folder_path_to_include,
                                items_to_include, shortcut_exclude_regex;
                                2 of those also flow to orphan-unpublish)
                          and returns DeployResult(workspace_id, items_published,
                                                   items_failed, raw={...})
                            │
                            ▼
                     NOTE: the orchestrator neither emits telemetry nor calls
                           reconcile_folders_from_repo on the profile's behalf.
                           The caller does both if desired:
                             fdo.emit("deploy_completed", {...})
                             reconcile_folders_from_repo(client, ws, repo, …)
              │
              ▼ ( context-manager exit or explicit .close() )
     FabricDataOps.close()
       for seam in (auth, telemetry, dq_gate, deploy_profile, runbooks, capacity):
         if seam is None: continue
         if isinstance(seam, Closeable):   ← runtime_checkable duck-type
             seam.close()                  ← e.g. a telemetry sink releases
                                             its HTTP client
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
     <resolved DataQualityGate>.run(suite, data_ref)  → GateResult
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
     <resolved TelemetrySink>.emit(TelemetryEvent(name, properties, timestamp))
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

### 3.6 CLI surface — `sigantry workspace list ...`

```
shell ─▶ sigantry workspace list --tenant-id <...>
            │
            ▼ (pyproject [project.scripts])
     sigantry_core.cli:main -> app  (Typer, 18 subapps)
            │
            ▼  app.add_typer(workspace_app, name="workspace")
     workspace.cli.list_cmd
       ├─ _cli_tenant.resolve_tenant_id(--tenant-id)
       │    └─ else core.tenant_id from the settings; a GUID or refused
       ├─ FabricRestClient.from_defaults(tenant_id=…)
       │    └─ azure-identity DefaultAzureCredential chain; with a tenant,
       │       wrapped so every token is requested from it and a token
       │       whose tid names another tenant is refused (auth/tenant.py)
       ├─ paginate(GET /v1/workspaces [?roles=…])
       └─ rich.Table | json output

IMPORTANT: workspace / capacity / label-sync / rbac-audit / tenant-settings /
fabric-item / git / variable-library / env subapps bypass the seam layer
entirely — they call v1 subpackages directly. Only `deploy`, `dq`, and
`doctor` touch the dispatcher / registry layer.
```

### 3.7 ADO pipeline surface

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
              ├─ fabric-*: pip install sigantry, then sigantry deploy run /
              │            deploy validate / git commit / variable-library update
              └─ post-pr-comment: posts a PR thread through the ADO REST API
```

---

## 4. Validation table

| # | Claim | How verified | Verdict |
|---|---|---|---|
| 1 | Six `@runtime_checkable` Protocols in `protocols.py` | File read — `DeployProfile`, `DataQualityGate`, `TelemetrySink`, `AuthProvider`, `RunbookRegistry`, `CapacityPolicy` + optional `Closeable` | Confirmed |
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
| 17 | Version-alignment + changelog + wiki-link prereq tests exist | `ls tests/prereqs/` — `test_version_alignment.py`, `test_changelog.py`, `test_wiki_links.py` | Confirmed |
| 18 | CLIENT-01 (httpx-only-in-client) enforcement test | `tests/sigantry_core/client/test_package_structure.py` exists | Confirmed |
| 19 | `pytest11` entry point auto-registers contract fixtures downstream | `pyproject.toml [project.entry-points.pytest11] sigantry_core = "sigantry_core.testing.fixtures"` | Confirmed |
| 20 | Test counts | **SUPERSEDED — do not cite.** For current numbers, run `python -m pytest` after the setup in CONTRIBUTING.md. | Stale |
| 23 | Six entry-point groups in `pyproject.toml` — all empty in base | **SUPERSEDED.** True on 2026-04-24: `[project.entry-points."fabric_dataops_toolkits.*"]` sections existed with no plugins in base. Re-checked 2026-08-13: those legacy tables were dropped in v3.1 and the base now declares 11 `sigantry.*` groups (`pyproject.toml:84-119`). | Confirmed at the time; no longer true |

---

## 5. Known unknowns (explicitly not yet verified)

These remain open. Do NOT re-probe them casually — they each have real cost or require tenant access.

- **Every plugin's Protocol conformance at runtime.** Structural typing means `isinstance(impl, DeployProfile)` only checks method names + `name` attribute, not signatures. Contract tests via `testing.fixtures` are the canonical check.
- **Track 3** (git connect live) — blocked on portal-OAuth Connection GUID + disposable `NotConnected` test workspace.

---

## 6. Change protocol for this doc

1. When code in any of the surfaces listed above changes, re-run the corresponding grep / read / `pytest --collect-only` from the validation table and update the row.
2. When an unknown in §5 is resolved, move it up into §4 with its verification method, and strike the §5 entry.
3. Never weaken a claim without evidence. If verification now fails, either (a) fix the code or (b) mark the row `Regression` and open a ticket — don't silently downgrade the claim.
4. This doc's "Status" line at the top records when the page was last checked against the code. Update it on every substantive edit.
