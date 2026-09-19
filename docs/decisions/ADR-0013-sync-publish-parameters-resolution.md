# ADR-0013 -- sync-publish parameters.yml resolution rule

- **Status:** Accepted
- **Date:** <TBD-on-merge>
- **Milestone:** v3.1.0 (Phase 17 SYNC-PUBLISH)
- **Deciders:** platform team (autonomous-mode planner; rule locked at `.planning/phases/17-sync-publish-compose-sync-apply-first-time-publish/17-CONTEXT.md` D-17-01 + D-17-02)
- **Context:** Phase 17 ships `sigantry sync apply --with-publish` (closes [ADR-0012](ADR-0012-sync-apply-vs-deploy-run-boundary.md) Option C). The flag drives `fabric_cicd.publish_all_items` after the folder reconcile, requiring a `parameters.yml` (PR #54 substituted-value path). The open question that ADR-0012 deferred was: when `--with-publish` is supplied without `--params`, does the CLI infer a sibling `parameters.yml` (mirroring `deploy run`'s `<repository_directory>/parameters.yml` default at `sigantry_core/deploy/core.py:114`) or hard-fail with a clear error? A second open question: under multi-env `parameters.yml`, is `--environment` required?

## Decision

The locked rules, restated as bare phrases so future docs-shape regressions can grep them by substring:

> --with-publish requires --params

> multi-env parameters.yml requires --environment

Expanded:

1. **Hard-fail on missing `--params`.** When `--with-publish` is set without `--params <path>`, the CLI raises `typer.BadParameter` with a message naming the missing argument and pointing at this ADR + the runbook §1.3. There is NO sibling-file inference. There is NO fallback to `<manifest-dir>/parameters.yml`. The locked rule is: `--with-publish` requires `--params <parameters.yml>`. The error message MUST embed the bare phrase `--with-publish requires --params` so operators searching their shell history or CI logs can find a single canonical string.

2. **Hard-fail on missing `--environment` under multi-env params.** When the supplied `parameters.yml` references any environment scope beyond the wildcard `_ALL_` (i.e. `ParametersConfig.environments_seen` is non-empty), the CLI requires `--environment <name>`. A missing `--environment` raises `typer.BadParameter` with a message listing the available environment names. When `parameters.yml` is `_ALL_`-only (`environments_seen == set()`), `--environment` is OPTIONAL. The error message MUST embed the bare phrase `multi-env parameters.yml requires --environment` for the same reason as rule 1.

3. **No silent fallbacks.** Neither rule has a fallback path. If either precondition fails, the CLI exits non-zero immediately and the engine is never invoked. The combined `DeployRecord` is never written. Operators who hit either error see the same "fix-and-rerun" pattern they already know from `sync apply`'s manifest-validation gate.

## Alternatives considered

| Option | Description | Why rejected |
|--------|-------------|--------------|
| **A. Sibling-file inference (`<manifest-dir>/parameters.yml`)** | Mirror `deploy run`'s default of looking for `parameters.yml` next to the manifest. | Inference is operator-friendly until the moment it picks up the wrong file silently. The 2026-05-01 brownfield evidence shows operators already mis-read CLI output; favouring explicitness here matches ADR-0012's design philosophy (the boundary trailer was added precisely because operators don't read default-behaviour documentation before running commands). Inference adds a load-bearing implicit behaviour at the worst possible moment. |
| **B. Inference with explicit warning** | Look for the sibling file, print a yellow `[note]` line if found, exit non-zero if not. | Halfway. Operators who successfully invoke and see the note will assume the inference is the intended path going forward; the next time the manifest moves, the silent-pickup failure mode resurfaces. Either inference is the contract or it isn't. |
| **C. Inference with `--no-infer` opt-out** | Default to inference; require `--no-infer` to disable. | Inverts the load-bearing surface. Operators who never set `--no-infer` (because they don't know about it) get the silent-pickup failure mode; operators who do set it get a CLI surface that's harder to type than `--params`. Net cost > net benefit. |
| **D. Hard-fail (chosen)** | Always require `--params <path>` when `--with-publish` is set. | Explicit, debuggable, matches ADR-0012's "explicit-where-it-matters" philosophy. Cost: operators must type one more flag. Acceptable given the explicit-opt-in nature of `--with-publish` itself. |

## Rationale

1. **Explicit-where-it-matters.** ADR-0012's trailer fix exists because operators don't read default-behaviour documentation. Inference would smuggle the SAME class of confusion under a different surface. The cost of one extra flag is bounded; the cost of a silent-pickup failure mode at first-time publish is unbounded (operators do not discover the wrong-file bug until items appear in the wrong workspace or with the wrong parameter substitution).

2. **Multi-env `--environment` requirement is mechanical.** `ParametersConfig.environments_seen` is already populated by `load_and_validate` (PR #54 work). Detecting "this parameters.yml has multi-env scope" is a single set-emptiness check. Requiring `--environment` when the check is non-empty matches `deploy run`'s existing semantics (also requires `--environment` for multi-env `parameters.yml`). Symmetry is a design feature -- operators who internalise the rule once apply it consistently across both verbs.

3. **`_ALL_`-only is permitted without `--environment`.** Some operators ship a single `_ALL_` block intentionally (CI workflow YAML, single-tenant deployment). Requiring `--environment <name>` in this case would force them to type a sentinel value that the engine ignores. The check `if envs_seen: require_env_flag()` is the minimal correct rule. If multi-env scope is later added to a previously `_ALL_`-only `parameters.yml`, the next invocation hard-fails and tells the operator exactly which `--environment` values are now valid.

4. **CLI exit-code mapping unchanged.** `typer.BadParameter` exits non-zero (Typer's default). The existing exit-code surface for `sync apply` (1 for `SyncEngineError`, 2 for `WorkspacePendingGitUpdateError`) is unaffected -- `BadParameter` exits before the engine is invoked. Operators reading the runbook §1.3 troubleshooting table see the same exit-code semantics they already know from `sync apply` defaults.

## Consequences

- The `--with-publish` flag's CLI surface is two-arg-minimum: `--with-publish --params <path>` is the smallest valid invocation. Documented in [`docs/runbooks/sync/apply.md`](../runbooks/sync/apply.md) §1.3.
- The new error messages do NOT contain customer / tenant / project literals; the banned-API gate at `tests/prereqs/test_phase8_banned_apis.py` stays green.
- Operators upgrading from a v3.0 workflow that ran `sync apply` followed by `deploy run` see no behavioural change unless they opt into `--with-publish`. The pair-of-commands workflow remains valid and supported.
- The hard-fail rule is locked: `--with-publish` requires `--params`, full stop. Future ADR amendments to widen this rule (e.g. permitting sibling-file inference under a feature flag) require a new ADR; this one is the locked rule.
- The multi-env requirement is locked: multi-env `parameters.yml` requires `--environment` whenever `environments_seen` is non-empty. `_ALL_`-only parameters.yml continues to work without `--environment`.
- No new pytest markers; no new exit-code surface; no new dependencies. All three locked rules are enforced inside `apply_cmd`'s argument-validation block, BEFORE delegating to the `apply_sync` engine helper.

## Worked examples

The three operator-facing invocation shapes the rule sorts into:

1. **Single-env or `_ALL_`-only `parameters.yml`** -- valid:

   ```bash
   sigantry sync apply \
     --manifest sync.yml \
     --workspace-id <guid> \
     --with-publish \
     --params parameters.yml
   ```

2. **Multi-env `parameters.yml` with explicit `--environment`** -- valid:

   ```bash
   sigantry sync apply \
     --manifest sync.yml \
     --workspace-id <guid> \
     --with-publish \
     --params parameters.yml \
     --environment DEV
   ```

3. **Missing `--params`** -- hard-fail with the bare phrase `--with-publish requires --params <parameters.yml>`:

   ```bash
   sigantry sync apply \
     --manifest sync.yml \
     --workspace-id <guid> \
     --with-publish        # <-- no --params; CLI exits non-zero before engine invocation
   ```

4. **Multi-env `parameters.yml` without `--environment`** -- hard-fail with the bare phrase `multi-env parameters.yml requires --environment`:

   ```bash
   sigantry sync apply \
     --manifest sync.yml \
     --workspace-id <guid> \
     --with-publish \
     --params multi-env-parameters.yml   # <-- environments_seen non-empty; CLI exits non-zero
   ```

## Cross-references

- [`ADR-0012-sync-apply-vs-deploy-run-boundary.md`](ADR-0012-sync-apply-vs-deploy-run-boundary.md) -- the deferring decision; Option C closed by Phase 17.
- [`docs/runbooks/sync/apply.md`](../runbooks/sync/apply.md) §1.3 -- operator-facing reference.
- `sigantry_core/sync/cli.py::apply_cmd` -- implementation site for the rule (Phase 17 plan 17-02).
- `sigantry_core/deploy/parameters.py::load_and_validate` -- `ParametersConfig.environments_seen` is the input to the multi-env check.
- `tests/sync/test_cli_apply_with_publish.py::test_with_publish_without_params_raises_bad_parameter` + `test_with_publish_multi_env_params_without_environment_flag_raises` -- the falsifiability tests pinning the rule.
