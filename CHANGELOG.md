# Changelog

All notable changes to **Sigantry** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed
- **Shipped templates and workflows told consumers to `pip install
  sigantry-core`, which 404s.** The distribution is `sigantry`
  (`pyproject.toml` declares it; `sigantry-core` has never existed on PyPI —
  ADR-0017 records the amendment to ADR-0011). Every consumer following a
  shipped ADO step template, starter workflow or demo quickstart hit a package
  that is not there. 49 references corrected across `templates/`,
  `.github/workflows/`, `scripts/` and `.pre-commit-config.yaml`. Because the
  old name resolves for nobody, this fix cannot break an existing install.
- `sigantry --help` announced the tool as "Fabric DataOps Toolkit", a name the
  project left behind in v3.0, and `sigantry doctor` titled its plugin table
  "sigantry-core plugins".
- A broken link in the demo quickstart pointed at
  `github.com/sigantry/sigantry-core`, which does not exist.
- `tests/demo/test_demo_quickstart.py` *required* the string `sigantry-core`
  to appear in the demo quickstart, pinning the dead name in place. The test
  now requires the real distribution name.

### Added
- `tests/ci/test_distribution_name.py` keeps the shipped surface — templates,
  workflows, scripts and the package — free of the dead distribution name, so
  it cannot creep back. It reads the expected name from `pyproject.toml`
  rather than hardcoding it, and its one carve-out (the ADO artifact
  identifier `sigantry-core-wheel`) is itself guarded by a test asserting the
  carve-out is still in use.

### Known remaining
- 16 `pip install` / dependency lines under `docs/` still name the dead
  distribution. They are prose rather than shipped artefacts and are tangled
  with a separate version-scheme inconsistency (docs say `>=3.0`, the shipped
  line is 1.0.x), so they are deliberately left for their own change rather
  than half-corrected here.
- The ADO artifact identifier `sigantry-core-wheel` and the template parameter
  `fabricDataopsVersion` are public interface names. Renaming them breaks
  consumer pipelines that reference them, so both need a deprecation window
  rather than a find-and-replace.

## [1.0.0] - 2026-09-19

### Added
- **Initial Open-Source Release** of Sigantry as a standalone Python library on PyPI.
- **Pre-Deployment Safety Probes (`sigantry preflight`)**:
  - Non-destructive simulation engine evaluating Schema Syntax, Dependency DAG order, Entra ID scope permissions, and Fabric capacity active state prior to execution (ADR-0015).
  - Human-friendly colored terminal tables, machine-readable JSON (`--json`), and `--fail-on-warning` flags.
- **Bulk Publishing Concurrency Acceleration**:
  - `--bulk` parallel execution flag on `sigantry deploy run` and `sigantry sync apply`, publishing items through a multi-worker thread pool (`max_workers=4`, not currently configurable) instead of serially.
- **Standalone Interactive HTML Reports (`sigantry_core.reports`)**:
  - Zero-dependency, self-contained HTML reports featuring responsive dark/light themes, summary metrics cards, and instant client-side search/filtering.
  - Interactive drift reports via `sigantry diff --output html --html-out <path>`.
  - Interactive release inspection and comparison via `sigantry release show/diff --html --html-out <path>`.
- **TMDL Semantic Model Breaking Change Impact Guard**:
  - Deep TMDL syntax parsing in `sigantry pr-bot` detecting dropped tables, columns, measures, and model relationships.
  - `--fail-on-breaking` CI/CD gate surfacing prominent alert banners in PR reviews and preventing accidental breaking schema deployments.
- **Dual-Mode Workspace Lifecycle**:
  - Declarative greenfield workspace bootstrapping (`sigantry workspace bootstrap`) with probe-before-act convergence and `BootstrapRecord` audit.
  - Lossless brownfield workspace adoption (`sigantry sync pull`) reverse-engineering live Fabric workspaces into local code and `sync.yml`.
- **Sync Engine (`sigantry sync`)**:
  - Folder-aware item synchronization with `--with-publish` (wrapping `fabric-cicd`) and `--republish-existing`.
  - Staged `.platform` v2 packaging with LF line-ending normalization for Notebooks, Pipelines, Semantic Models, Reports, and Spark Job Definitions.
- **Drift Detection (`sigantry diff`)**:
  - Continuous topology comparison between committed manifests and live Fabric workspaces.
  - Rich color-coded terminal tables and SemVer-pinned JSON output (`--fail-on-drift` CI alerting).
- **Deployment & Automated Rollback (`sigantry deploy`)**:
  - Forward deployments with topological dependency ordering and `$ENV:` parameter substitution.
  - One-command release rollback (`--rollback --to-release <release-id> --rollback-force`) restoring historical item states.
- **Audit & Provenance Ledger (`sigantry release` & `governance.audit`)**:
  - Integrity-checked, append-only JSONL ledgers with SHA-256 hash chains (unkeyed and
    unanchored - see docs/reference/audit-ledger-threat-model.md for what that resists).
  - Independent chain verification CLI command (`sigantry release verify`).
  - Work-item linkage linking releases to GitHub Issues or Azure DevOps work items.
- **Headless PR-Review Bot (`sigantry pr-bot`)**:
  - Automated PR-review bot diffing Power BI TMDL semantic models and Lakehouse schemas on pull requests.
  - Native support for both GitHub Actions and Azure DevOps Pipelines.
- **Fabric Item Controls (`sigantry fabric-item`)**:
  - Folder duplication with automatic `logicalId` regeneration (`fabric-item copy`).
  - Cross-workspace notebook environment and lakehouse re-binding (`fabric-item set-binding`).
- **Environment Management (`sigantry env`)**:
  - Upgrade-safe wheel reconciliation (`env reconcile`) blocking on remote Spark cluster image build.
- **11 Protocol Seams**:
  - `DeployProfile`, `DataQualityGate`, `TelemetrySink`, `AuthProvider`, `RunbookRegistry`, `CapacityPolicy`, `WorkItemProvider`, `NotificationSink`, `SecretStore`, `ApprovalGate`, and `PrReviewBot`.
