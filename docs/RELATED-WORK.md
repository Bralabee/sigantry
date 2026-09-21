# Related Work — `usf_fabric_cli_cicd`

**Source:** `~/Documents/J'TOYE_DIGITAL/LEIT_TEKSYSTEMS/1_Project_Rhico/usf_fabric_cli_cicd`
**Surveyed:** 2026-04-29 (initial scan + verification pass against actual source)
**Their version at time of survey:** v1.9.2
**Sigantry-side re-audit:** 2026-06-11 — the gotcha verdicts and §6 recommendations
below have been updated in place: items 1-4 of §6 are CLOSED, shipped 2026-04-29
in the related-work hardening set (commits `e73c015` gotcha #6, `ba6fc15`
gotcha #10, `4bef215` gotcha #12; CHANGELOG heading "Added -- Related-work
hardening (lifted from `usf_fabric_cli_cicd` v1.7.x-1.9.x)").
Companion ecosystem survey: [LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md).

A mature (~14 months evolution, Jan 2026 → Mar 2026), multi-customer (Ricoh + JToye)
Fabric CI/CD CLI in the same product category as Sigantry. Built one or two iterations
earlier by an adjacent author. This document captures what we can learn — both
patterns worth borrowing and bugs we can pre-empt.

Claim labels: `[VERIFIED]` = checked against their actual source code (file:line);
`[DOC-CLAIM]` = sourced from their own CHANGELOG/docs and not independently
re-verified. **Verification pass on 2026-04-29 confirmed every gotcha entry below
is implemented in their current source — their CHANGELOG accurately reflects the
codebase.**

---

## 1. Positioning summary

| Dimension | usf_fabric_cli_cicd | Sigantry |
|---|---|---|
| Entry points `[VERIFIED — pyproject.toml]` | `fabric-cicd`, `usf-fabric` (alias) | `sigantry` |
| Stack `[VERIFIED — pyproject.toml]` | Typer + Pydantic v2 + Jinja2 + jsonschema + GitPython + requests | Typer + httpx + tenacity + Pydantic |
| HTTP `[VERIFIED]` | `requests` + custom `fabric_api_base.py` (4.9KB) | `httpx` + tenacity + single front-door |
| Wraps `[VERIFIED]` | `fab` CLI subprocess in `fabric_wrapper.py` (1975 LOC monolith) | `ms-fabric-cli`, `fabric-cicd`, `msfabricpysdkcore` (thin wrappers) |
| Plugin model `[VERIFIED]` | None — multi-customer via `.env.<customer>` files | Protocol-seam plugins (17 plugins, 11 seam groups) |
| State `[DOC-CLAIM]` | `DeploymentState` for rollback | `DeployRecord` ledger + `sigantry deploy run --rollback` |
| Audit `[VERIFIED — audit_logs/ + audit_report_..._2026-03-02.xlsx exist]` | JSONL + monthly Excel rollup | `governance.audit` decorator |
| Web UI `[VERIFIED — webapp/ exists with FastAPI + Vite/React]` | FastAPI + React/TanStack/Radix | None |
| License `[VERIFIED]` | Proprietary (Ricoh) | Apache-2.0 |
| Live-validation discipline `[DOC-CLAIM]` | Green unit tests + monthly XLSX audit | V3-RISK-1/2/3 operator UAT (26 gates) |

**Major divergence:** they took a monolithic-with-customer-envs path; we took a
plugin-with-protocol-seams path. Their `deployer.py` (**1842 LOC** as of v1.9.2,
`[VERIFIED via wc -l]`) and `fabric_wrapper.py` (**2152 LOC**) are flagged as tech
debt in their own `.planning/codebase/CONCERNS.md` (which quoted older 1649 / 1975
counts dated 2026-02-26 — code grew ~10-15% since then). Verified counts: 30
non-`__init__.py` source modules; 23 test files; 738 `def test_` test functions
(CHANGELOG claims 763 passing — gap is parametrized / class-based test cases).

---

## 2. CLI verbs they have that Sigantry doesn't `[VERIFIED — grep @app.command in src/usf_fabric_cli/cli.py]`

| Verb | Purpose | Sigantry equivalent |
|---|---|---|
| `scaffold` | Live workspace → YAML config (brownfield greenfield-ifier) | **None** — this is the BOOTSTRAP-XX gap (deferred to v3.x) |
| `discover-folders` | Diff live folders vs YAML, auto-update YAML (CI auto-commit, exit 2 = changes) | Partial (`sync pull` exists; no folder-diff-into-yaml) |
| `bind-direct-lake` | Bind Direct Lake datasource | None |
| `repoint-connections` | Rebind semantic-model datasources across stages | None |
| `bulk-destroy` | Multi-workspace teardown w/ pipeline unbinding (O(pipelines) upfront map) | None |
| `organize-folders` | Apply folder-rules to existing items | Partial overlap with `sync apply` |
| `onboard` | Full Dev+Test+Prod+Pipeline bootstrap from one command | **None** (BOOTSTRAP-XX) |
| `init-github-repo` / `init-ado-repo` | Auto-create + initialize SCM repo | None |
| `feature-workspace` | Branch-isolated workspace (auto-create on push, auto-destroy on PR merge) | Partial (we have WorkItemProvider traceability but no feature-workspace lifecycle) |

---

## 3. API gotchas — bugs they paid for that we should pre-empt

These are sourced from their CHANGELOG entries v1.7.x → v1.9.x. Each carries our
audit verdict against current Sigantry code.

| # | Gotcha | Source | Sigantry status |
|---|---|---|---|
| 1 | `commitToGit` body field is `comment`, not `message` (silently dropped commit msgs) | `[VERIFIED their services/fabric_git_api.py:549]` `"comment": message` | ✅ **CORRECT** — `sigantry_core/deploy/git_integration.py:157` uses `"comment": comment` |
| 2 | `initializeConnection` response is camelCase (`requiredAction`, `remoteCommitHash`, `workspaceHead`) — PascalCase silently defaults to `"None"` | `[VERIFIED their fabric_git_api.py:427,437,438]` reads `result.get("requiredAction", "None")`, `result.get("remoteCommitHash")`, `result.get("workspaceHead")` | ⚠️ **NO CURRENT RISK** — `initialize_connection` returns the raw dict; only consumer is `deploy/cli.py:534-535` (prints as JSON). Tests use camelCase. **Doc-comment recommended** so future readers don't introduce PascalCase access. |
| 3 | `commitToGit` accepts `workspaceHead` for head-mismatch validation | `[VERIFIED their fabric_git_api.py:553]` `request_body["workspaceHead"] = workspace_head` | ✅ **CORRECT** — `git_integration.py:263` (line ref refreshed 2026-06-11 after the `connect_or_reconnect` insertion shifted the file) |
| 4 | `deploy_to_stage` `DeploymentOptions` dropped `allowCreateArtifact`/`allowOverwriteArtifact` (Power-BI-era); only accepts `allowCrossRegionDeployment` | `[VERIFIED their deployment_pipeline.py:548]` body has only `"allowCrossRegionDeployment": True` | **N/A** — Sigantry routes deploy through `fabric-cicd` wrapper; we don't construct `DeploymentOptions` directly. Worth a re-check if/when we add a native deployment-pipeline integration. |
| 5 | Selective promote `targetItemId` doesn't exist; `ItemDeploymentRequest` only takes `sourceItemId`+`itemType` | `[VERIFIED their deployment_pipeline.py:708-709]` body keys = `sourceItemId` + `itemType` only | **N/A** — same reason as #4. |
| 6 | Workspace deletion: Fabric `DELETE /v1/workspaces/{id}` intermittently returns `UnknownError`; PBI fallback (`DELETE https://api.powerbi.com/v1.0/myorg/groups/{id}`) works reliably | `[VERIFIED their fabric_wrapper.py:573 delete_workspace + :620 UnknownError detection + :626 _delete_workspace_pbi_api fallback]` | ✅ **CLOSED 2026-04-29** (`e73c015`) — `delete_workspace(..., pbi_fallback=True)` at `sigantry_core/workspace/core.py:86-142` detects the `UnknownError` envelope and falls back to the PBI groups DELETE. Live-proven in the Phase 13.5 UAT tear-down. |
| 7 | Pipeline `/users` is a Power BI API endpoint, not Fabric. SP cannot access via Fabric API. Also requires `ServicePrincipal → App` type translation and `pipelineRole → accessRight` field rename | `[VERIFIED their deployment_pipeline.py:43-52,286,317,335]` `PBI_API_BASE_URL = "https://api.powerbi.com/v1.0/myorg"` + SP-to-App map at lines 47-52 + `accessRight` at 335 | **N/A today** — Sigantry doesn't yet wire pipeline user management. **Pre-stash this in research before Phase X for native pipeline integration.** |
| 8 | `update_from_git` needs `conflictResolution.conflictResolutionPolicy` (`PreferRemote`/`PreferWorkspace`) and `options.allowOverrideItems` | `[VERIFIED their fabric_git_api.py:493-501]` body has `conflictResolution.conflictResolutionPolicy` + `options.allowOverrideItems` | ✅ **CORRECT** — `git_integration.py:234-238` (refreshed 2026-06-11) |
| 9 | `get_git_status` can return 202 LRO with empty body; must handle `operation_id + retry_after`, not assume sync 200 | `[VERIFIED their fabric_git_api.py:576,595-603]` `get_git_status` checks `status_code == 202` and returns `{operation_id, retry_after}` | ✅ **CORRECT** — `git_integration.py:275-286` uses `send_lro` which handles both 200 and 202 (refreshed 2026-06-11) |
| 10 | Pagination infinite-loop guard: Fabric API has known infinite-loop bugs (same `continuationToken` returned twice). Their fix: `max_pages=50` + duplicate-token detection | `[VERIFIED their fabric_wrapper.py:1791-1864]` — `max_pages=50` (line 1791) + `seen_tokens: set` (1793) + duplicate-token break with warning (1832-1839) | ✅ **CLOSED 2026-04-29** (`ba6fc15`) — `sigantry_core.client.pagination` now tracks `seen_tokens` on the token-only fallback path and raises `PaginationError` on a duplicate token, instead of spinning to `MAX_PAGES=1000`. |
| 11 | Polling busy-loop: `time.sleep(max(retry_after, 2))` not bare `time.sleep(retry_after)` (Fabric occasionally returns `Retry-After: 0`) | `[VERIFIED their fabric_git_api.py:696 + deployment_pipeline.py:620]` both use `time.sleep(max(retry_after, 2))` | ✅ **EFFECTIVELY CORRECT** — `sigantry_core/client/retry.py:_parse_retry_after` parses + caps; `client/base.py:315` falls back to 3.0s default. We don't have a hardcoded "0 returned → spin" path. |
| 12 | **Git connection idempotency trap (the big one):** `connect_workspace_to_git` returns "already connected" idempotently, but if the workspace is bound to a *different* repo/branch/dir, the next `initializeConnection` fails with **400 Bad Request**. Fix: check current connection vs target; disconnect-before-reconnect when they differ. Most common failure mode of `scaffold → deploy` on existing workspaces | `[VERIFIED their deployer.py:1044-1133]` Step 2 of `_setup_git_connection` reads existing connection, branches on mismatch (line 1090), calls `disconnect_from_git` (line 1111), then re-calls `connect_workspace_to_git` (line 1133). Pattern is fully wired and worth lifting verbatim. | ✅ **CLOSED 2026-04-29** (`4bef215`) — `connect_or_reconnect` (`git_integration.py:119-215`) reads current state, no-ops when already at target, and disconnects-then-reconnects on mismatch behind `force_reconnect=True` + destructive-op audit. Wired into `workspace bootstrap` step 4 (`workspace/bootstrap.py:452`). |
| 13 | Windows `.env` utf-8 encoding (Windows defaults cp1252 → silent failures) | `[VERIFIED their codebase has 15+ explicit `encoding="utf-8"` sites]` including `utils/secrets.py:41`, `cli.py:28`, `utils/config.py:379,410`, `scripts/dev/onboard.py:43`, `scripts/admin/preflight_check.py:26`, etc. | **Likely N/A** — Sigantry uses `pydantic-settings` and `DefaultAzureCredential`, not direct `.env` parsing. **Worth a sweep of any direct `open(".env")` calls.** |
| 14 | `typer.Exit` inherits from `RuntimeError` → silently caught by `except RuntimeError`. Move `typer.Exit` outside try/except blocks | `[VERIFIED their cli.py:1411]` literal comment in code: `# Raised outside try/except because typer.Exit inherits from RuntimeError` followed by `raise typer.Exit(code=2)` placed outside the try block. Many `except typer.Exit:` clauses also added defensively (lines 707, 1032, 1109, 1151, 1202, 1312, 1593, 1728, 1912) | ✅ **VERIFIED CLEAN 2026-06-11** — AST scan of every `sigantry_core/` module raising `typer.Exit`: none does so inside a try-block guarded by `except RuntimeError` / `except Exception`. |

### Audit summary (updated 2026-06-11)
- **5 VERIFIED-CORRECT**: gotchas #1, #3, #8, #9, #11
- **3 CLOSED 2026-04-29** (related-work hardening set: `e73c015` / `ba6fc15` / `4bef215`): gotcha #6 (PBI fallback on workspace delete), #10 (duplicate-token detection in pagination), #12 (`connect_or_reconnect` orchestration, wired into bootstrap)
- **2 VERIFIED / DOC-CLOSED**: gotcha #2 (camelCase doc-comment landed on `initialize_connection`), #14 (AST scan clean 2026-06-11)
- **2 N/A TODAY** (will become live if we add native pipeline integration): #4, #5, #7

---

## 4. Patterns worth lifting

1. **Blueprint catalog** `[VERIFIED — 11 yaml files in src/usf_fabric_cli/templates/blueprints/]`:
   `minimal_starter / medallion / data_mesh_domain / data_science / advanced_analytics / compliance_regulated / realtime_streaming / specialized_timeseries / migration_hybrid / extensive_example / basic_etl`.
   `generate_project.py` + Jinja2 substitutes org/project. **Sigantry's `templates/starter/`
   is one starter; theirs is a catalog**.

2. **Numbered medallion folder convention** `[VERIFIED — minimal_starter.yaml]`:
   `000 Orchestrate / 100 Ingest / 200 Store / 300 Prepare / 400 Model / 500 Visualize / 999 Libraries / Archive`
   plus `folder_rules` per item type (DataPipeline → 000, Lakehouse → 200, Notebook → 300, …).
   Operator-friendly default; Phase 14's PR-bot could enforce this.

3. **`scaffold --templatise` flag** `[DOC-CLAIM CHANGELOG 1.8.1]`: replaces real
   workspace/pipeline/principal names with `CHANGE-ME` and `CHANGEME_` placeholders.
   Round-trippable via `make new-project`.

4. **`scaffold --brownfield` flag** `[DOC-CLAIM CHANGELOG 1.8.4 + HANDOFF.md]`:
   emits discovered principal GUIDs as **active YAML entries** (not env-var
   placeholders). For propagating prod principals to test/staging without GitHub
   Secrets round-trips.

5. **Stage-marker regex parsing** `[VERIFIED — scaffold_workspace.py functions
   `_strip_any_stage_marker`, `_replace_stage_marker`, `_infer_stage_name` at lines
   463-528]`: workspace-name conventions like `[DEV] / [TEST] / [PROD] / [F] [FEATURE-branch]`
   are first-class. Worth borrowing for `sigantry sync` workspace-name conventions.

6. **`feature-workspace` make target** `[VERIFIED Makefile]`: branch-isolated workspace
   per feature branch, auto-create on push, auto-destroy on PR merge. Sigantry has
   WorkItemProvider traceability but no auto-feature-workspace lifecycle.

7. **Webapp pattern** `[VERIFIED webapp/ exists]`: FastAPI + React + TanStack Query
   for an in-browser deployment guide / scenario explorer. Differentiator they have
   that we don't.

8. **JSON-schema validation** of YAML configs `[VERIFIED — src/usf_fabric_cli/schemas/workspace_config.json]`
   via `jsonschema` library at parse time. Catches typos before they reach the API.
   Sigantry uses Pydantic; both work but the JSON-schema route is operator-readable.

9. **Per-customer `.env.<customer>`** files plus `.env.template` (master). Their
   multi-tenant story is "swap the env file"; ours is "swap the plugin." Theirs is
   simpler for operators; ours is cleaner for code. Could co-exist.

10. **`make scaffold` + `docker-scaffold` pairs for every operator command**
    `[VERIFIED Makefile]`: every Make target has a Docker variant. Means operators
    can run on a fresh laptop with only Docker. Sigantry has a Makefile but not
    every target has a Docker pair.

11. **Audit reports as Excel** `[VERIFIED — audit_report_..._2026-03-02.xlsx exists]`:
    JSONL audit log rolled into a monthly XLSX for non-engineer stakeholders.
    Pattern worth borrowing for compliance teams.

---

## 5. Anti-patterns / regrets they're carrying `[VERIFIED — their CONCERNS.md]`

- **Bare `except Exception: pass`** in 11+ files including auth paths.
- **Two large monolithic files** (`fabric_wrapper.py` 1975 LOC, `deployer.py` 1649 LOC)
  flagged as tech debt; recommended split not yet done.
- **Two `DEPRECATED` modules without removal timeline** (`utils/config.py:372`,
  `services/git_integration.py`). Sigantry's deprecation shims are time-stamped;
  this is the failure mode to avoid.
- **No protocol seams** — every customer-specific behaviour is a Pydantic field or
  env var. Adding a new customer needs core code changes.
- **Inconsistent API choice** (Fabric vs Power BI) leaks into code; no abstraction.
- **Subprocess to `fab` CLI** instead of REST — needs PATH-installed Fabric CLI on
  every runner; doubles the failure surface.
- **No live-validation gate** mentioned in their docs. They ship from green
  unit-tests + monthly audit. Sigantry's V3-RISK-1/2/3 UAT discipline is more rigorous.

---

## 6. Recommended actions for Sigantry

In rough priority order:

1. ✅ **CLOSED 2026-04-29** (`e73c015`, `4bef215`) — gotchas #6 and #12 both fixed
   (`pbi_fallback` + `connect_or_reconnect`), tested, and wired into
   `workspace bootstrap`.
2. ✅ **CLOSED 2026-04-29** (`ba6fc15`) — duplicate-token detection added to
   `sigantry_core.client.pagination` (raises `PaginationError`).
3. ✅ **CLOSED 2026-04-29** — camelCase doc-comment landed on
   `initialize_connection` with test citations.
4. ✅ **VERIFIED CLEAN 2026-06-11** — AST scan found no `typer.Exit` inside
   `except RuntimeError`/`except Exception` guards in `sigantry_core/`.
5. **Re-open BOOTSTRAP-XX (Phase 13.5)** as a v3.x phase, modelling the verb on
   their `scaffold --brownfield --templatise --as-stage` semantics rather than
   from-scratch. They've already validated the operator flow against live
   workspaces.
6. **Borrow the blueprint-catalog idea** for `templates/starter/` — turn one starter
   into a directory of named patterns. Phase 14's starter-repo work already laid
   the groundwork.
7. **Lift the numbered-folder-rules convention** as a documented default, possibly
   enforced by the Phase 14 PR-bot.
8. **Mine their CHANGELOG.md systematically** before any phase that touches a new
   Fabric API surface. Every "Fixed" entry from v1.7.x → v1.9.x is a Fabric REST
   API discovery we don't have to make on our own.
9. **Consider whether Sigantry needs a webapp** as a v3.x or v4.0 differentiator.
   Not urgent; theirs is a guide, not control plane.

---

## 7. Caveats

- A 2026-04-29 verification pass confirmed every gotcha entry above against their
  actual source (file:line citations in column 3). Our initial concern that the
  CHANGELOG might be stale relative to the codebase did not materialise — their
  fixes are present and the field-name / orchestration patterns line up with
  what the CHANGELOG describes.
- Their multi-customer model (`.env.ricoh`, `.env.jtoye`) is a different commercial
  posture from Sigantry's plugin model. Operator-flow learnings transfer; commercial
  architecture does not.
- Survey was a single-session scan. Their `services/deployer.py` (1842 LOC) was
  read selectively (the disconnect-before-reconnect orchestration at lines
  1044-1166 was confirmed; the rest was not) — additional patterns may yield
  further learnings on a focused follow-up.
- Test count `738` reflects `def test_` definitions; their CHANGELOG `763` claim
  reflects test cases (parametrized / class-based fixtures multiply). Both
  numbers are correct from their perspective; we did not run their suite.
