# Changelog

All notable changes to **Sigantry** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Upgrading from 1.0.0
- **The bullets below describe ways this release differs from 1.0.0** when
  it reads settings or bootstraps a workspace, one subject each: Config
  file; Env prefix; Invalid values in the new inputs; Settings keys;
  `ToolkitSettings()` built directly; `auth.expected_group`; Unprefixed
  variables (and see Security); Settings classes; Warnings as errors;
  pytest plugin; Workspace bootstrap.
- **Config file.** `.sigantry.toml` is now read. When it and the legacy config
  file both exist and differ, the legacy file is still the one read, as in
  1.0.0, with a `UserWarning`; two identical files are read without one. A
  `.sigantry.toml` that 1.0.0 ignored because it was the only file present now
  takes effect.
- **Env prefix.** `SIGANTRY_<SECTION>__<KEY>` is now read. `FDT_` is still
  read, with a `DeprecationWarning`, and through 1.0.x it outranks
  `SIGANTRY_` for the same setting. A `SIGANTRY_` value overrides
  `.sigantry.toml`, but over the legacy config file, or a file passed by
  path, it only fills what the file leaves unset, because 1.0.0 read those
  files and ignored `SIGANTRY_`; in a table
  such as `[release.ado]` it fills the keys the table leaves unset. A
  `UserWarning` names each `SIGANTRY_` variable that a different legacy value
  overrides; equal values are silent. `FDT_` names in another letter case
  (`fdt_core__tenant_id`), and `fdt_<section>` holding a JSON object, are read
  again and ranked as in 1.0.0: below a file key spelled `tenant_id`, above
  one spelled in another case such as `TENANT_ID`, and with
  `<SECTION>__<KEY>` names laid over the JSON object. Where `fdt_<section>`
  holds JSON that is not an object (`null`, `5`), that section's
  `<SECTION>__<KEY>` names are not read, as in 1.0.0; where
  `fdt_<section>__<table>`, for a table such as `release.ado`, holds a value
  that is not a JSON object (`null`, `5`, an empty value), a table spelled
  two ways in the file resolves as it did there. Where 1.0.0 failed on such
  a value, the value is ignored with a `UserWarning`; an `fdt_<section>`
  value that is not JSON at all, an empty one included, is ignored that way
  and the section's `<SECTION>__<KEY>` names are read.
  Where several `FDT_` names set one setting, the one 1.0.0 used still wins,
  which for names that differ only in letter case depends, as in 1.0.0, on
  the order the environment lists them. `load_settings()` again keeps an `FDT_` name whose
  section is not a settings section (`FDT_MYPLUG__KEY`) as a top-level extra,
  and it now reads `FDT_<SECTION>` holding a JSON object, a spelling 1.0.0's
  `load_settings()` failed on.
- **Invalid values in the new inputs.** A value from a `.sigantry.toml`
  found without its path, or from a `SIGANTRY_` variable read without a
  prefix the caller passed or declared, is validated like any other
  wherever it takes effect, so an invalid one, such as an empty
  `SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED` or
  `preview_apis_acknowledged = "maybe"` under `[workflow]`, now raises
  `ValidationError`, from `load_settings()` and, for a variable, from
  `ToolkitSettings()` built directly. 1.0.0 read neither that file nor such
  a variable as settings. Where an input 1.0.0 read outranks a `SIGANTRY_`
  value in the same load, that value is ignored and does not fail the load.
- **Settings keys.** A key written in another letter case, such as `TENANT_ID`
  under `[core]`, sets its field, with a `DeprecationWarning`, and so does a
  section name such as `[CORE]`. 1.0.0, with pydantic-settings 2.15, set its
  own fields from such keys too, without a warning; `auth.expected_group`,
  which it did not have, is described below. Where one is spelled more than
  once, the first spelling wins, as in 1.0.0. Keys that name no field keep
  their spelling.
