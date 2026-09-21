# Changelog

All notable changes to **Sigantry** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `tests/ci/test_distribution_name.py` keeps the shipped surface — templates,
  workflows, scripts and the package — free of the dead distribution name, so
  it cannot creep back. It reads `pyproject.toml` as a *precondition* — the
  scan is meaningless if the declared name ever stops being `sigantry` — but
  the name it polices (`_DEAD_DIST`) is a literal, so a future rename means
  editing the guard, not just `pyproject.toml`. Its one carve-out (the ADO
  artifact identifier `sigantry-core-wheel`) is itself guarded by a test
  asserting the carve-out is still in use.

### Changed
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
  to appear in the demo quickstart, pinning the dead name in place. Replacing
  that token with `"sigantry"` would have been vacuous — three CLI commands
  already in the same list (`sigantry config validate`, `sigantry sync apply`,
  `sigantry diff`) each contain that substring, so the assertion would be true
  on every possible input, `pip install sigantry-core` included. The check is
  now the absence of the dead name plus a real `pip install` line.
- The test.pypi.org upload step in `release-alpha.yml` was relabelled
  `sigantry` while its twine glob still read `dist/sigantry_core-*`. Measured
  against a real `python -m build`: the artifacts are
  `sigantry-1.0.0-py3-none-any.whl` / `sigantry-1.0.0.tar.gz`, and the old
  glob expands to nothing, so bash passes the literal to twine and the release
  step fails.
- Shipped quickstart templates told adopters to run
  `pip install "sigantry>=3.0.0"`, which cannot resolve against the shipped
  1.0.x line, and claimed `requires-python = ">=3.11,<3.13"` refuses 3.13 when
  `pyproject.toml` declares `>=3.11` with no upper bound.
- Dead `github.com/sigantry/...` links (the org returns 404) remained in the
  demo quickstart and the shipped demo template after a sibling link in the
  same file was corrected.

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
- 15 `pip install` / dependency lines across 9 files under `docs/` still name
  the dead distribution. They are prose rather than shipped artefacts and are
  tangled with a separate version-scheme inconsistency (docs say `>=3.0`, the
  shipped line is 1.0.x), so they are deliberately left for their own change
  rather than half-corrected here. Two of them must survive any such change:
  ADR-0017 quotes the dead name to explain the defect, and ADR-0011 records it
  as history.
- `requirements-lock.txt` carries 21 `# via sigantry-core (pyproject.toml)`
  annotations. pip-compile writes the project's own name into those comments,
  so they are evidence the lock has not been regenerated since `pyproject.toml`
  became `name = "sigantry"`. They are comments and do not affect resolution,
  but regenerating the lock belongs with the dependency work, not here.
- The guard scans `templates/`, `.github/workflows/`, `scripts/` and the
  package. It does **not** scan `requirements-lock.txt`, `pyproject.toml`,
  `environment.yml`, `README.md` or `CONTRIBUTING.md`, so the dead name could
  reappear in those without failing CI.
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
