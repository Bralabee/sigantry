# Sigantry

[![PyPI version](https://img.shields.io/pypi/v/sigantry.svg)](https://pypi.org/project/sigantry/)
[![Python versions](https://img.shields.io/pypi/pyversions/sigantry.svg)](https://pypi.org/project/sigantry/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/Bralabee/sigantry/blob/main/LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Sigantry** is an enterprise-grade governance, drift detection, and rollback engine for Microsoft Fabric CI/CD pipelines.

Built by **JToye Digital**, Sigantry wraps Microsoft's official deployment tooling (`fabric-cicd`, `fab` CLI, and Fabric REST APIs) rather than replacing them. While Microsoft owns the automation lane, Sigantry provides the mission-critical governance layer enterprise platform teams require: **integrity-checked deploy ledgers, release rollback, scheduled drift detection, destructive-operation gating, and headless PR-review bots**.

---

## Key Capabilities

- 🚀 **Pre-Deployment Safety Probes**: `sigantry preflight` runs four-phase non-destructive simulations (Schema Syntax, Dependency DAG, Entra ID Scope, Capacity State) before deploying.
- 🔄 **Brownfield Adoption**: `sigantry sync pull` introspects a hand-built Fabric workspace and generates a `sync.yml` manifest and code tree from it.
- 🏗️ **Declarative Greenfield Scaffolding**: `sigantry workspace bootstrap` provisions brand-new workspaces, capacity bindings, and medallion folder blueprints from a single `workspace.yml` manifest with idempotent probe-before-act convergence.
- ⚡ **Concurrent Bulk Publishing**: `--bulk` publishes items through a parallel worker pool instead of one at a time, cutting wall-clock deploy time on multi-item repositories.
- 🛡️ **Release Rollback**: `sigantry deploy run --rollback --to-release <id> --rollback-force` publishes again the items a recorded release names whose type is in `--item-types` (by default `Lakehouse`, `Environment`, `Notebook` and `DataPipeline`), taking their content from the `--source` checkout you pass. The ledger records item names, not item content or a commit, so check out the source you want to restore before you run it.
- 🔍 **Interactive Drift Detection**: `sigantry diff` compares a live Fabric workspace with a Git manifest at the moment it runs, by item name, type and folder (not item content), and outputs rich CLI tables, SemVer JSON, or standalone interactive HTML reports (`--output html`). To check on a schedule, call the reusable drift workflow from a scheduled workflow of your own.
- 🛑 **TMDL Breaking Change Impact Guard**: `sigantry pr-bot run --fail-on-breaking` intercepts Power BI semantic model edits in CI/CD, highlighting dropped measures, columns, and tables before downstream reports break.
- 📜 **Integrity-Checked Audit Ledger**: SHA-256 hash-chained JSONL records (`DeployRecord`, `BootstrapRecord`). `sigantry release verify` checks the deploy ledger's chain; no `sigantry` command checks the bootstrap ledger's chain. The chain is **unkeyed and unanchored**: it detects accidental corruption, unsealed edits and middle-record deletion, but anyone who can write the ledger file can re-seal it, and tail truncation is undetectable without an external anchor. Read the [audit ledger threat model](https://github.com/Bralabee/sigantry/blob/main/docs/reference/audit-ledger-threat-model.md) before treating the ledger as evidence against an insider.
- 🔌 **11 Protocol Seams**: Pluggable architecture supporting custom notification sinks (Teams, Slack, Email), secret stores (Key Vault, GitHub, ADO), approval gates (OPA, ADO, GHA), and data quality gates.

---

## 60-Second Quickstart

### 1. Installation

```bash
pip install sigantry
```

> [!IMPORTANT]
> **The config names changed after 1.0.0.** Since 1.0.1, `sigantry` reads the config file
> `.sigantry.toml` and settings overrides named `SIGANTRY_<SECTION>__<KEY>`, the names
> these docs use. `sigantry` 1.0.0 looks for neither: given no path (the CLI, or
> `FabricDataOps.from_config()` with no argument) it looks only for `.fabric-dataops.toml`,
> and it reads settings overrides as `FDT_<SECTION>__<KEY>`, not `SIGANTRY_<SECTION>__<KEY>`.
> To see which version you have, run `pip show sigantry`.
>
> - **On 1.0.0,** upgrade with `pip install --upgrade sigantry`. Until you do, name the file
>   `.fabric-dataops.toml` (its contents are the same), or pass its path explicitly: 1.0.0
>   reads an explicit path under any name, and skips a missing one without a message.
>   Write settings overrides as `FDT_<SECTION>__<KEY>`, for example `FDT_CORE__TENANT_ID`.
>   Keep every other `SIGANTRY_` variable under its documented name: 1.0.0 reads
>   `SIGANTRY_TRUSTED_PLUGIN_DISTS`, `SIGANTRY_NOTIFICATION_SINK`, the webhook and
>   `SIGANTRY_SMTP_*` variables and `SIGANTRY_DRIFT_WORKSPACE_ID` under those names, and
>   the code that uses them ignores an `FDT_` spelling.
> - **On 1.0.1 or later,** rename the file to `.sigantry.toml` and keep only that one,
>   change any path you pass explicitly, such as `from_config(".fabric-dataops.toml")`, to
>   the new name, and rename the overrides to `SIGANTRY_`. The old names are still read
>   during a deprecation period, and through 1.0.x an old name wins over a new one: while
>   both config files exist and differ, `.fabric-dataops.toml` is the file read.
>   `from_config()` reports an old name through a `DeprecationWarning`, which Python shows
>   by default only when the script being run made the call; the `sigantry` commands do not
>   print it. The migration guide's Verify step checks for old names.

Verify the installation:

```bash
sigantry --help
sigantry doctor
```

> [!TIP]
> **Troubleshooting: `sigantry: command not found`**
> If your terminal reports `sigantry: command not found` immediately after installation:
> - **Refresh Shell Hash Table:** In an active bash/zsh session, run `hash -r` (or `rehash` in zsh) to update executable lookup.
> - **Check Environment PATH:** Ensure your virtualenv/conda environment is active (`conda activate <env-name>` or `source .venv/bin/activate`), and that your Python `bin/` directory is in `$PATH`.
> - **Direct Execution:** You can always invoke the CLI directly via Python: `python -m sigantry_core.cli --help`.

### 2. Adopt an Existing Workspace (Brownfield)

Bring an existing, hand-built Fabric workspace under version-controlled manifest management:

```bash
sigantry sync pull --workspace-id "<YOUR-WORKSPACE-GUID>" --into ./adopted
```

Inspect what was generated:

```bash
head -25 ./adopted/sync.yml
```

Check that re-applying the pulled manifest changes nothing (the dry run should report 0 folders created and 0 items moved):

```bash
sigantry sync apply --manifest ./adopted/sync.yml --workspace-id "<YOUR-WORKSPACE-GUID>" --dry-run
```

### 3. Detect Drift

Check if anyone has modified, added, or deleted items out-of-band in the Fabric portal:

```bash
sigantry diff --manifest ./adopted/sync.yml --workspace-id "<YOUR-WORKSPACE-GUID>" --fail-on-drift
```

### 4. Bootstrap a Fresh Workspace (Greenfield)

Author a `workspace.yml` blueprint:

```yaml
schema_version: "1.0"
workspace:
  name: "analytics-prod"
  description: "Production Fabric Workspace"
  capacity_id: "<YOUR-CAPACITY-GUID>"
folders:
  blueprint: medallion
git:
  enabled: false
```

Bootstrap with probe-before-act convergence:

```bash
sigantry workspace bootstrap workspace.yml --dry-run
sigantry workspace bootstrap workspace.yml
```

---

## Documentation

Full documentation, architecture guides, and step-by-step tutorials are available at:
👉 **[https://github.com/Bralabee/sigantry/tree/main/docs](https://github.com/Bralabee/sigantry/tree/main/docs)**

---

## License

Sigantry is licensed under the [Apache License, Version 2.0](https://github.com/Bralabee/sigantry/blob/main/LICENSE).
