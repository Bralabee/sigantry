# `templates/` -- audiences and audiences only

Two distinct audiences live under this directory. **Don't confuse them.**

The dual-CI parity gate (`scripts/ci/check-dual-ci-parity.py` plus the per-phase `tests/ci/test_*_dual_ci.py` suites) and the export-mirror parity scripts (`scripts/export-{starter,demo}.py`) treat these two audiences differently. Anyone editing `templates/` should know which half they're in before they touch a file.

## A. Consumer-deployable templates (operators fork these)

These are the source-of-truth for the public scaffolding repos. Operators **never instantiate them from this monorepo** -- they clone the public mirror.

| Subdir | Public mirror (planned) | Mirror parity tool |
|---|---|---|
| `starter/` | `sigantry/sigantry-starter` (Phase 14 Test 1, operator-bound) | `python scripts/export-starter.py --dry-run` |
| `demo/` | `sigantry/demo-sigantry` (Phase 15 Test 1, operator-bound) | `python scripts/export-demo.py --dry-run` |

The export scripts are **parity gates**, not propagation tools. Live `--target-github` / `--target-ado-org` flags raise `NotImplementedError` deliberately -- mirror push is operator-driven per CONTEXT D-01..D-03 (`gh repo create` + initial mirror commit by an operator with org-admin permissions). See [`../CONSUMING.md`](../docs/CONSUMING.md) for the consumer-facing summary.

## B. Internal CI building blocks (NOT operator-deployable)

These are the product's own dual-CI parity sources. Adopters compose **individual entries** via `template:` reference (ADO) or reusable workflow `uses:` (GHA). They do not instantiate the directory wholesale.

| Subdir / file | Purpose |
|---|---|
| `stages/` | ADO stage templates composed into consumer pipelines (e.g. `sigantry-cd.yml` is the 5-stage deploy/smoke/integration/approval/promote pair, byte-paired with `.github/workflows/sigantry-cd.yml`) |
| `jobs/` | Reusable ADO job templates (`run-tests`, `publish-wheel`, `lint-python`, etc.) |
| `steps/` | Reusable ADO step templates (`auth-spn`, `install-deps`, `setup-python`, etc.) |
| `extends/` | ADO `extends:` parents for monorepo-style consumer pipelines |
| `schedules/` | Cron-driven templates (`drift-check.yml` etc.) -- consumed by Sigantry's own CI AND by adopters via composition |
| `pr-review/` | PR-bot template fragments (Phase 14 / STARTER-04..06 -- TMDL diff, Lakehouse metadata diff, dual-CI parity gates) |
| `environments/` | ADO environment definitions for dev/preprod/prod approval gates |
| `parameters.example.yml` | Top-level example of `parameters.yml` shape -- copy + edit per [docs/reference/parameters-yml.md](../docs/reference/parameters-yml.md) |
| `workspace.example.yml` | Top-level example of `workspace.yml` shape (greenfield `sigantry workspace bootstrap`) -- copy + edit per [docs/runbooks/workspace-bootstrap-operator.md](../docs/runbooks/workspace-bootstrap-operator.md). Validated by `tests/templates/test_workspace_example_validates.py` |

The dual-CI parity test gate (`tests/ci/test_starter_dual_ci.py`, `tests/ci/test_demo_dual_ci.py`, `tests/ci/test_drift_template_parity.py`) asserts every entry in this section has a GHA + ADO half with byte-identical args (modulo documented `# sigantry-dual-ci-exception:` annotations and `# sigantry-dual-ci-ignore:` per-line markers).

## Editing rules

- **Edits inside `starter/` or `demo/` MUST keep `scripts/export-{starter,demo}.py --dry-run` green.** The script's parity invariants (PR-checklist fenced block byte-equality, paths-filter parity, sample-item parity) are the contract with the public mirror operator.
- **Edits inside `stages/` / `jobs/` / `steps/` / `extends/` / `schedules/` / `pr-review/` MUST keep `python scripts/ci/check-dual-ci-parity.py` green.** The dual-CI registry asserts pair-with-equivalent-shape semantics; any byte-divergence between halves needs an explicit `sigantry-dual-ci-ignore:` annotation pointing at the divergence rationale.
- **Adding a NEW pair under section B requires updating** `scripts/ci/check-dual-ci-parity.py`'s registry. The current state is `pairs=3 exceptions=9 errors=0` per CLAUDE.md.

## See also

- [`../CONSUMING.md`](../docs/CONSUMING.md) -- operator entry point for the Python distribution + scaffolding
- [`../docs/PRODUCT-BRIEF.md`](../docs/PRODUCT-BRIEF.md) -- product positioning, ICP
- [`../docs/demo/QUICKSTART.md`](../docs/demo/QUICKSTART.md) -- 15-minute end-to-end walkthrough
- [`../scripts/export-starter.py`](../scripts/export-starter.py) -- starter mirror parity gate (CI-side closure of STARTER-01)
- [`../scripts/export-demo.py`](../scripts/export-demo.py) -- demo mirror parity gate (CI-side closure of DEMO-01)
- [`../scripts/ci/check-dual-ci-parity.py`](../scripts/ci/check-dual-ci-parity.py) -- internal CI building-block parity registry (Plan 10-06)
