# Usage Handbook

**Last updated:** 2026-09-19 (v1.0.0 open-source debut)
**Toolkit version:** 1.0.0

This is the living, day-to-day usage reference for `sigantry`. It covers the scenarios the toolkit is designed for, the environment you work in, the complete CLI surface across all 18 subapps, configuration patterns, and common recipes.

If you're brand new: start at §2 (ground zero) and run through the "shortest path from nothing to running" in §6. Everything else is reference material you dip into when you need it.

---

## 1. Scenarios — when to reach for Sigantry

From the 18 wired CLI subapps (`sigantry_core/cli.py`) plus the `diagnose-auth` console script (`pyproject.toml`) and the 11 protocol seams in `sigantry_core/protocols.py`.

| You're doing… | Tool you'd use |
|---|---|
| Simulating pre-deployment safety, validating syntax, DAG dependencies, Entra scopes, and capacity state | `sigantry preflight` |
| Listing, creating, or deleting a Fabric workspace; assigning to capacity; greenfield scaffolding | `sigantry workspace` |
| Pausing, resuming, or scaling an F-SKU Fabric capacity | `sigantry capacity` |
| Publishing a Git tree of `.platform` items to a Fabric workspace (with `--bulk` parallel workers or `--rollback`) | `sigantry deploy` |
| Connecting / disconnecting Fabric ↔ ADO Git integration | `sigantry git` |
| Uploading a wheel to a Fabric Environment (one target or multi-environment sync) | `sigantry env` |
| Copying a Fabric item with `logicalId` regeneration | `sigantry fabric-item copy` |
| Attaching an Environment or default Lakehouse to a notebook | `sigantry fabric-item set-binding` |
| RBAC audit, sensitivity-label sync, tenant-settings baseline | `sigantry rbac-audit`, `sigantry label-sync`, `sigantry tenant-settings` |
| Running a DQ gate before a deploy step | `sigantry dq` |
| Variable Library CRUD | `sigantry variable-library` |
| "Which plugins are installed, and did any fail to import?" | `sigantry doctor` |
| "Why is auth failing — 401 or 403?" | `diagnose-auth` |
| Release records — `record` / `list` / `show` / `diff` with an integrity-checked audit hash ([threat model](reference/audit-ledger-threat-model.md)) and `--html` reports | `sigantry release` |
| Lossless Fabric workspace round-trip adoption and local sync (`apply`, `pull`, `snapshot`, `--bulk`) | `sigantry sync` |
| Scheduled or CI drift detection with CLI table, SemVer JSON, or standalone interactive HTML (`--output html`) | `sigantry diff` |
| Headless PR review bot diffing TMDL and schemas, with breaking change guards (`--fail-on-breaking`) | `sigantry pr-bot` |
| Validating Sigantry configuration files (e.g. `parameters.yml` schemas) | `sigantry config` |

**Explicitly not for**: Replacing Microsoft's native deployment engine, custom lineage graphing, ad-hoc DAX authoring, or replacing Azure Monitor. Sigantry sits directly on top of Microsoft's official APIs to deliver enterprise-grade governance, safety, and operational resilience.

---

## 2. Ground zero — your environment

**Conda or venv are both first-class environment mechanisms.**

- **Python:** 3.11+ (Fabric notebook runtime parity; `pyproject.toml` pins `>=3.11`)

### Setup with Conda

```bash
git clone https://github.com/Bralabee/sigantry.git
cd sigantry

conda create -n sigantry-dev python=3.11 -y
conda activate sigantry-dev
pip install -e ".[dev,test]"
```

### Setup with venv

```bash
git clone https://github.com/Bralabee/sigantry.git
cd sigantry

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"
```

### Sanity check

```bash
sigantry --help
sigantry doctor
```

!!! tip

    If your terminal displays `sigantry: command not found` after installing via `pip`:

    - Run `hash -r` in Bash (or `rehash` in Zsh) to refresh the shell command table.
    - Ensure the virtualenv/conda bin folder is in your `$PATH`.
    - Alternatively, run `python -m sigantry_core.cli <command>`.

---

## 3. CLI Surface — All 18 Subapps

### Main CLI

```bash
sigantry --help
# or, when the script isn't on PATH:
python -m sigantry_core --help
```

18 subapps (every one accepts `--help`):

| Subapp | Purpose |
|---|---|
| `preflight` | Pre-deployment safety probe engine — non-destructive simulations (Syntax, DAG, Entra Scope, Capacity) |
| `workspace` | Fabric workspace CRUD + items + capacity assignment + greenfield `bootstrap` |
| `capacity` | Fabric capacity inspection + lifecycle (pause/resume via ARM) |
| `label-sync` | Apply a sensitivity label to every item in a workspace |
| `rbac-audit` | Emit workspace + capacity + item RBAC audit |
| `tenant-settings` | Export Fabric admin tenant-settings baseline |
| `deploy` | Deploy Fabric items via `fabric-cicd`; supports `--bulk` and `--rollback --to-release <id>` |
| `fabric-item` | Fabric item folder operations: `copy` (with logicalId regeneration) and `set-binding` (attach an Environment and/or default Lakehouse to a notebook) |
| `git` | Fabric workspace ↔ ADO Git integration (7-endpoint surface) |
| `variable-library` | Fabric Variable Library CRUD |
| `env` | Fabric Environment wheel upload, sync, and reconcile |
| `dq` | Run a registered DQ gate plugin against a dataset |
| `doctor` | List discovered plugins and diagnose entry-point import failures |
| `release` | Sigantry release records — `record`, `list`, `show`, `diff`, and `--html` reports with cryptographic audit hash |
| `sync` | Local ↔ Fabric folder-aware sync engine — `apply` (supports `--bulk`), `pull`, `snapshot` |
| `diff` | Drift detection between local `sync.yml` and live Fabric workspace (human, json, or `--output html`) |
| `config` | Validate Sigantry configuration files (e.g. `config validate parameters.yml`) |
| `pr-bot` | PR-review bot — diffs TMDL + schemas, comments on PRs, guards with `--fail-on-breaking` |

