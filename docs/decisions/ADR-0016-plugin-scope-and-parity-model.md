# ADR-0016 -- plugin scope & parity model (customer-specifics only; generics in core)

- **Status:** Proposed
- **Date:** 2026-06-17
- **Milestone:** v3.x
- **Deciders:** platform team (TBD — review gate before adopting the principle as a CI check)
- **Provenance:** distilled from a real "do we still need the customer plugins, and how do
  we bring them to parity with core without duplicating effort?" review. The trigger was a
  downstream tenant operating entirely on `sigantry-core` and asking whether its customer
  plugin was carrying its weight. Per the vendor-neutral boundary, the customer-specific
  remediation that came out of that review is recorded in **that plugin's own changelog**,
  not here; this ADR captures only the vendor-agnostic decision.

## Context

`sigantry-core` declares **11 seams** (entry-point groups): `deploy_profiles`,
`dq_gates`, `telemetry_sinks`, `auth_providers`, `runbook_registries`,
`capacity_policies` (the 6 v2 seams) and `work_item_providers`, `pr_review_bots`,
`notification_sinks`, `secret_stores`, `approval_gates` (the 5 v3 seams).

A recurring worry is that a customer plugin is "behind" core and must "catch up" by
implementing the seams it lacks. (The reference customer plugin implements the 6 v2
seams and none of the v3 seams; the second-customer scaffold implements 2 v3 seams as
deliberate, forkable stubs.)

The worry rests on a false premise. **The v3 advancements already ship working,
vendor-generic reference implementations inside `sigantry-core` itself**, verified in
the tree:

- `sigantry_core/workitems/{ado,github}.py`
- `sigantry_core/notifications/{teams,slack,email}.py`
- `sigantry_core/secrets/{key_vault,github_secrets,ado_variable_group}.py`
- `sigantry_core/approval_gates/{ado_environments,github_environments,opa_hook}.py`
- `sigantry_core/pr_bot/providers/{ado,github}.py`

These are Azure/GitHub/ADO generics, not customer-specific. A consumer reaches them by
**config** (e.g. `[notifications].sink = "teams"`), not by writing a plugin. So a plugin
that re-implements them would be **duplicated, non-reusable effort** — exactly what the
product (config-driven, reusable by any tenant) exists to avoid.

The audit also found that what shared REST/auth/retry boilerplate exists is **already**
de-duplicated in core via `sigantry_core/client/base.py` (`BaseRestClient` +
`execute_with_retry`), which the core ado/github providers compose. There is no
auth/retry copy-paste to "promote".

What the audit *did* find were two genuine defects in the reference customer plugin:
customer-specifics baked into **plugin code** (not config), which blocked reuse — a
`RunbookRegistry` whose wiki org/project base URL was a hardcoded module constant, and a
`TelemetrySink` that validated its stream name against a **closed**, plugin-owned tuple
(so a second customer had to fork to add a stream). Both were fixed in the plugin by
turning the customer value into a config field that defaults to the plugin's own value;
the specifics are in that plugin's changelog.

## Decision

**1. Redefine "parity".** Parity is **not** "every plugin implements every seam." It is:

> A plugin contains **only what is irreducibly customer-specific**. Everything else is
> **config over a core-provided generic**. A plugin is "at parity" when a new customer
> can run the full product by *configuring* core's built-ins, and the plugin holds only
> the bits that genuinely cannot be expressed as config.

Stop measuring a customer plugin against the 11-seam list. By this definition the
reference customer plugin is already ~90% aligned and needs **zero** v3-seam
implementations.

**2. Placement rule (where does code go?).** For any new behaviour:

- Generic to a vendor/protocol (Azure, GitHub, ADO, Fabric, OPA) → **core**, behind a
  seam, selected by config. Ships in OSS per ADR-0010.
- Specific to one customer's tenant/org/naming/policy → that customer's **plugin**, and
  even there expressed as **config defaults**, never as a value a second customer would
  have to edit code to change.
- A value a second customer would need to change → it is **config**, full stop. If it is
  currently a module constant or a closed enum, that is a defect.

**3. The second-customer scaffold is a litmus test, not a parallel product.** Its job is
to *falsify* the config-driven claim: if a second customer can stand up on **config + a
thin plugin of only-tenant-specifics** (no logic copied from core or another plugin), the
architecture works. Each thing the scaffold must copy is a promote-to-core signal — but
only then, not speculatively. Do **not** feature-match one customer plugin to another.

## Remediation status

**Done (this change set):** the two reference-plugin defects above were fixed in that
plugin (config field + default, backward-compatible, with tests). Details live in the
plugin's own changelog per the vendor-neutral boundary.

**Evaluated and deliberately NOT done (with rationale):**