- **`ToolkitSettings()` built directly** reads settings variables again:
  `FDT_` ones from the environment, `_env_file` and `_secrets_dir`, ranked
  in that order as in 1.0.0, and `SIGANTRY_` ones from the same three
  inputs, which only fill what those leave unset. Given a non-empty prefix
  of its own, passed as `_env_prefix` or declared as `env_prefix` in the
  `model_config` of a subclass, or of a class between it and
  `ToolkitSettings`, `SIGANTRY_` in any letter case included, it reads the
  names under that prefix instead (a passed one over a declared one), as
  1.0.0 read them in place of `FDT_` names, and no others. Only names whose
  section is a settings section are read, in the forms 1.0.0 read
  (`<PREFIX><SECTION>__<KEY>`, and `<PREFIX><SECTION>` holding a JSON object)
  and as `SIGANTRY_<SECTION>__<KEY>`. A name in `_env_file` that sets no
  settings field is not kept, where 1.0.0 kept names such as
  `FDT_MYPLUG__K=v` and an unprefixed `OTHER=o` as top-level extras
  (`fdt_myplug__k`, `other`) and `model_dump()` rendered them. Values passed to
  the constructor outrank them all. An empty prefix, passed or declared, is
  refused with a `UserWarning` that names it, because it would read
  unprefixed names, and the default prefixes are read instead; under an
  empty prefix 1.0.0 set no field from an `FDT_` name.
- **`auth.expected_group`** is a new field, typed `str | None`, which
  `diagnose-auth` now reads (see Changed). 1.0.0 had no such field: it kept
  an `expected_group` key under `[auth]` as an extra, whatever its value,
  and its `diagnose-auth` loaded no settings. A value other than null that
  pydantic does not accept as a string now fails validation where it takes
  effect: in a config file, a number, a boolean, a date or time, an array or
  a table, such as `expected_group = 5` or `expected_group = true`; in JSON,
  a number, a boolean, an array or an object; passed to the constructor,
  `5` or `True`, for example. `load_settings()` or `ToolkitSettings()` then
  raises `ValidationError` where 1.0.0 kept the value as an extra and set
  the other settings. A value pydantic converts to a string, such as
  `b"grp"` passed to the constructor, sets the field to that string
  (`"grp"`), where 1.0.0 kept the value unchanged as an extra. Measured
  against 1.0.0 for a key in the legacy config file or in a file passed by
  path, a value passed to `ToolkitSettings()`, and `FDT_AUTH` holding a
  JSON object in the environment, an `_env_file` or a `_secrets_dir` given
  to `ToolkitSettings()` built directly; `load_settings()` on 1.0.0 failed on
  `FDT_AUTH={"expected_group": 5}` as well. A key in a config file spelled in
  another letter case, such as `Expected_Group`, now sets the field, with
  the `DeprecationWarning` described under Settings keys, where 1.0.0 kept
  it as an extra under that spelling, so a value of the kinds above there
  fails the same way.
- **Unprefixed variables stay unread** (see Security). A `FutureWarning` names
  each one that 1.0.0 would have read, where nothing else sets the field, with
  its `SIGANTRY_<SECTION>__<KEY>` replacement: `TENANT_ID`, `PROVIDER`, `SINK`,
  `PROFILE`, `GATE`, `REGISTRY`, `POLICY`, `STORE`, `BOT`, `AUDIT_DIR` and
  `PREVIEW_APIS_ACKNOWLEDGED`, and `STATIC_MAP`, `ADO` or `GITHUB` holding a
  JSON object, in any letter case. `sigantry sync apply` and `sigantry sync
  pull` still honour an unprefixed `PREVIEW_APIS_ACKNOWLEDGED`, which only
  silences the Preview-API notice, where no file or prefixed variable sets it.
- **Settings classes.** The section models (`CoreSettings`, `AuthSettings` and
  the other eleven) are plain pydantic models, not `BaseSettings`, which is
  what stops unprefixed variables binding. A subclass that relied on
  `BaseSettings` reading the environment must read it itself or go through
  `load_settings()`.
- **Warnings as errors.** Library code run with `PYTHONWARNINGS=error`,
  `-W error` or pytest's `filterwarnings = error` turns these warnings into
  exceptions. `sigantry sync apply`, `sigantry sync pull` and `diagnose-auth`
  record them and print none, as 1.0.0 printed none, so neither a
  warnings-as-errors setting nor a pipeline step that fails on any stderr
  output stops them. A `DeprecationWarning` from the settings loader is
  attributed to the first caller outside sigantry, so Python shows it by
  default when the script being run made the call,
  `FabricDataOps.from_config()` included.
