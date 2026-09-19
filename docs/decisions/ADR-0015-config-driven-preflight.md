# ADR-0015 -- config-driven deploy pre-flight (`sigantry preflight`)

- **Status:** Proposed
- **Date:** 2026-06-15
- **Milestone:** v3.x (post-`set-binding`/`--republish-existing`)
- **Deciders:** platform team (TBD — review gate before implementation)
- **Provenance:** distilled from a real consumer's Fabric notebook prod deploy +
  end-to-end run. That deploy reached a green pipeline only after manually working
  through a recurring class of pre-flight failures. The deploy/troubleshooting field
  guide lives in the *consumer's* repo (a downstream tenant of sigantry, kept out of
  this base tree per the vendor-neutral boundary); this ADR proposes turning that
  checklist into a first-class, config-driven sigantry capability.

## Context

`sigantry sync apply --with-publish --republish-existing` + `fabric-item set-binding`
now correctly deploy and bind notebooks (ADR-0012 boundary, ADR-0013 parameters). But
the **operator experience around** those commands is still tribal knowledge. A real
deploy only succeeds if a set of pre-conditions hold, and today each is discovered the
hard way — usually as a runtime failure one redeploy at a time:

1. **Identity has a role on the target workspace.** The deploy service principal may
   have *no* role on the target; only a specific user does. Wrong identity → silent
   no-op or 403. This is often **by design**: least-privilege governance deliberately
   withholds a standing prod-admin role from an automation principal. A tool hard-wired
   to its SPN would be blocked; sigantry is **identity-agnostic**
   (`DefaultAzureCredential`), so the same commands run as the SPN in CI *or* as an
   authorized human locally. The value of an `auth_mode` check is therefore not just to
   catch a misconfiguration — it makes the **intended deploy principal per scenario**
   explicit and asserts the resolved identity matches it (SPN for a sandbox; a user for
   a locked-down prod workspace), turning an implicit, error-prone convention into a
   declared, verifiable contract.
2. **Attached environment carries the expected wheel + version.** Notebook code is
   deployed independently of the wheel; a stale env → runtime `ImportError`.
3. **No disabled cell magics.** A workspace with inline-install disabled rejects any
   notebook containing `%pip` (etc.) with **HTTP 400 at preprocess/fetch time, before
   any cell runs** — and the wrapped error points at a token-service URL, so it reads
   like a throttle. `runMultiple`/retry do not help; the 400 is deterministic.
4. **Bindings survive the deploy.** `--republish-existing` overwrites the in-notebook
   `metadata.dependencies`, **wiping** the env + lakehouse binding. Must re-bind after
   every deploy.
5. **Required input artifacts are present in the lakehouse.** Notebooks read reference
   data from `Files/…`; deploying the notebook does not deploy its inputs.
6. **Cell `source` is list-form**, **preview APIs acknowledged**, **`--republish-existing`
   set** when targeting existing items — else the publish silently updates nothing.

`sigantry doctor` already establishes the right *pattern* (a typer subapp that runs a
set of checks, renders a rich table, and gates exit codes via `--strict`), but it is
scoped to **plugin discovery/trust** (ADR-0014), not deploy readiness. And the
pre-conditions above are inherently **environment-specific** — target ws/env/lakehouse
GUIDs, auth mode, required wheel versions, and the required-artifact list all vary by
scenario (sandbox vs prod; SPN-auth vs user-auth; same-workspace vs cross-workspace
environment). That variability is exactly what configuration is for.

The toolkit already has the config substrate: `ToolkitSettings`
(`sigantry_core/config.py`) is loaded from `.fabric-dataops.toml` via
`load_settings()`, is `FDT_`-prefixed with `__` nesting, and is declared
`extra="allow"` — so a **new top-level section can be added without changing the model**.

## Decision (proposed)

Add a **config-driven pre-flight** with three parts:

### 1. A declarative `[preflight]` config block (in `.fabric-dataops.toml`)

Operators declare **scenarios** (named target topologies + expectations) and which
checks apply. Sketch (GUIDs/names are placeholders the consumer fills in):

```toml
[preflight]
default_scenario = "prod"

# A scenario captures everything environment-specific the checks need.
[preflight.scenarios.prod]
auth_mode            = "user"            # "user" | "service_principal"
workspace_id         = "<target-workspace-guid>"   # must hold the items
environment_id       = "<environment-guid>"        # attached env (wheel host)
environment_ws_id    = "<environment-workspace-guid>"  # cross-workspace env owner
lakehouse_id         = "<lakehouse-guid>"
lakehouse_name       = "<lakehouse-name>"
preview_apis_required = true

# Expectations the checks assert.
required_wheels      = ["<package>>=<version>", "<other-package>>=<version>"]
forbid_cell_magics   = true              # workspace disables inline %pip
required_lakehouse_files = [
  "Files/docs/<required-reference-input>.txt",
]
manifest             = "notebooks/<sync-manifest>.yml"

# Which checks run, and at which stage. Stages: pre_deploy | post_deploy | pre_run.
[preflight.scenarios.prod.checks]
identity_has_role        = { stage = "pre_deploy",  severity = "blocker" }
env_wheel_version        = { stage = "pre_deploy",  severity = "blocker" }
notebook_magics          = { stage = "pre_deploy",  severity = "blocker" }
notebook_source_shape    = { stage = "pre_deploy",  severity = "warn" }
publish_flags            = { stage = "pre_deploy",  severity = "blocker" }
lakehouse_artifacts      = { stage = "pre_deploy",  severity = "blocker" }
bindings_present         = { stage = "post_deploy", severity = "blocker" }

# A second scenario reuses the shape with different values — e.g. a sandbox
# deployed as a service principal.
[preflight.scenarios.sandbox]
auth_mode      = "service_principal"
workspace_id   = "<sandbox-workspace-guid>"
# … same keys, different values …
```