---

## 4. Key Open-Source Features in v1.0.0

### 4.1 Pre-Deployment Safety Probes (`sigantry preflight`)

Execute non-destructive pre-deployment simulations before triggering any live changes:

```bash
# Run preflight against target environment
sigantry preflight --manifest sync.yml --environment prod

# Machine-readable JSON output for CI pipelines
sigantry preflight --manifest sync.yml --environment prod --json

# Treat warnings as failures (the CI gate)
sigantry preflight --manifest sync.yml --environment prod --strict
```

The preflight engine runs 4 progressive probes:
1. **Schema Syntax**: Validates manifest schema and item declarations.
2. **Dependency Graph**: Detects circular dependencies and validates DAG order.
3. **Entra Scope**: Validates authentication tokens and required RBAC scopes.
4. **Capacity State**: Confirms target Fabric capacity is active and not paused.

### 4.2 Bulk Publish Acceleration (`--bulk`)

Drastically accelerate deployments on workspaces with multiple items using parallel thread pools:

```bash
# Parallel deploy
sigantry deploy run --workspace-id "<GUID>" --environment prod --bulk

# Parallel sync apply
sigantry sync apply --manifest sync.yml --workspace-id "<GUID>" --bulk
```

### 4.3 Standalone Interactive HTML Reports

Generate zero-dependency, self-contained interactive reports featuring dark/light modes, summary cards, and instant client-side filtering:

```bash
# Interactive drift report
sigantry diff --manifest sync.yml --workspace-id "<GUID>" --output html --html-out drift-report.html

# Interactive release record inspection
sigantry release show rel-2026-09-19-1 --html --html-out release-rel-1.html

# Interactive release comparison
sigantry release diff rel-2026-09-19-1 rel-2026-09-19-2 --html --html-out release-diff.html
```

### 4.4 TMDL Semantic Model Breaking Change Guard (`--fail-on-breaking`)

Protect downstream Power BI reports and dashboards from breaking changes by intercepting dropped tables, columns, measures, or relationships in CI/CD:

```bash
# Guard CI build from breaking semantic model changes
sigantry pr-bot run --provider github --fail-on-breaking
```

---

## 5. Configuration Patterns

### Direct Dependency Injection (In-Memory / Testing)

Zero config files, zero cloud auth. Use the in-memory doubles:

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

### Declarative Config (`.fabric-dataops.toml`)

Create `.fabric-dataops.toml` at repo root:

```toml
[core]
tenant_id = "<your-entra-tenant-guid>"

[deploy]
profile = "standard"

[dq]
gate = "standard_gate"

[telemetry]
sink = "in_memory"
```

Load and execute:

```python
from sigantry_core import FabricDataOps
from sigantry_core.protocols import DeployContext

fdo = FabricDataOps.from_config(".fabric-dataops.toml")
result = fdo.deploy(DeployContext(
    workspace_id="<workspace-guid>",
    environment="PROD",
    parameters={"items_directory": "./fabric_items"},
))
```

---

## 6. Shortest Path From Nothing to Running

```bash
# 1. Install
pip install sigantry

# 2. Verify health
sigantry doctor

# 3. Pull an existing Fabric workspace (Brownfield adoption)
sigantry sync pull --workspace-id "<YOUR-WORKSPACE-GUID>" --into ./my-fabric-repo

# 4. Check for out-of-band drift
sigantry diff --manifest ./my-fabric-repo/sync.yml --workspace-id "<YOUR-WORKSPACE-GUID>"

# 5. Run preflight simulation
sigantry preflight --manifest ./my-fabric-repo/sync.yml --environment prod
```

---

## 7. Troubleshooting

### `sigantry doctor` reports a plugin with `status != ok`
The plugin's entry point failed to import. Run `sigantry doctor` to inspect the error trace. Verify that required dependencies are installed in your Python environment.

### `DefaultAzureCredential` fails
Run `diagnose-auth` to inspect which credential in the chain was evaluated and whether the issue is missing authentication (401) or permissions (403).

### Drift report exits 1 in CI
If `--fail-on-drift` is passed, `sigantry diff` exits with code 1 when any uncommitted changes, additions, or removals are detected on the remote workspace. Review the diff table or HTML report to synchronize Git with the live workspace.

---

## See also

- [Index](index.md) — Documentation home
- [Installation Guide](getting-started/install.md) — Installation walkthrough
- [Quickstart](getting-started/quickstart.md) — 5-minute quickstart
- [Tutorials](tutorials/index.md) — Step-by-step worked examples
