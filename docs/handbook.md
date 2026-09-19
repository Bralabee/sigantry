# Usage Handbook

**Last updated:** 2026-06-14 (v3.4.0 env reconcile)
**Toolkit version:** 3.4.0

This is the living, day-to-day usage reference for `sigantry-core`. It covers the scenarios the toolkit is designed for, the environment you work in, every `make` target, the complete CLI surface, both configuration patterns, and common recipes. Every claim cites the file or command that proves it — if a section drifts from reality, the citation is how we find and fix it.

If you're brand new: start at §2 (ground zero) and run through the "shortest path from nothing to running" in §6. Everything else is reference material you dip into when you need it.

---

## 1. Scenarios — when to reach for this toolkit

From the 17 wired CLI subapps (`sigantry_core/cli.py:66-82` — `release` joined as the 13th in Phase 11; `sync` + `diff` added in Phase 13; `config` + `pr-bot` added in Phase 14) plus the `diagnose-auth` console script (`pyproject.toml`) and the 11 protocol seams in `sigantry_core/protocols.py` (the original six plus the v3 additions `WorkItemProvider`, `NotificationSink`, `SecretStore`, `ApprovalGate`, `PrReviewBot`).

| You're doing… | Tool you'd use |
|---|---|
| Listing, creating, or deleting a Fabric workspace; assigning to capacity | `sigantry workspace` |
| Pausing, resuming, or scaling an F-SKU Fabric capacity | `sigantry capacity` |
| Publishing a Git tree of `.platform` items to a Fabric workspace | `sigantry deploy` (thin wrapper over `fabric-cicd`) |
| Connecting / disconnecting Fabric ↔ ADO Git integration | `sigantry git` |
| Uploading a wheel to a Fabric Environment (one target) | `sigantry env sync` (blocks until the publish rebuild finishes; on an *upgrade* strip the old wheel from staging first — see [Tutorial 11](tutorials/11-environments-and-libraries.md#troubleshooting--the-dual-version-pitfall)) |
| Keeping many Environments current from a manifest | `sigantry env sync-all` + `environments.yml` |
| Copying a Fabric item with `logicalId` regeneration | `sigantry fabric-item` |
| RBAC audit, sensitivity-label sync, tenant-settings baseline | `rbac-audit`, `label-sync`, `tenant-settings` |
| Running a DQ gate before a deploy step | `sigantry dq` |
| Variable Library CRUD | `sigantry variable-library` |
| "Which plugins are installed, and did any fail to import?" | `sigantry doctor` |
| "Why is auth failing — 401 or 403?" | `diagnose-auth` |
| Emit structured telemetry from a deploy pipeline into Log Analytics | `FabricDataOps.emit(...)` + the HS2 plugin's `LogAnalyticsSink` |
| Provision Azure-side telemetry infra (DCE + DCR + LA + alerts) | `bicep/main.bicep` + `bicep/examples/greenfield.bicepparam` |
| PowerShell token acquisition for Fabric / Power BI / Graph / Purview / ARM | `Fabric\Get-FabricToken` (PowerShell module) |

**Explicitly not for** (`CLAUDE.md`): Fabric replacement, custom lineage engine, DQ-rule authoring, Power BI content authoring, cross-cloud abstraction, on-prem migration, a web portal, real-time preventive policy, or an auto-remediating control plane.

---

## 2. Ground zero — your environment

**Conda is the first-class environment mechanism.** venv exists as a documented fallback for contributors without conda; do not default to it.

- **Env name:** `fabric-dataops-toolkits` (pinned in `environment.yml:40` and `Makefile:17`). The conda env name is deliberately held at the v2.x string through v3.0 per ADR-0011 — renaming the env breaks every contributor's local machine and is deferred (V3.X-ROADMAP LEGACY-SURFACE-DROP). The *package* renamed (`sigantry-core`); the *env* did not.
- **Python:** 3.11 (Fabric notebook runtime parity; `environment.yml` and `pyproject.toml:9` both pin it)
- **Sibling-repo convention:** AIMS uses `aims_data_platform`, DQ uses `fabric-dq` — this repo follows the same pattern

### First time

```bash
cd /mnt/Storage/Insync/olusanmi_18th@hotmail.com/OneDrive/Documents/HS2/HS2_PROJECTS_2025/3_DATAOPS_FABRIC_2026

make conda-create                        # or: conda env create -f environment.yml
conda activate fabric-dataops-toolkits   # always run before working
make install-dev                         # editable install of base + plugin with dev+test extras
```

### Every subsequent session

```bash
conda activate fabric-dataops-toolkits
```

### After pulling changes that touch `environment.yml` or `pyproject.toml`

```bash
make conda-update                        # conda env update --prune
```

### Sanity check

```bash
python -c "import sigantry_core; print(sigantry_core.__version__)"
# → 3.4.0

sigantry doctor           # lists every discovered plugin
```

### venv fallback (only if you cannot use conda)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"
```

---

## 3. Make commands — the complete inventory

From `Makefile`. Run `make help` for the live list.

| Target | What it does |
|---|---|
| `make help` | Print the live target list |
| **Conda (first-class)** | |
| `make conda-create` | Create the conda env from `environment.yml` |
| `make conda-update` | Update env after `environment.yml` / `pyproject.toml` changes (`--prune`) |
| `make conda-recreate` | Remove + recreate from scratch |
| `make conda-remove` | Delete the conda env |
| `make conda-status` | Print env existence, Python version, pip-package count |
| **Install (after activating the env)** | |
| `make install` | Editable install of base + HS2 plugin (plain — plugin runtime deps resolve cleanly) |
| `make install-dev` | `make install` plus `[dev,test]` extras (adds ruff, mypy, freezegun, respx, etc.) |
| **Test / lint** | |
| `make test` | pytest prereqs + Pester |
| `make test-pytest` | Just pytest on `tests/prereqs` (for the full suite use `pytest` directly) |
| `make test-pester` | Just Pester via `tests/Pester.config.ps1` |
| `make docs-doctest` | Run doctest collection over `sigantry_core/` |
| `make lint` | `ruff check` + `ruff format --check` |
| `make clean` | Remove `.pytest_cache .ruff_cache TestResults.xml` |

---

## 4. CLI surface — every subapp

Two console scripts are declared in `pyproject.toml:61-63`. After `conda activate fabric-dataops-toolkits` + `make install-dev`, both are on your PATH.

### Main CLI

```bash
sigantry --help
# or, when the script isn't on PATH (containers, isolated venvs):
python -m sigantry_core --help
```

17 subapps (every one accepts `--help`):

| Subapp | Purpose |
|---|---|
| `workspace` | Fabric workspace CRUD + items + capacity assignment + greenfield `bootstrap` (Phase 13.5) |
| `capacity` | Fabric capacity inspection + lifecycle (pause/resume via ARM) |
| `label-sync` | Apply a sensitivity label to every item in a workspace |
| `rbac-audit` | Emit workspace + capacity + item RBAC audit |
| `tenant-settings` | Export Fabric admin tenant-settings baseline |
| `deploy` | Deploy Fabric items from a Git working tree via `fabric-cicd`; supports `--rollback --to-release <id>` (Phase 12) |
| `fabric-item` | Fabric item folder operations (copy with logicalId regeneration) |
| `git` | Fabric workspace ↔ ADO Git integration (7-endpoint surface) |
| `variable-library` | Fabric Variable Library CRUD |
| `env` | Fabric Environment wheel upload |
| `dq` | Run a registered DQ gate plugin against a dataset |
| `doctor` | List discovered plugins and diagnose entry-point import failures |
| `release` | Sigantry release records — `record` / `list` / `show` / `diff` with work-item links + immutable audit hash (Phase 11, TRACE-05) |
| `sync` | Local ↔ Fabric folder-aware sync engine — `apply` / `pull` / `snapshot` (Phase 13, SYNC-01..06) |
| `diff` | Drift detection between a local `sync.yml` manifest and the live Fabric workspace (Phase 13, DRIFT-01..02) |
| `config` | Validate Sigantry configuration files (e.g. `config validate parameters.yml` against the fabric-cicd schema) |
| `pr-bot` | PR-review bot — `run` detects provider, diffs TMDL + Lakehouse, posts comment (Phase 14, STARTER-04..06) |

### Auxiliary

```bash
diagnose-auth                   # identifies which credential in the DefaultAzureCredential
                                # chain succeeded; distinguishes 401 (token) from 403 (API off)
```

### Plugin-specific (HS2)

```bash
sigantry-hs2-livecheck                     # pre-flight for the live-Azure integration suite
                                           # (requires the HS2 plugin installed)
```

See `sigantry-hs2/docs/live-testing.md` for the HS2 live-testing workflow.

---

## 5. Configuration — two wiring paths

From `sigantry_core/api.py:1-25`.

### Path A — direct dependency injection (tests, notebooks, scripts you own completely)

Zero config files, zero cloud auth. Use the in-memory testing doubles:

```python
from sigantry_core import FabricDataOps
from sigantry_core.testing.doubles import (
    InMemoryTelemetrySink, FakeDeployProfile, NoopGate,
)
from sigantry_core.protocols import DeployContext

fdo = FabricDataOps(
    telemetry=InMemoryTelemetrySink(),
    deploy_profile=FakeDeployProfile(),
    dq_gate=NoopGate(),
)
fdo.emit("first_event", {"note": "exploring"})
result = fdo.deploy(DeployContext(workspace_id="ws-demo", environment="dev"))
print(result.items_published, result.items_failed)
```

Proven by `tests/integration/test_greenfield_hss.py` — the base alone is enough to author a deployable profile plus telemetry sink.

### Path B — config-driven (production)

Create `.fabric-dataops.toml` at the repo root. The `[<seam>]` tables select a named plugin implementation; `[<seam>.<name>]` sub-tables pass constructor kwargs to that plugin. Plugin names come from the plugin's `pyproject.toml` entry points (e.g. `sigantry-hs2/pyproject.toml:61-79`).

```toml
[core]
tenant_id = "<your-entra-tenant-guid>"

[auth]
provider = "hs2_entra_group"

[deploy]
profile = "aims"

[dq]
gate = "dq_framework"

[telemetry]
sink = "log_analytics"

[telemetry.log_analytics]                         # per-plugin nested config
dce_uri = "https://<your-dce>.ingest.monitor.azure.com"
dcr_immutable_id = "<your-dcr-immutable-id>"
stream_name = "Custom-Hs2Deploy"

[runbooks]
registry = "hs2_teams"

[capacity]
policy = "hs2"
```

Load + use:

```python
from sigantry_core import FabricDataOps
from sigantry_core.protocols import DeployContext

fdo = FabricDataOps.from_config(".fabric-dataops.toml")
result = fdo.deploy(DeployContext(
    workspace_id="<workspace-guid>",
    environment="PROD",
    parameters={"wheel_path": "./dist/my_wheel.whl", "items_directory": "./fabric_items"},
))
```

### Env-var overrides

`pydantic-settings` layers `FDT_<section>__<key>` env vars over the TOML. Example:

```bash
FDT_CORE__TENANT_ID=<tenant>
FDT_DEPLOY__PROFILE=aims
```

### Azure infrastructure (Bicep)

Examples at `bicep/examples/`:

| Bicepparam | Purpose |
|---|---|
| `greenfield.bicepparam` | Generic consumer template, every required param named with `<placeholder>` |
| `dcr-telemetry.bicepparam` | DCR provisioner for live telemetry (Phase 9) |

Deploy:

```bash
az deployment group create \
  --resource-group <your-rg> \
  --template-file bicep/main.bicep \
  --parameters bicep/examples/greenfield.bicepparam
```

---

## 5b. Declarative live credentials — the `.env.live` path

Live-Azure integration tests need credentials (service principal + Fabric test-tenant resource ids). The reproducible, non-ad-hoc way to supply them:

1. **Copy the template:**
   ```bash
   cp scripts/live-creds.template .env.live
   ```
   `.env.live` is gitignored via the existing `.env.*` pattern in `.gitignore`.

2. **Fill it in.** The template documents every variable and ships a Key-Vault-bootstrap snippet:
   ```bash
   VAULT=<your-keyvault-short-name>
   {
     echo "AZURE_TENANT_ID=$(az keyvault secret show --vault-name "$VAULT" --name sp-tenant-id     --query value -o tsv)"
     echo "AZURE_CLIENT_ID=$(az keyvault secret show --vault-name "$VAULT" --name sp-client-id     --query value -o tsv)"
     echo "AZURE_CLIENT_SECRET=$(az keyvault secret show --vault-name "$VAULT" --name sp-client-secret --query value -o tsv)"
   } > .env.live
   # Append the HS2_FABRIC_TEST_* values from the platform team.
   ```

3. **Run the integration suite** — no `export` required. `tests/integration/conftest.py` auto-loads `.env.live` at collection time (existing exported env vars always take precedence):
   ```bash
   pytest tests/integration/ -v
   ```

4. **Pre-flight before the suite** (recommended first run):
   ```bash
   sigantry-hs2-livecheck --load-env .env.live
   ```

**Precedence contract:** exported env vars always win over `.env.live`. The file is a declarative default — you can still override one value for one run with `HS2_FABRIC_TEST_WORKSPACE_ID=other-ws pytest ...`.

**Why not `export` every time?** You'd duplicate the values in shell history, CI env, every tab you open. `.env.live` is the single source of truth, gitignored, read once, same in every shell / editor / CI job that mounts it.

**Future increment (open question tracked in §9):** `.fabric-dataops.toml` will accept `kv://<vault>/<secret>` URIs for secret-bearing fields via the already-implemented `sigantry_core.auth.keyvault.resolve_secret`. That lets consumers keep `.env.live` empty of secrets and reference KV inline in the TOML instead.

## 6. Shortest path from nothing to running

```bash
cd /mnt/Storage/Insync/olusanmi_18th@hotmail.com/OneDrive/Documents/HS2/HS2_PROJECTS_2025/3_DATAOPS_FABRIC_2026

# One-time
make conda-create
conda activate fabric-dataops-toolkits
make install-dev

# Sanity
python -c "from sigantry_core import FabricDataOps; print(FabricDataOps.__module__)"
sigantry doctor

# Exploration with zero creds
python -c "
from sigantry_core import FabricDataOps
from sigantry_core.testing.doubles import InMemoryTelemetrySink, FakeDeployProfile
from sigantry_core.protocols import DeployContext
fdo = FabricDataOps(telemetry=InMemoryTelemetrySink(), deploy_profile=FakeDeployProfile())
fdo.emit('first', {'note': 'hello'})
print(fdo.deploy(DeployContext(workspace_id='demo', environment='dev')))
"
```

From here branches diverge by goal:

- **Just learning** — keep hitting the in-memory doubles. Fast iteration, no cloud.
- **Deploying something real** — write `.fabric-dataops.toml` (§5 Path B), populate with the HS2 plugin's `aims` profile, call `fdo.deploy(...)` against a real workspace.
- **Live integration suite** — provide `HS2_FABRIC_TEST_*` creds, run `sigantry-hs2-livecheck`, then `PYTEST_RUN_INTEGRATION=1 pytest tests/integration/ -v`. See `sigantry-hs2/docs/live-testing.md` for the env-var surface and role matrix.

---

## 7. Common recipes

### 7.1 Emit one telemetry event to Log Analytics

```python
from sigantry_core import FabricDataOps

fdo = FabricDataOps.from_config(".fabric-dataops.toml")
fdo.emit("deploy_started", {
    "correlation_id": "run-2026-04-22-001",
    "workspace": "<workspace-guid>",
    "principal": "pipeline-sp",
    "environment": "PROD",
})
```

Requires the HS2 plugin installed and a DCR whose stream schema matches `LogAnalyticsSink._to_row` (`sigantry-hs2/sigantry_hs2/telemetry/log_analytics_sink.py`). Use `bicep/modules/dcr-telemetry.bicep` to provision a compatible DCR for testing.

### 7.2 Run a DQ gate and bail if it fails

```python
from sigantry_core import FabricDataOps
from sigantry_core.protocols import DataRef

fdo = FabricDataOps.from_config(".fabric-dataops.toml")
result = fdo.run_dq_gate(
    "silver_checks",
    DataRef(name="silver", path="abfss://data@acct.dfs.core.windows.net/silver/"),
)
if not result.success:
    raise SystemExit(f"DQ gate failed: {result.raw}")
```

### 7.3 Context-managed deploy (deterministic resource cleanup)

```python
from sigantry_core import FabricDataOps
from sigantry_core.protocols import DeployContext

with FabricDataOps.from_config(".fabric-dataops.toml") as fdo:
    fdo.deploy(DeployContext(workspace_id="<guid>", environment="PROD", parameters={...}))
# fdo.__exit__ calls close() on every Closeable seam (e.g. LogAnalyticsSink.close).
```

### 7.4 Quick workspace inventory

```bash
sigantry workspace list
sigantry workspace list-items <guid>
sigantry rbac-audit --workspace-id <guid> --out-dir ./access-reviews
```

More recipes land here as real usage patterns emerge.

---

## 8. Troubleshooting

### `sigantry doctor` reports a plugin with `status != ok`

The plugin's entry point failed to import. Run `doctor` with `--strict` (fails CI), read the Module / Status columns, and check the plugin is installed (`pip list | grep sigantry-core-`). For dependency failures, verify the plugin's `pyproject.toml` dependencies are actually resolvable in your env.

### `ModuleNotFoundError: freezegun` / `respx`

You installed with `[dev]` but not `[test]`. Fix:

```bash
pip install -e ".[dev,test]"    # or: make install-dev
```

### `conda env create` complains env already exists

```bash
make conda-recreate             # or: conda env remove + re-create
```

### `DefaultAzureCredential` chain fails

Run `diagnose-auth` — it shows which credential in the chain worked (or all failed) and distinguishes 401 (token) from 403 (API not enabled for the principal).

### Live integration tests silently skip

You haven't set `PYTEST_RUN_INTEGRATION=1`, or a required `HS2_FABRIC_TEST_*` env var is absent. Run `sigantry-hs2-livecheck` first — it prints the exact missing vars.

### `LogsIngestionClient.upload` returns 400

DCR schema does not match what the sink writes. Reprovision the DCR with `bicep/modules/dcr-telemetry.bicep`, whose stream schema matches `_to_row` exactly.

### `LogsIngestionClient.upload` returns 403

The identity lacks `Monitoring Metrics Publisher` on the DCR:

```bash
az role assignment create \
  --assignee <principal-id> \
  --role "Monitoring Metrics Publisher" \
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Insights/dataCollectionRules/<dcr-name>"
```

More entries land here as real incidents surface.

---

## 9. Open questions / known rough edges

Living list — add items we want to fix but aren't blockers today.

- **Per-stream schema divergence** between the HS2 plugin's `LogAnalyticsSink` (6 columns regardless of stream) and the aspirational `bicep/modules/data-collection.bicep` (richer per-stream schemas). Today, use `bicep/modules/dcr-telemetry.bicep` for testing. Post-v2.0.1: decide whether to enrich the sink or simplify the prod DCR.
- **`sigantry-core-std` companion wheel** — generic implementations (plain `LogAnalyticsSink`, `DefaultAzureAuth`, `FabricCicdDeployProfile`) so non-HS2 consumers don't have to install the HS2 plugin just to get standard behaviour. Candidate for v2.2.
- **`sigantry init <path>` scaffold command** — generates `.fabric-dataops.toml` + sample inline `DeployProfile` + ready-to-run pytest. Candidate for v2.2.
- **ADR-0004 `api_version` compatibility policy** — still open; originally scoped to the six v2 protocol seams, now spans all 11.
- **AIMS + DQ adoption** — neither sibling repo currently consumes this toolkit at the code level (the AIMS notebooks are manifest-governed as of 2026-06-11). Execution plan with falsifiable gates: [`ADOPTION-PLAN.md`](operator/ADOPTION-PLAN.md).

---

## See also

- `docs/tutorials/index.md` — ten verified, hand-holding worked examples (start here if you learn by doing)
- `README.md` — project overview, layout, conventions
- `docs/getting-started/install.md` — install walkthrough (linked from §2)
- `docs/getting-started/quickstart.md` — minimal 5-minute example
- `docs/reference/protocols.md` — protocol seam contracts (for plugin authors)
- `docs/reference/observation-planes.md` — business telemetry vs audit
- `docs/reference/thread-safety.md` — threading model for plugin authors
- `docs/migration/1.x-to-2.0.md` — consumer-pattern reference
- `sigantry-hs2/docs/live-testing.md` — HS2 live-Azure testing
- `CHANGELOG.md` — version history
- `HANDOFF.md` — current session state