### 2. A check registry (mirrors the `doctor`/plugin pattern)

Each check is a small unit with a stable `id`, the stage it runs in, a severity, and a
`run(ctx) -> CheckResult` that returns `ok | warn | fail` + a human detail string.
Checks resolve their inputs from the active scenario + the Fabric client (reuse
`sigantry_core/auth/diagnose.py` and the existing API client). Initial catalogue:

| Check id | Stage | Asserts |
|----------|-------|---------|
| `identity_has_role` | pre_deploy | active identity holds a role on `workspace_id` (matches `auth_mode`) |
| `env_wheel_version` | pre_deploy | `environment_id` publishes each `required_wheels` spec |
| `notebook_magics` | pre_deploy | no line-leading `%`/`!` in manifest notebooks (when `forbid_cell_magics`) |
| `notebook_source_shape` | pre_deploy | every `cells[].source` is list-form |
| `publish_flags` | pre_deploy | preview APIs acknowledged + `--republish-existing` for existing items |
| `lakehouse_artifacts` | pre_deploy | every `required_lakehouse_files` path exists & non-empty |
| `bindings_present` | post_deploy | each deployed item's `metadata.dependencies` has env + lakehouse |

New checks register the same way plugins do, so tenant plugins can contribute
domain-specific checks.

### 3. CLI surface (consistent with `doctor`)

```
sigantry preflight --scenario prod [--stage pre_deploy] [--strict] [--json]
```
- Renders a rich table (Check · Stage · Severity · Status · Detail), like `doctor`.
- `--strict` → non-zero exit if any **blocker** failed (CI gate); `warn` never fails.
- `--stage` filters (run `post_deploy` checks after a deploy + re-bind).
- `--json` for machine consumption / pipeline steps.

**Optional integration (phase 2):** `sync apply --preflight <scenario>` runs the
`pre_deploy` checks first and aborts on a blocker, so the happy path is a single
command. Kept **opt-in** to preserve the ADR-0012 deploy/run boundary.

## Alternatives considered

- **Status quo (ad-hoc shell + a docs checklist).** What teams do today. Works, but is
  un-enforced, copy-paste-fragile, and re-discovered per operator. A docs field guide is
  necessary but not sufficient.
- **Hardcode checks in `sync apply`.** Rejected: pre-conditions are environment-specific
  (GUIDs, auth mode, required artifacts/wheels), and baking them in violates the
  vendor-agnostic posture. Config-driven keeps sigantry generic and the topology in the
  consumer's `.fabric-dataops.toml`.
- **Extend `doctor` instead of a new subapp.** `doctor` is plugin/trust-scoped and takes
  no scenario. A sibling `preflight` subapp reuses the table/`--strict` machinery without
  overloading `doctor`'s meaning. (Could alias as `doctor preflight` if preferred.)

## Consequences

- **Positive.** Failures move from runtime (expensive multi-minute pipeline round-trips)
  to a seconds-long pre-deploy gate; the deploy procedure becomes self-documenting via
  config; CI can gate on `--strict`; new gotchas become new checks, not new tribal
  knowledge.
- **Cost.** New config surface to document + validate; checks need the Fabric client and
  must degrade gracefully when a scenario omits optional keys; `post_deploy` checks imply
  a two-call flow (deploy → re-bind → `preflight --stage post_deploy`).
- **Compatibility.** Additive only — `[preflight]` is ignored by older versions
  (`extra="allow"`); no change to `sync apply`/`set-binding` semantics.

## Rollout (proposed phases)

1. **MVP:** `preflight` subapp + scenario loader + the 5 highest-value pre_deploy checks
   (`identity_has_role`, `env_wheel_version`, `notebook_magics`, `publish_flags`,
   `lakehouse_artifacts`) with `--strict`/`--json`. Unit tests with a faked client.
2. **Post-deploy:** add `bindings_present` + `notebook_source_shape`; document the
   deploy → re-bind → `preflight --stage post_deploy` loop.
3. **Integration:** opt-in `sync apply --preflight <scenario>`; plugin-contributed checks.

## Open questions

- Should `auth_mode` actively *select* the credential (drive `DefaultAzureCredential` vs
  an SPN profile), or only *assert* the resolved identity matches? (Lean: assert in MVP,
  select in a later phase.)
- Where do `required_wheels` version specs get their truth — pinned in config (shown
  here) or derived from the manifest/lockfile?
- Do `post_deploy` checks belong to `preflight` or a separate `postflight`/`verify`
  verb? (Lean: one `preflight` verb with `--stage`, to keep the mental model small.)
