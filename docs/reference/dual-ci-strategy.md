# Dual-CI Strategy — GitHub Actions + Azure DevOps Parity

**Status:** v3.0 working draft (2026-04-24, milestone v3.0 Phase 10, BRIEF-06)
**Audience:** Sigantry contributors and plugin authors shipping pipeline / workflow templates.
**Rule:** Every user-facing feature ships in both Azure DevOps YAML AND GitHub Actions workflow variants, or blocks the PR.

## Why dual-CI

The Microsoft Fabric customer base is split roughly in half between Azure DevOps shops and GitHub shops. Many large enterprises use **both** — GitHub for public/vendor collaboration, ADO for regulated internal work. A Fabric-DataOps product that only supports one CI system loses half the addressable market at evaluation time.

Sigantry's commitment: every feature that produces a pipeline, a workflow, or a CI-runnable template ships **simultaneously** for ADO and GHA. No "ADO first, GHA later" migrations; no "ADO-only premium features".

## The parity rule

> **Every feature that produces a CI template, a workflow YAML, or a scheduled runner ships a GitHub Actions variant AND an Azure DevOps variant in the same PR. A merge that only updates one side is blocked by the `dual-ci-parity` lint.**

The rule is enforced by CI. See the *Enforcement* section below.

## Naming conventions

Template pairs live side-by-side with matching basenames under convention-pinned prefixes:

| Feature | ADO path | GHA path |
|---------|----------|----------|
| Deploy-with-tests (5-stage flow) | `templates/stages/sigantry-cd.yml` | `.github/workflows/sigantry-cd.yml` |
| Scheduled drift check | `templates/schedules/drift-check.yml` | `.github/workflows/drift-check.yml` |
| PR review bot | `templates/pr-review/pr-review-bot.yml` | `.github/workflows/pr-review-bot.yml` |
| Release record (post-deploy WI link) | `templates/stages/sigantry-release-record.yml` | `.github/workflows/sigantry-release-record.yml` |

Rules:
1. ADO templates live under `templates/` with a `stages/`, `schedules/`, or `pr-review/` subdir by purpose.
2. GHA workflows live under `.github/workflows/` with the same basename (no subdirs — GitHub flattens).
3. Template internal names (ADO `stages:` / GHA `jobs:`) use identical kebab-case identifiers so parity tests can diff stage graphs.
4. Parameter names match across both (camelCase on ADO YAML is a convention but Sigantry enforces lowercase_with_underscores on both for portability).

## Semantic parity — what "the same" means

The two templates must provide the **same user-observable behaviour**, not identical YAML. Because the two systems differ in how they implement some concepts, the parity rule allows adapters:

| Concept | ADO | GHA |
|---------|-----|-----|
| Approval gate | Named ADO Environment approver | GitHub `environment.reviewers` |
| Secret resolution | ADO variable group / Key Vault-linked group | GitHub `secrets` + Key Vault action |
| Matrix | `strategy.matrix` | `strategy.matrix` (identical semantics, similar syntax) |
| Artifact upload | `PublishPipelineArtifact@1` | `actions/upload-artifact@v4` |
| Conditional execution | `condition: succeeded()` | `if: success()` |
| Reusable template extension | `extends:` / `template:` | `uses:` (workflow_call) |

The parity test (see *Enforcement*) checks that the **stage graph** is identical (same stage names, same dependency order, same conditional gates) even if the underlying action steps differ.

## Test matrix

Every new feature runs a three-axis matrix in CI:

| Axis | Values |
|------|--------|
| CI system | ADO, GHA |
| OS | ubuntu-latest |
| Python version | 3.11, 3.12 |

For pipeline-level integration tests (Phase 12 PIPELINE-05, Phase 13 DRIFT-03, Phase 14 STARTER-05..07), the CI-system axis drives which real runner the test fires from.

## Enforcement — the `dual-ci-parity` CI lint

A Python script `scripts/ci/check-dual-ci-parity.py` runs in a required CI check on every PR. It does three things:

1. **Pair check.** For every file matching `templates/**/*.yml`, asserts there's a file in `.github/workflows/` with the same basename, and vice versa. Missing counterpart → red.
2. **Stage-graph parity check.** For each pair, parses both YAMLs and asserts the stage names + dependency edges are identical. Differences in step bodies are allowed; differences in the graph are not.
3. **Parameter parity check.** Asserts the input parameters declared on both sides have the same names and types. Extra parameters require a matching counterpart or an explicit `sigantry-dual-ci-ignore: <reason>` annotation in both files.

The lint runs under both GitHub Actions and Azure DevOps CI (the CI pipeline of this repo itself must pass both).

## Exceptions

The rule has **two narrow exceptions**, both explicitly annotated:

1. **CI-system-specific tooling.** A template that exists only because of CI-system mechanics (e.g. `publish-ado-wiki.yml` vs `publish-gh-pages.yml`) is annotated `sigantry-dual-ci-exception: ci-mechanics` in its header. The parity test skips it.
2. **Bootstrap / initial-setup scripts.** The one-shot `service-connection-bootstrap.ps1` (for ADO) and its `workflow-identity-bootstrap.sh` counterpart (for GHA OIDC) are annotated `sigantry-dual-ci-exception: bootstrap`. They have different enough semantics that a single parity test would be more confusing than useful.

Adding a third exception requires an ADR. The rule is deliberately sticky.

## Runner strategy

- **ADO.** Microsoft-hosted `ubuntu-latest` pool, plus an optional `hs2-fabric-agents` self-hosted pool for HS2-specific live-tenant tests. The `agentPool` parameter is first-class on every ADO stage template.
- **GHA.** GitHub-hosted `ubuntu-latest` runners by default. Self-hosted runners are supported for customers with private Fabric tenants; `runs-on` is parameterised in every workflow.

Both CI systems support **OIDC workload-identity federation** to Azure — no long-lived secrets in either environment. See [ADR-0011](../decisions/ADR-0011-rename-to-sigantry.md) for the `SIGANTRY_DEMO_*` env-var pattern used for the public demo.

## Plugin authors

Plugins that ship their own templates follow the same rule. A plugin's entry in its `pyproject.toml` can declare template locations, and the `sigantry doctor` command validates plugin templates against the same parity lint.

## Success metrics for Phase 10

BRIEF-06 completion requires:

- This document exists at `docs/reference/dual-ci-strategy.md` ✓ (you are reading it)
- `scripts/ci/check-dual-ci-parity.py` is committed and wired into both `.github/workflows/ci.yml` and `azure-pipelines.yml` as a required check (planned — lands in the Phase 10 execution plan, not this brief)
- A deliberately-incomplete test PR (adds only the ADO template) fails the check; a matching-counterpart PR passes (fixture tests in `tests/ci/test_dual_ci_parity.py` — planned)

## See also

- [PRODUCT-BRIEF.md](../PRODUCT-BRIEF.md) — the why.
- [Seam Map](seam-map.md) — seams that ship CI-adjacent reference implementations (approval gate, PR bot, notification sink).
- [ADR-0011 — Rename to Sigantry](../decisions/ADR-0011-rename-to-sigantry.md) — env-var prefix and package-name migration.

---

*Updated: 2026-04-24 (milestone v3.0 Phase 10, BRIEF-06).*
