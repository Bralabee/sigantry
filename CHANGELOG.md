# Changelog

All notable changes to **Sigantry** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **CI now runs `mypy`.** The project has configured mypy under `[tool.mypy]`
  since before v1.0.0 and no workflow ever invoked it, so it reported nothing
  for as long as that was true — including a real `attr-defined` bug in
  `scripts/ci/check-no-sys-path.py` that crashed the guard on any malformed
  `.py` file. Scoped to `sigantry_core/`, which is clean today, so the job
  starts green and any regression belongs to the PR that caused it.

  Note the scope honestly: `sigantry_core/` does **not** include `scripts/`,
  so this job would *not* have caught that bug. It is cited as evidence that
  an unrun type checker reports nothing, not as something this job now
  prevents. Extending the scope is recorded under *Known remaining*.
- `tests/ci/test_quality_gates_run.py` asserts that a quality tool the project
  configures is actually invoked by CI, and that the artifact build depends on
  every quality job. A configured-but-unrun tool is worse than an absent one:
  the config advertises a gate that does not exist.

### Changed
- `ruff` now covers `scripts/` in CI alongside `sigantry_core/` and `tests/`.
  The CI guard scripts — the files whose whole job is policing the repo — were
  themselves unlinted. They were already clean; this stops that drifting.
- The artifact build continues to depend on lint and test. It deliberately does
  **not** depend on the new type-check job while `Type Check (mypy)` is not a
  required status check on `main`: a job skipped because a dependency failed
  still reports a check run, and GitHub counts a skipped run as satisfying its
  required context, so the dependency would hand branch protection a green
  `Build & Verify Artifacts` on a tree that failed type-checking.

- **Config surface renamed to match the product (ADR-0011, V3.X-ROADMAP
  LEGACY-SURFACE-DROP item 2).** `load_settings()` and
  `FabricDataOps.from_config()` now resolve `.sigantry.toml` by default, and
  settings env overrides use the `SIGANTRY_<SECTION>__<KEY>` prefix. Before
  this, the documented `.sigantry.toml` filename was read by nothing: an
  operator who followed the migration guide got a config file that was
  silently ignored and a run on all defaults.

### Deprecated
- `.fabric-dataops.toml` and the `FDT_` settings env prefix. Both are still
  read for one more minor release and each emits a `DeprecationWarning` naming
  its replacement. Where a setting is supplied under both prefixes, `SIGANTRY_`
  wins.

### Fixed
- `sigantry sync` no longer swallows a config-load failure in silence. An
  unreadable or malformed config is logged as a warning saying the command is
  continuing on defaults, instead of a bare `except Exception` that left the
  operator with no signal their settings were never applied.
- `load_settings`' docstring claimed a missing config file raised
  `ValidationError`. It never did — no settings field is required — so the
  documented fail-fast did not exist. The docstring now states the real
  behaviour and says who is responsible for checking.

### Security
- Settings env overrides are now restricted to `<PREFIX><SECTION>__<KEY>` forms
  whose section names a real settings field, and **no** model in the tree
  enables pydantic-settings' own env source. `SIGANTRY_` is shared with ~70
  operational variables, several of them credentials
  (`SIGANTRY_SMTP_PASSWORD`, `SIGANTRY_GITHUB_TEST_PAT`,
  `SIGANTRY_FABRIC_TOKEN`). Because `ToolkitSettings` allows extra fields, an
  unfiltered sweep under the new prefix would have bound those onto the
  settings object and exposed them through `model_dump()`.
- **Unprefixed environment variables no longer bind to settings.** Dropping the
  env source on the root model closed only one of fourteen: each seam section is
  a `Field(default_factory=...)`, and while those sub-models were `BaseSettings`
  with no `env_prefix`, every factory call ran an env source that matched BARE
  names. Measured before the fix: `TENANT_ID` bound to `core.tenant_id`,
  `PROVIDER` to `auth.provider`, `REGISTRY` to `runbooks.registry` — so a CI
  runner exporting `REGISTRY` for a container registry silently populated
  settings the operator never wrote. The sub-models are now plain `BaseModel`.
- A scalar env override aimed at a dict-typed field
  (`SIGANTRY_RELEASE__GITHUB`, `SIGANTRY_RELEASE__ADO`,
  `SIGANTRY_RUNBOOKS__STATIC_MAP`) raised `ValidationError` out of *every*
  settings load for as long as the variable stayed exported. It is now skipped
  with a warning. `SIGANTRY_CORE__` (empty trailing segment) likewise cleared
  the length guard and wrote an empty-string key onto the section.
- `sigantry sync` crashed rather than warned on a config file containing a
  non-UTF-8 byte: `tomllib.load` decodes the file itself, so it raises
  `UnicodeDecodeError`, which is caught by neither `OSError` nor
  `TOMLDecodeError`.

### Known remaining
- `mypy` covers `sigantry_core/` only. Measured on this branch, `mypy scripts/`
  reports **8 errors in 5 files** (including the `attr-defined` bug above), so
  widening the scope is a change with real work behind it, not a one-word edit.
  `ruff` does now cover `scripts/`; `mypy` does not.
- The type-check job gates nothing until `Type Check (mypy)` is added to the
  required status checks on `main`. It cannot be required before it exists on
  `main`, so this is the immediate follow-up to merging this change.
- The CI `build` job gates only the CI `dist` artifact. `publish-pypi.yml`
  triggers on `release: published` and runs build → `twine check` → publish
  with no dependency on lint, test or types, so the wheel users actually
  install is not gated by any of them. Closing that is a separate change.


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