- **pytest plugin.** The `fdt_settings_toml` fixture still writes only the
  legacy file and returns its path, as in 1.0.0. While the test that asked
  for it runs, the loader reads that file without a warning, in the test's
  own process and in any process the test starts that inherits its
  environment: no `DeprecationWarning` for the legacy name, and no
  `UserWarning` if the test also writes a `.sigantry.toml` beside it, which
  1.0.0 did not read either. A test that changes into that directory and
  calls `FabricDataOps.from_config()`, or runs a script there that does,
  therefore passes under warnings as errors, as it did on 1.0.0, whether it
  edits, replaces or deletes the file first. A key the test spells in
  another letter case, such as `core={"TENANT_ID": ...}`, still gets the
  `DeprecationWarning` described under Settings keys. Any other legacy file
  warns as before.
- **Workspace bootstrap: pin your folder names before re-running a
  blueprint.** This release changes the folder names of the
  `minimal_starter` and `medallion` blueprints, and bootstrap never renames
  or deletes a folder, so re-running either blueprint on a workspace that
  1.0.0 bootstrapped creates the new folders beside the old ones. To keep
  an existing layout, list its folder names under `folders.list` in
  `workspace.yml` instead of naming a blueprint; a dry run then reports
  `folders` as `already-converged`. Bootstrap warns when a blueprint would
  add folders beside a workspace's existing top-level folders (see
  Changed).

### Added
- **A name gate** (`scripts/ci/check-name-gate.py`, run by
  `.github/workflows/name-gate.yml`) fails when the repository carries a name
  from a token list held outside it, in a repository secret. It is built for
  the accidental case, a name written plainly, not for one hidden on purpose.
  Unlike the file-type and path allowlist of the older banned-string test, it
  exempts no path, basename or suffix. It reads the path of every tracked and
  untracked-not-ignored file; text as UTF-8, UTF-16 or UTF-32 with a
  byte-order mark, or cp1252, each line also with markup tags removed and
  character and `\uXXXX` escapes decoded; PDF text layers and metadata; zip
  and gzip-tar members, their names, link targets, zip comments and tar
  headers; and, with `--archive`, a built wheel or sdist. What it cannot read
  is reported: an unreadable document or container, or a git LFS pointer,
  fails the run, and a binary file with no reader (an uncompressed tar
  included) fails until the register accepts it by name. It prints
  `<file>:<line> <pattern id>` and never the matched text; a path that itself
  matches is printed as a hash. An exception register in the same secret
  excuses exact lines, and an entry that no longer matches fails the run.
  Without the list, as on a fork's pull request, it fails closed.