| Candidate (raised in audit) | Verdict | Rationale |
|---|---|---|
| `BaseWebhookSink` shared by Teams/Slack/Email + scaffold sink | **Defer** | Teams/Slack/Email already live in core and are parity-tested (byte-identical comment/card invariants). The scaffold's self-contained webhook POST is *intentional* — it is meant to be forked standalone; coupling it to a core base reduces its value. Revisit only if a real (non-scaffold) third webhook sink appears. |
| `BaseWorkItemProvider` mixin for auth/retry | **Already handled** | Core ado/github compose `BaseRestClient` + `execute_with_retry` (`sigantry_core/client/base.py`). No duplication to extract. |
| Promote an Entra/`DefaultAzureCredential` helper to core | **Defer (YAGNI)** | Only one plugin needs Entra-group auth today. Promote when a second consumer needs Azure auth, not before. |
| Codify the lazy-import (`_load_*()`) pattern as a core util | **Defer (cosmetic)** | Pure DRY; no correctness or reuse impact. Low priority. |
| A customer plugin implements the 5 v3 seams | **Do not do** | Core ships the generics; the plugin configures them. This is the duplicated effort the product avoids. |
| Add `api_version` to plugin Protocol impls | **Do not do (now)** | Per ADR-0004 the field lands cross-seam together in a later release; adding it early in plugins is throwaway. |

## Consequences

- The imagined "parity backlog" (a plugin must implement WorkItemProvider/SecretStore/…)
  is **deleted**. That work should not happen.
- New customer onboarding is a **config + thin-plugin** exercise, validated by the
  second-customer scaffold.
- A lightweight CI/lint check could enforce the placement rule (flag new module-level
  customer-identifying constants — org URLs, tenant ids, closed name enums — in plugin
  packages). Proposed as a follow-up, not part of this ADR's acceptance. Note this would
  complement the existing vendor-neutral grep gate
  (`tests/prereqs/test_phase8_banned_apis.py`), which already keeps customer branding out
  of the base tree.
- Plugin authors get a checklist (below).

### Plugin author checklist (the placement rule, operationalised)

1. Is this value something a *different* customer would change? → it is **config**
   (constructor kwarg + a `*Settings` field), with the customer's current value as the
   default. Never a closed enum or module constant they must fork to change.
2. Is the *mechanism* generic (a vendor/protocol)? → it belongs in **core** behind a
   seam, not in the plugin.
3. Does it duplicate something already in core (REST/auth/retry, an existing sink)? →
   compose core, don't copy.
4. Register only under the canonical `sigantry.<seam>` entry-point group.
5. Accept config via the `_dispatch` contract: either `**cfg` kwargs on `__init__` or a
   `from_settings(cls, settings: dict)` classmethod.
6. Keep customer branding out of the base tree (the grep gate enforces this); plugin
   specifics live in the plugin package and its own changelog/docs.

### Contract-suite skip policy (added 2026-09-20)

The seam contract batteries run each implementation against its seam's contract
fixture, and each real-implementation arm is guarded by `pytest.importorskip` so the
suite stays green where a plugin is not installed. Given this ADR's decision --
customer plugins live outside core -- that skipping is **correct by design** on a
clean CI runner, and the Fake-double arm of every battery still runs there, so the
seam protocols are always exercised.

What is **not** decided, and is recorded here as the gap:

- **The skip is silent and unbounded.** Nothing asserts a floor on how many contract
  tests actually executed, so the number can drift to zero without a red build.
  Measured on the v1.0.0 CI run: 20 tests skipped, spanning six seam batteries that
  lose their real-implementation arm entirely (auth_provider, capacity_policy,
  deploy_profile, dq_gate, runbook_registry, telemetry_sink) plus notification_sink,
  workitem_provider and one doctor test.
- **The local suite is stronger than CI by accident.** Those same tests run locally
  only because a maintainer's environment happens to have plugin distributions
  editable-installed from an unrelated tree. That is machine state, not policy: the
  stronger run is not reproducible and the weaker run is not detected.

**The decision this ADR records: the skip is intended; its invisibility is not.** A
minimum-run floor -- assert that at least N contract tests executed, failing the
build when the count falls below the number this ADR expects on a clean runner --
is the executable half and is deliberately **not** added here, because turning it on
can red `main` and belongs with the rest of the enforcement-chain work. Any future
change to which seams ship reference implementations must update that expected count
in the same commit.

## Related

- ADR-0004 (api-version policy) — why plugins do not add `api_version` yet.
- ADR-0010 (commercial model) — all seam reference impls ship in OSS core.
- ADR-0011 (rename to sigantry) — canonical `sigantry.<seam>` entry-point groups.
- ADR-0014 (plugin trust model) — `doctor` trust/allowlist for discovered plugins.
- `docs/reference/seams.md` — the 11-seam catalogue and reference impls.
