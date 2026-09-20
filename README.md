# Sigantry

[![PyPI version](https://img.shields.io/pypi/v/sigantry.svg)](https://pypi.org/project/sigantry/)
[![Python versions](https://img.shields.io/pypi/pyversions/sigantry.svg)](https://pypi.org/project/sigantry/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Sigantry** is an enterprise-grade governance, drift detection, and rollback engine for Microsoft Fabric CI/CD pipelines.

Built by **JToye Digital**, Sigantry wraps Microsoft's official deployment tooling (`fabric-cicd`, `fab` CLI, and Fabric REST APIs) rather than replacing them. While Microsoft owns the automation lane, Sigantry provides the mission-critical governance layer enterprise platform teams require: **integrity-checked deploy ledgers, automated rollback, scheduled drift detection, destructive-operation gating, and headless PR-review bots**.

---

## Key Capabilities

- 🚀 **Pre-Deployment Safety Probes**: `sigantry preflight` runs four-phase non-destructive simulations (Schema Syntax, Dependency DAG, Entra ID Scope, Capacity State) before deploying.
- 🔄 **Lossless Brownfield Adoption**: `sigantry sync pull` introspects any hand-built Fabric workspace and generates an exact, round-trip lossless `sync.yml` manifest and code tree.
- 🏗️ **Declarative Greenfield Scaffolding**: `sigantry workspace bootstrap` provisions brand-new workspaces, capacity bindings, and medallion folder blueprints from a single `workspace.yml` manifest with idempotent probe-before-act convergence.
- ⚡ **Concurrent Bulk Publishing**: `--bulk` publishes items through a parallel worker pool instead of one at a time, cutting wall-clock deploy time on multi-item repositories.
- 🛡️ **Automated Release Rollback**: `sigantry deploy run --rollback --to-release <id>` re-publishes the exact historical version of items from immutable ledger snapshots.
- 🔍 **Interactive Drift Detection**: `sigantry diff` continuously compares live Fabric workspaces against Git manifests, outputting rich CLI tables, SemVer JSON, or standalone interactive HTML reports (`--output html`).
- 🛑 **TMDL Breaking Change Impact Guard**: `sigantry pr-bot run --fail-on-breaking` intercepts Power BI semantic model edits in CI/CD, highlighting dropped measures, columns, and tables before downstream reports break.
- 📜 **Integrity-Checked Audit Ledger**: SHA-256 hash-chained JSONL records (`DeployRecord`, `BootstrapRecord`) independently verifiable via `sigantry release verify`. The chain is **unkeyed and unanchored**: it detects accidental corruption, unsealed edits and middle-record deletion, but anyone who can write the ledger file can re-seal it, and tail truncation is undetectable without an external anchor. Read the [audit ledger threat model](docs/reference/audit-ledger-threat-model.md) before treating the ledger as evidence against an insider.
- 🔌 **11 Protocol Seams**: Pluggable architecture supporting custom notification sinks (Teams, Slack, Email), secret stores (Key Vault, GitHub, ADO), approval gates (OPA, ADO, GHA), and data quality gates.

---

## 60-Second Quickstart

### 1. Installation

```bash
pip install sigantry
```

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

Prove the round-trip is lossless (should report 0 folders created and 0 items moved):

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
👉 **[https://bralabee.github.io/sigantry](https://bralabee.github.io/sigantry)**

---

## License

Sigantry is licensed under the [Apache License, Version 2.0](LICENSE).