- **The name gate reads built distributions against the tree they were built
  from.** `check-name-gate.py --root . --dist dist` scans the tree, then the
  wheel and the sdist in `dist/`, in one run with one verdict; `dist/` must
  hold exactly one of each and nothing else, dot files included, because it
  is what the upload sends. A member whose bytes are identical to the tree
  file at the same path (a wheel's PEP 639 licence copy included) is judged
  by that file's register entries rather than reported twice. A hit in the
  core metadata is keyed by its header field (`METADATA#Author:1`); in a
  field that can repeat, such as `Classifier`, `Requires-Dist` or
  `Project-URL`, by a digest of its entry (`METADATA#Classifier@<digest>:1`);
  or by the readme's line when the body is a byte-identical copy of the
  readme. So a version bump, or a new classifier, dependency or URL, moves
  no key, while an edited one is a new key. A wheel `RECORD` line
  whose path, SHA-256 and size all check out is read as empty, because a
  random digest can contain a short token by chance. The artifact file
  names and each sdist member's owner and group names are read as well.
  `--archive` without `--root` still scans only the artifacts it names, so
  it runs outside a work tree; given `--root`, it scans that tree first and
  reads the artifacts against it, as `--dist` does.
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
- `tests/ci/test_contract_floor.py` pins how many seam contract tests run. It
  runs `tests/contract/` in a child pytest and fails unless every contract
  test executed and exactly `CONTRACT_FLOOR` did, so a skip, an error, a
  removed test, or a new test added without raising the number turns the
  suite red (ADR-0016). The
  contract suite no longer carries arms for plugin distributions that are not
  part of this repository; on a clean runner they always skipped.

### Changed
- The workspace delete fallback is described as what it does. With
  `pbi_fallback=True`, a Fabric `DELETE` that fails with `UnknownError` is
  tried again through the Power BI groups endpoint. The docstring, the
  operator runbook and a test docstring made a reliability claim for that
  endpoint that nothing in the project measures; they now say it is tried
  again.
- **One copyright statement.** ADR-0010 said copyright was held jointly by
  contributors and that a DCO sign-off check was enforced, while the guide
  cover pages named a single holder. ADR-0010 now says copyright in a
  contribution stays with its owner (the contributor, or their employer),
  that contributions come in under section 5 of Apache-2.0 unless the
  contributor states otherwise, and that DCO sign-off was never adopted;
  the covers name the maintainer and the contributors; CONTRIBUTING.md
  states the licence of contributions.
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
  `twine check` → publish with no quality job in front of it, while CI's own
  `build` job gated only a `dist` artifact that nobody installed. `ci.yml` is
  now callable (`workflow_call`) and the publish job depends on it.
- **A release publishes the files CI built and checked, and builds nothing
  of its own.** Even behind the quality gate, `publish-pypi.yml` rebuilt the
  sdist and wheel inside its publish job, so the bytes uploaded to PyPI were
  never the bytes `ci.yml` had checked. `ci.yml`'s `build` job now records
  the SHA-256 of each file it uploads as `dist` and hands the record to the
  release through a `workflow_call` output, and it checks out without
  persisting the GitHub token, so the build tools it installs from PyPI
  cannot read that token from the git config. The publish job (renamed from
  `build-and-publish` to `publish`; the workflow file and the `pypi`
  environment that PyPI's trusted publisher matches are unchanged) downloads
  that artifact, verifies it against the record, scans the tree and both
  distributions with the name gate's `--dist`, and uploads them. It checks
  out without persisting credentials, installs nothing from PyPI and no
  longer restores a pip cache, so nothing from PyPI or from an earlier run
  executes beside the OIDC token or the token list before the scan. To let
  the scan read PDFs, it installs `poppler-utils` and the libraries it
  depends on from the runner's Ubuntu archive, and apt-get runs the package
  scripts and triggers they set off as root, before the scan.
  `skip-existing` stays on: re-running all jobs of the release run is now
  the only recovery from an upload that stopped after one file.
- `mypy` in `.pre-commit-config.yaml` moved from `v1.13.0` to `v1.20.2`, the
  version `mypy>=1.19,<2.0` actually resolves to, so the hook and the CI gate
  cannot disagree about what counts as an error.

- **Config surface renamed to match the product (ADR-0011).** `load_settings()` and
  `FabricDataOps.from_config()` now resolve `.sigantry.toml` by default, and
  settings env overrides use the `SIGANTRY_<SECTION>__<KEY>` prefix. 1.0.0
  read the documented `.sigantry.toml` filename only when given its path:
  an operator who followed the migration guide and relied on the default
  lookup got a config file that was silently ignored, and a run without the
  settings in it. Upgrading from 1.0.0 describes ways settings are now read
  differently from 1.0.0.
- The workflows' action pins move to `actions/checkout` v7.0.1,
  `actions/setup-python` v7.0.0 and `actions/setup-node` v7.0.0, still pinned
  by commit SHA: 28 pins in 7 workflows, moved together because the pin test
  requires every workflow to pin `actions/checkout`, and
  `actions/setup-python`, to the same SHA. All three stay on the node24
  runtime. `actions/checkout` v7 refuses to check out a fork's pull-request
  code in a `pull_request_target` or `workflow_run` workflow unless
  `allow-unsafe-pr-checkout` is set. No workflow here uses either trigger; a
  caller that reached `sigantry-pr-bot.yml` through `workflow_call` from a
  `pull_request_target` workflow, on a fork pull request, would now be
  refused at its head checkout.
- Dependabot now proposes GitHub Actions bumps monthly instead of weekly and
  applies no labels. Its PRs are not merged as they stand: their changes move
  in a maintainer-owned PR, and the bot PR is closed. `labels: []` is written
  out because leaving the key out makes Dependabot apply its default labels.
  The pip entry is gone with the development lock (see Removed): its grouping,
  ignore rules and comments were all about that file and a resolve gate that
  was never part of any workflow in this repository, and the dependency ranges
  in `pyproject.toml` are edited by hand.
- **`diagnose-auth`: the expected Entra group is configurable.** It was a
  hard-coded name. Set it with `--expected-group`, `[auth] expected_group` in
  `.sigantry.toml`, or `SIGANTRY_AUTH__EXPECTED_GROUP` (the flag wins). With
  none set, the group check is reported as `skipped`, sends no Graph request
  and does not change the exit code. If the settings cannot be loaded and no
  `--expected-group` is given, the check is reported as `error` (exit code 2)
  rather than skipped, because a group set in the environment is lost when the
  file fails to parse. `--output json` is now written with plain `json.dumps`
  and table values are rendered as plain text, so a group name containing
  square brackets or an emoji code such as `:fire:` is printed unchanged, and
  long values are no longer wrapped inside JSON strings.
- **Workspace bootstrap creates new folder names for the `minimal_starter`
  and `medallion` blueprints:** `00_control`, `10_intake`, `20_storage`,
  `30_transform`, `40_semantic`, `50_reporting`, `90_shared` and
  `99_retired`. Bootstrap never renames or deletes folders, so re-running it
  against a workspace bootstrapped with the old names creates the new folders
  alongside the old ones. To keep an existing layout, list its folder names
  under `folders.list` instead of naming a blueprint.
- **Workspace bootstrap warns before it lays a blueprint out beside
  existing folders.** When a blueprint will create any of its folders at
  the top level of a workspace that already has top-level folders with
  other names, `sigantry workspace bootstrap` prints one
  `sigantry: warning:` line on stderr, giving the number of those other
  folders and of the blueprint folders it creates and pointing at
  `folders.list`, and adds a `warnings` list with the same text to its JSON
  report, in a dry run too. The run still creates the folders, as 1.0.0
  did. The warning names no folder and compares the workspace only with the
  blueprint's own names, so it also fires for folders made by hand. A run
  without a warning prints the same output as before; a warning adds that
  one stderr line and the `warnings` key. In the library,
  `bootstrap_workspace()` takes an `on_warning` callback (default: a
  WARNING on the `sigantry_core.workspace.bootstrap` logger), called before
  any folder is created, and returns the texts on
  `BootstrapResult.warnings`. If the callback raises, bootstrap logs the
  error at WARNING on the same logger, finishes the run and, outside a dry
  run, writes its audit record, whose shape is unchanged.
- The HTML drift, release and release-diff reports end with
  `Generated by Sigantry v<installed version>`; the version was hard-coded.
- Package metadata: the author entry has no email address, the
  `Documentation` URL points at the `docs/` tree on GitHub (as does the
  README's documentation link; the Pages site both named does not exist),
  and the Python 3.13 classifier is dropped because CI tests 3.11 and 3.12
  only; `requires-python` is unchanged.
- Security reports go through GitHub private vulnerability reporting
  (`SECURITY.md`).
- Pytest markers that nothing in this repository applies or selects are no
  longer declared, and the root `conftest.py` no longer ignores a smoke test
  file that does not exist.
- `scripts/live-creds.template` and `scripts/discover_env_live.py` now name
  the Fabric test-tenant values `SIGANTRY_FABRIC_TEST_TENANT_ID`,
  `SIGANTRY_FABRIC_TEST_WORKSPACE_ID`, `SIGANTRY_FABRIC_TEST_CAPACITY_ID` and
  `SIGANTRY_FABRIC_TEST_ENVIRONMENT_ID`, the prefix the template already used
  for its other Fabric test values. The template now keeps only the service
  principal values, the Fabric test-tenant values that `discover_env_live.py`
  prints, and the demo-tenant values `docs/demo/QUICKSTART.md` uses. It drops
  the variables of live tests that are not in this repository and the
  `PYTEST_RUN_INTEGRATION` line, which nothing reads.
  `discover_env_live.py --workspace-name` no longer has a default: without it,
  no workspace is picked.
- The documentation no longer tells readers to install plugin distributions
  this project does not publish. `docs/migration/2.x-to-3.0.md` is now a
  short note on the pre-rename names: what replaces each one, which of them
  the code still reads, and what the 1.0.0 release reads instead.
- `tests/prereqs/test_phase7_banned_apis.py` no longer lists the packages
  `sigantry_core` must not import; it permits a reviewed set and fails on
  anything else. An import statement outside function bodies in any of the
  package's modules may name only the standard library, `sigantry_core`
  itself, or a third-party root the test permits or a module inside one,
  each root declared in `pyproject.toml` or required by a dependency
  declared there. The check now also covers imports inside module-level
  `try`, `if` (`if TYPE_CHECKING:` included), `with` and class bodies, which
  the old line-start match could not see. Imports inside function bodies
  stay allowed. Its two `httpx` checks keep their scope, now read import
  statements rather than line starts, and fail if their directory holds no
  Python file.
- The name gate (see Added) takes over the name checks of the older
  banned-string test (see Removed). It runs on every pull request to `main`
  and on every push to `main`, and must pass before merge. The release
  workflow also runs it on the released tree and on the wheel and sdist it
  uploads to PyPI, as the last step before the upload.

### Deprecated
- `.fabric-dataops.toml` and the `FDT_` settings env prefix. Both are still
  read for one more minor release and each emits a `DeprecationWarning` naming
  its replacement. Through 1.0.x, where a setting is supplied under both
  prefixes, the `FDT_` value is used, as in 1.0.0.

### Removed
- The wheel and the sdist no longer carry the six `TODO-*.md` planning notes
  kept beside the code under `sigantry_core/`. Both build targets exclude
  them, and a slow test now lists the members each distribution may carry:
  a file that reaches either one without being listed fails, and so does a
  listed one that goes missing.
- The demo walkthrough video build: `scripts/remotion/` (a Node project),
  `scripts/build-walkthrough.sh`, `.github/workflows/sigantry-demo-mp4.yml`,
  `docs/demo/walkthrough-script.md` and their tests. The video was never
  published, so the pages that linked to it now say nothing about it, and
  the demo-tenant runbook loses its re-recording section (later sections
  are renumbered). The repository no longer carries an npm manifest.
- The older banned-string test (`tests/prereqs/test_phase8_banned_apis.py`),
  its path allowlist (`tests/prereqs/banned_api_allowlist.yaml`, 91 entries,
  38 of them naming files this repository did not have) and the
  allowlist's own schema test
  (`tests/prereqs/test_banned_api_allowlist_yaml.py`). The name gate replaces
  their name checks; their checks that a few moved files stay deleted, and
  the `site_name` check on `mkdocs.yml`, are dropped. One of the removed
  tests scanned a `bicep/` directory that this repository does not have, so
  it could not fail.
- `.github/workflows/release-alpha.yml` and `scripts/release/publish-v3-alpha.sh`
  are removed, with the dated `_PUBLISH_GATE_EXEMPT` carve-out that excused
  the workflow's ungated publish job (#13). The workflow served a retired
  `v*-alpha` tag scheme, could not succeed (its build script named directories
  absent from this repository, under `set -euo pipefail`), and the carve-out's
  review-by date of 2026-12-31 would have turned the `test` job red on
  2027-01-01 with no code change, blocking every release until someone edited
  the date. No dated carve-out remains. The script-routed publish detection
  and the carve-out checks keep their own direct tests, since loops over an
  empty map assert nothing.
- `scripts/ci/mypy_gate.py` and its tests are removed. It was a baseline-ratchet
  gate invoked by nothing, its docstring claimed 63 pre-existing errors in a
  tree that is clean, and the `mypy-baseline.txt` it read never existed. With
  both type-checked roots clean there is nothing to ratchet, and an unrun tool
  that describes a world that no longer exists is exactly what this release is
  removing elsewhere.
- The hash-pinned lock of the development dependencies is removed, with the
  six tests that checked only its format. Nothing installed it: no workflow,
  script or setup step read it, and the resolve gate that the Dependabot
  config said guarded it was never part of any workflow in this repository.
  It had drifted too:
  21 of its comments still named the old distribution, and on 2026-09-30 the
  OSV database listed 37 advisories against 8 of its 94 pins. A lock that
  looks like a control but is never installed is worse than none. Python
  dependencies stay declared as ranges in `pyproject.toml`.
- Internal documents are removed from `docs/`: the operator punch-list,
  adoption plan and v3.x roadmap (the whole `docs/operator/` folder), the
  1.x-to-2.0 migration guide, the related-work survey, and one deployment's
  onboarding runbook. Two tests go with them: the migration guide's own
  test, and a Pester test whose target script is not in this repository.
  The committed `docs/Sigantry-User-Guide.pdf` and
  `docs/Sigantry-Tutorials.pdf` are removed too; both covers read release
  3.2.1. The Markdown stays the canonical source:
  `scripts/userguide/render.py` and `scripts/tutorials/render.py` now write
  to `build/docs/`, which is gitignored.
- `sigantry_core.auth.diagnose.ExpectedEntraGroup`, and the default group of
  `check_entra_group()`. Both carried one deployment's group name; pass
  `expected_group=`, or configure the group as described under Changed.

### Fixed
- **`.github/workflows/drift-check.yml` failed every day.** Its `schedule:`
  trigger ran the workflow with an empty `inputs` context (declared defaults are
  not applied to scheduled runs either), so `sigantry diff` got no workspace and
  no manifest: 11 of 11 scheduled runs failed between 2026-09-20 and 2026-09-30.
  The workflow is reusable (`workflow_call` / `workflow_dispatch`) and now has no
  schedule of its own; adopters schedule a caller that passes the inputs, as the
  drift runbook already showed. It also gains `permissions: contents: read`. A
  new check fails any scheduled workflow file (`.yml` or `.yaml`) that declares
  a required input or whose jobs mention `inputs.`. It does not see reads
  outside `jobs` or the index form `inputs['x']`, and it flags a read with a
  `||` fallback, which does work on a schedule.
- **`sigantry diff --output json` wrote its errors to stdout**, the stream the
  drift pipelines capture into `drift.json`, so a failed run's `drift.json`
  held error text. Errors now go to stderr, and on an error `drift.json` is
  empty. The notify step still cannot report an operational error (#34).
- **`scripts/audit_chain_migrate.py` could destroy or launder audit records,
  and reported success either way.** A re-run read the `.pre-w3.1.bak` backup
  whenever it existed and replaced the live ledger with it, so every record
  written since the first migration was lost; on a copy of a real deploy
  ledger it dropped 13 of 63 records at exit 0 and `release verify` then
  reported the shorter chain as valid. It also re-sealed a record that failed
  its own hash (so a hand edit came out verifying), and judged a ledger
  "already chained" from its first two records only, leaving a broken later
  link in place. Now:
  - every record is checked against its own stored hash, and records from
    before chaining may only form a prefix: from the first record that carries
    `prev_hash`, each must link to its predecessor, so a truncated, reset or
    forked chain is refused rather than re-sealed;
  - the backup is read only when the live ledger is absent, and a backup whose
    records are not all at the head of the live ledger is refused (it may be
    their only copy);
  - a refusal writes nothing and exits 1, the other ledgers still run, and
    unreadable input (invalid UTF-8, malformed JSON) is a refusal, not a crash;
  - records are split on `\n` only, as the readers do, so U+2028, U+2029 or
    U+0085 inside a field no longer breaks a record in two;
  - the migration holds the audit writers' lock from choosing the file to
    replacing it; the backup never overwrites an earlier one and appears under
    its name only once complete; the written ledger is read back and verified;
  - `--dry-run` refuses exactly as a real run would and creates no file (not
    even a lock file), so it works on a read-only copy.
- **The test suite wrote into the real `~/.sigantry/audit/` ledgers.** Tests
  that fall back to the default audit directory appended fixture records
  (approvals, destructive-op and secret-change records from principals such as
  `MockCredential`) to the developer's own ledgers on every run. A root
  `conftest.py` fixture now points `HOME` and every default audit location at
  a per-test temp directory, and a guard test fails if either half is removed.
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
- **The publish scan looked at one filename and one action.** A workflow
  that published via `twine upload` in a job with no `needs:` — precisely the
  defect the test exists to catch — was invisible to it. Every workflow is now
  scanned for both mechanisms. That workflow is removed in this release (see
  Removed), so no carve-out remains.
- **`ci.yml` became reusable while keeping `group: ci-${{ github.ref }}` with
  `cancel-in-progress`.** Publishing a release fires both `push: tags` on
  `ci.yml` and `release: published` on `publish-pypi.yml`, which calls
  `ci.yml`; in a called workflow `github.ref` is the caller's, so both landed
  in one group and one cancelled the other. If that was the release's gate, the
  publish job is *skipped* and nothing ships — a cancellation, not a red X.
  The group now includes `github.workflow`.
- `twine check` in CI is now `--strict`. A release publishes the files that
  job checks, so a metadata defect that is only a warning would otherwise
  pass every quality job and surface at upload.
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
  sigantry-core`, a name with no release to install.** The distribution is
  `sigantry` (`pyproject.toml` declares it; no sigantry release was ever
  published as `sigantry-core`, and since 2026-10-04 that name on PyPI holds
  only a yanked, code-free 0.0.1 placeholder that points to `sigantry` —
  ADR-0017 records the amendment to ADR-0011). Every consumer following a
  shipped ADO step template, starter workflow or demo quickstart hit a package
  that is not there. 49 references corrected across `templates/`,
  `.github/workflows/`, `scripts/` and `.pre-commit-config.yaml`. Because the
  old name installs no code for anyone, this fix cannot break an existing
  install.
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
- Shipped quickstart templates told adopters to run
  `pip install "sigantry>=3.0.0"`, which cannot resolve against the shipped
  1.0.x line, and claimed `requires-python = ">=3.11,<3.13"` refuses 3.13 when
  `pyproject.toml` declares `>=3.11` with no upper bound.
- Dead `github.com/sigantry/...` links (the org returns 404) remained in the
  demo quickstart and the shipped demo template after a sibling link in the
  same file was corrected.

- `sigantry sync` no longer swallows every config-load failure. An unreadable
  or malformed config is logged at WARNING level on the
  `sigantry_core.sync.cli` logger, saying the command continues without the
  file's settings, instead of a bare `except Exception` that also absorbed
  genuine defects in the loader. The `sigantry` command does not print that
  logger's warnings. Settings from `FDT_` and `SIGANTRY_` variables still
  apply after such a failure, as `FDT_` ones did in 1.0.0.
- `load_settings`' docstring claimed a missing config file raised
  `ValidationError`. It never did — no settings field is required — so the
  documented fail-fast did not exist. The docstring now states the real
  behaviour and says who is responsible for checking.

### Security
- **`publish-pypi.yml` no longer has a manual trigger.** `workflow_dispatch`
  let a run be started against any ref, leaving the `pypi` environment's
  `v*` tag policy and its reviewer as the only stops before an upload. A
  published GitHub Release is now the only trigger, and a failed release is
  recovered by re-running all jobs of its run. `docs/release-process.md` no
  longer presents the manual run as a fallback.
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
- **A pull request could mark itself as reviewed.** The required
  `review-record` status is posted by `.github/workflows/review-record.yml`,
  which also ran on `pull_request` and `pull_request_review`. Both events run
  the pull request's own copy of the workflow file with `statuses: write`, so
  a pull request that edited the job could post a passing status for its own
  head. The workflow now runs on `pull_request_target`, `workflow_run` and
  `issue_comment`, which run the default branch's copy (a manual
  `workflow_dispatch` runs the ref it is given). A submitted review reaches it
  through the new `.github/workflows/review-record-relay.yml`, which has no
  permissions and runs no code from the pull request. The vendored
  `scripts/gates/review-record-check.sh` is refreshed too: reviews, inline
  comments and record comments count only from the repository owner, members
  or GitHub's Copilot reviewer; a review or inline comment counts only for the
  commit it was made on, and a record comment that names a commit counts only
  for that commit. A pull request from this repository can still add a new
  workflow of its own that asks for `statuses: write`; no file here can
  prevent that.
- **A record comment that named no commit could still pass `review-record`.**
  The vendored `scripts/gates/review-record-check.sh` accepted a
  `Review-Record:` comment that did not name the head as `on <12-hex SHA>`
  (including one naming another commit by a 7-character SHA) whenever the
  comment was newer than the head commit's committer date. That date is set by
  whoever makes the commit, so a contributor could push a back-dated,
  unreviewed commit after any such comment and the status would pass. Such a
  comment no longer counts: a record comment must name the head as
  `on <12-hex SHA>`. Reviews and inline comments still count when made on the
  head, and the owner's head-scoped waiver is unchanged. Copilot
  reviews now count only from the `copilot-pull-request-reviewer[bot]` account
  of type `Bot`; the plain `copilot-pull-request-reviewer` login belongs to a
  separate organization account and no longer counts.

### Known remaining
- Among the `pip install` and dependency lines under `docs/`, the dependency
  instructions in `docs/migration/3.x-pr-bot.md` and one pinning step in
  `docs/reference/api-stability.md` still name the dead distribution, and
  ADR-0011 and ADR-0017 name it too. They are prose rather
  than shipped artefacts and are tangled with a separate version-scheme
  inconsistency (docs say `>=3.0`, the shipped line is 1.0.x), so they are
  deliberately left for their own change rather than half-corrected here.
  Two of them must survive any such change:
  ADR-0017 quotes the dead name to explain the defect, and ADR-0011 records it
  as history.
- The guard scans `templates/`, `.github/workflows/`, `scripts/` and the
  package. It does **not** scan `pyproject.toml`, `environment.yml`,
  `README.md` or `CONTRIBUTING.md`, so the dead name could reappear in those
  without failing CI.
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
