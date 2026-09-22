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
- **CI now runs `mypy`.** The project has configured mypy under `[tool.mypy]`
  since before v1.0.0 and no workflow ever invoked it, so it reported nothing
  for as long as that was true — including a real `attr-defined` bug in
  `scripts/ci/check-no-sys-path.py` that crashed the guard on any malformed
  `.py` file. It covers `sigantry_core/` **and** `scripts/` — so it does
  now cover the file that carried that bug — and both are clean, so the job
  starts green and any regression belongs to the PR that caused it.

  `tests/` is deliberately out of scope: it fails module resolution before
  type checking begins (duplicate basenames with no `__init__.py`), so
  claiming coverage there would assert something that cannot currently be
  true.
- `tests/ci/test_quality_gates_run.py` asserts that a quality tool the project
  configures is actually invoked by CI, and that the artifact build depends on
  every quality job. A configured-but-unrun tool is worse than an absent one:
  the config advertises a gate that does not exist.

### Changed
- `ruff` now covers `scripts/` in CI alongside `sigantry_core/` and `tests/`.
  The CI guard scripts — the files whose whole job is policing the repo — were
  themselves unlinted. They were already clean; this stops that drifting.
- **The artifact build now depends on every quality job, `types` included.**
  It deliberately did not while `Type Check (mypy)` was unrequired — a job
  skipped because a dependency failed still reports a check run, and GitHub
  counts a skipped run as *satisfying* its required context, so the
  dependency would have handed branch protection a green `Build & Verify
  Artifacts` on a tree that failed type-checking. `Type Check (mypy)` became
  a required context on `main` on 2026-09-21, closing that route, so the
  carve-out is gone. Exemptions now carry a review-by date and fail the
  suite once it passes, because this one outlived its reason in silence.
- **The wheel published to PyPI is now gated by the same lint, type and test
  jobs that gate a pull request.** `publish-pypi.yml` ran checkout → build →
  `twine check` → publish with no quality job in front of it; CI's own
  `build` job gates only the throwaway `dist` artifact that nobody installs.
  `ci.yml` is now callable (`workflow_call`) and the publish job depends on
  it.
- `mypy` in `.pre-commit-config.yaml` moved from `v1.13.0` to `v1.20.2`, the
  version `mypy>=1.19,<2.0` actually resolves to, so the hook and the CI gate
  cannot disagree about what counts as an error.

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

### Removed
- `scripts/ci/mypy_gate.py` and its tests are removed. It was a baseline-ratchet
  gate invoked by nothing, its docstring claimed 63 pre-existing errors in a
  tree that is clean, and the `mypy-baseline.txt` it read never existed. With
  both type-checked roots clean there is nothing to ratchet, and an unrun tool
  that describes a world that no longer exists is exactly what this release is
  removing elsewhere.

### Fixed
- **The assertions guarding "a configured tool must actually RUN" could not
  fail for the reasons that mattered — twice.** Review of the first rewrite
  found it still passed with `if: false` on the mypy step (or a never-matching
  `if:` on the `types` job, which makes it *skipped*, and a skipped run
  satisfies its required context), with `mypy ... | tee mypy.log` (steps run
  under `bash -e {0}` with pipefail OFF, so the step exits with tee's 0), with
  `|| echo`, with `set +e` plus a trailing `exit 0`, and with
  `continue-on-error` on the publish workflow's gating job. "Enforcing" is no
  longer a denylist of neutering suffixes: an invocation counts only when it is
  the whole command, in an unconditional step and job, in a `run:` block that
  neither disables `errexit` nor forces `exit 0`.
- **A third round found the rewrite still defeatable**, each confirmed by arm
  against the file's own helpers: `if: always()` on the *publish* job (the
  enforcement check ran only on the gate it depends on, never on the publisher
  itself); `python -m twine upload` (the scan compared raw tokens to
  `["twine", "upload"]`, missing the `python -m` form the repo already uses for
  `python -m build`); and `mypy ... &`, which backgrounds the tool so the step
  exits on the shell. `test_mypy_covers_every_typed_root` also unioned operands
  across every job, so splitting the roots between two jobs left the *required*
  context checking half the tree. All four are closed and armed.
- Carve-outs are keyed `"<workflow>::<job>"`, not by filename. A filename key
  excused every publish job in that file, including ones added later, on a
  reason recorded about a different job.
- **The publish scan looked at one filename and one action.**
  `release-alpha.yml` publishes via `twine upload` in a job with no `needs:` —
  precisely the defect the test exists to catch, and invisible to it. Every
  workflow is now scanned for both mechanisms. `release-alpha.yml` carries an
  explicit, dated carve-out pointing at #13 (it cannot currently succeed at
  all, so gating it would assert nothing) rather than being silently missed.
- **`ci.yml` became reusable while keeping `group: ci-${{ github.ref }}` with
  `cancel-in-progress`.** Publishing a release fires both `push: tags` on
  `ci.yml` and `release: published` on `publish-pypi.yml`, which calls
  `ci.yml`; in a called workflow `github.ref` is the caller's, so both landed
  in one group and one cancelled the other. If that was the release's gate, the
  publish job is *skipped* and nothing ships — a cancellation, not a red X.
  The group now includes `github.workflow`.
- `twine check` in CI is now `--strict`, matching the publish path. A metadata
  defect that is a warning under one and an error under the other would
  otherwise pass every quality job and surface mid-release.
- A malformed `metrics.json` (a JSON list or scalar) no longer reads as "no
  metrics". `docs_freshness` raises and names the real cause instead of passing
  clean on a broken input or later reporting a claimed metric as "absent".
- **The original finding, for the record.** Measured clean → arms → clean against a
  parsed copy of the real `ci.yml`, the substring checks passed when the whole
  `types` job body was replaced with `pip install mypy ruff` (the string is
  present, nothing executes it), when the mypy step became a comment plus an
  `echo`, when `continue-on-error: true` was added to it, and when both ruff
  steps were rewritten to `--exclude scripts/` — the root is named in the
  command precisely because it is being excluded from it. Only deleting the job
  outright failed them. The assertions now tokenise each `run:` line and ask
  what a shell would execute.
- `mypy` did not cover `scripts/`. All 5 errors it found there are resolved in
  place rather than parked in a baseline file, but be precise about how: three
  are real corrections (a `None`-into-`Module` assignment, and two functions
  returning `Any` where a concrete type was declared), and **two are
  suppressions** — a `# type: ignore[attr-defined]` in `tutorials/render.py`
  and a `bool(...)` wrapper in `audit_chain_migrate.py`. Both suppressions are
  correct code: the `toc` extension really does attach `toc_tokens` at runtime
  and the stubs cannot express it. But a per-line ignore is the same
  accept-a-known-error mechanism `mypy_gate.py` is being deleted for, applied
  inline, and calling it "fixed" would overstate it.

  One of the five was only visible once the stubs were declared: `render.py`
  read `md.toc_tokens` off a `Markdown` instance with no such attribute, which
  the untyped import had been hiding. `types-Markdown` is now a declared dev
  dependency, so local and CI type-check the same tree instead of differing by
  whatever happens to be installed.
- **`CONTRIBUTING.md` documented a test command that hides its own result.**
  `pyproject.toml` sets `-q` in `addopts`, so the documented `pytest -q` becomes
  `-qq`, which suppresses the pass/fail summary entirely: measured `rc=0` with
  no counts at all — indistinguishable from a run that collected nothing, which
  is precisely what the adjacent comment ("expect a non-zero test count, not
  just exit 0") asks the reader to rule out. `CONTRIBUTING.md`,
  `docs/contributing.md`, `docs/release-process.md` and **both** pull-request
  templates now say `python -m pytest`, and name `scripts/` and `mypy` so they
  match the gates that are actually required on `main`. (An earlier draft of
  this entry claimed "all three contributor docs"; review found the two PR
  templates still carrying the `pytest -q` tick-box.)
- **The ADO lane kept the gap the GitHub lane just closed.**
  `templates/jobs/lint-python.yml` still defaulted `sourcePaths` to
  `sigantry_core/ tests/` and had no mypy step at all, so a type error or a
  ruff violation under `scripts/` failed on GitHub and passed on Azure. The
  repo enforces dual-CI parity on purpose
  (`docs/reference/dual-ci-strategy.md`, and a parity lint inside that very
  template), so the two lanes disagreeing about what the gates are is the
  defect, not a gap in coverage.
- `markdown` and `weasyprint` are declared, under a new `render` extra. Both
  are imported at module scope by the render scripts and appeared in no
  dependency group at all. Stated accurately: this does **not** change what CI
  does — `[tool.mypy] ignore_missing_imports = true` means an undeclared import
  was never an error, and `mypy sigantry_core/ scripts/` is clean with
  weasyprint absent from the environment. Nor does any workflow install
  `.[render]`; the render scripts are run by hand, and weasyprint needs system
  pango/cairo, so it does not belong in `dev`. The extra makes the requirement
  nameable instead of undiscoverable. (An earlier draft put both in `docs` and
  claimed it fixed a CI gap; review found nothing installs `.[docs]` either.)
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
- `mypy` covers `sigantry_core/` and `scripts/`; it does **not** cover
  `tests/`. That is not a scope choice that can be made by editing the command:
  `mypy tests/` currently aborts during module resolution (duplicate basenames
  with no `__init__.py`) before type checking begins, so covering it means
  restructuring the test tree first.
- The quality-gate assertions read only inline `run:` strings in `ci.yml`.
  Moving `mypy` or `ruff` into a composite action or a reusable workflow would
  read there as "not invoked" and fail the suite. That is the safe direction —
  a false alarm demanding the guard be updated, never a silent pass — but it is
  a real limit, recorded rather than discovered later.
- The pre-commit `mypy` hook still scopes to `^sigantry_core/` while CI also
  type-checks `scripts/`, so a type error under `scripts/` is caught in CI
  rather than at commit time. The hook runs in an isolated environment with its
  own pinned dependency list, so widening it means maintaining that list too.

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
