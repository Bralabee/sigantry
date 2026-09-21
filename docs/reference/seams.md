# Seams Reference -- All 11 Protocol Seams (v3.0)

**Audience:** Plugin authors integrating with Sigantry's vendor-agnostic
core. This document is the authoritative per-seam reference for the 11
Protocol seams that comprise the SemVer-committed plugin surface as of
Sigantry v3.0.

**Source of truth:** [`sigantry_core/protocols.py`](https://github.com/hs2labs/sigantry/blob/master/sigantry_core/protocols.py)
-- this document is a hand-maintained mirror; when in doubt the Python
type hints in `protocols.py` win.

**SemVer commitment:** all 11 seams are stable at v3.0. Adding fields to a
seam method signature is a SemVer-minor bump; removing fields is a
SemVer-major bump. The `api_version` field is **deferred** to a
post-v3.1 minor (ADR-0004) -- cross-seam widening is preferred over
per-seam asymmetry.

**Cross-references:**

- [Reference > Protocol seams](protocols.md) -- the v2.0 reference (6 seams)
- [Reference > Seam map](seam-map.md) -- BRIEF-04 high-level catalogue
- [Reference > Observation planes](observation-planes.md) -- the
  non-pluggable audit + telemetry layer
- [Migration > 2.x -> 3.0](../migration/2.x-to-3.0.md) -- entry-point
  group rename for the existing 6 seams + the 5 net-new ones

---

## Overview

Sigantry's 11 seams are the SemVer-committed extension points where
plugin authors integrate vendor-specific code with the vendor-agnostic
core. Each seam is a `runtime_checkable typing.Protocol` in
`sigantry_core.protocols` with a single class variable `name: str`
(no `api_version` field at v3.0; see ADR-0004).

| Seam | Phase | Entry-point group | Reference impls (in `sigantry-core`) |
|------|-------|-------------------|--------------------------------------|
| `DeployProfile` | v2.0 (Phase 8) | `sigantry.deploy_profiles` | (consumer-side) |
| `DataQualityGate` | v2.0 (Phase 8) | `sigantry.dq_gates` | (consumer-side) |
| `TelemetrySink` | v2.0 (Phase 8) | `sigantry.telemetry_sinks` | `InMemoryTelemetrySink` (testing) |
| `AuthProvider` | v2.0 (Phase 8) | `sigantry.auth_providers` | (`TokenProvider` chain in core) |
| `RunbookRegistry` | v2.0 (Phase 8) | `sigantry.runbook_registries` | `StaticRunbookRegistry` (testing) |
| `CapacityPolicy` | v2.0 (Phase 8) | `sigantry.capacity_policies` | `NoopCapacityPolicy` (testing) |
| `WorkItemProvider` | v3.0 (Phase 11) | `sigantry.work_item_providers` | `AdoWorkItemProvider`, `GithubWorkItemProvider`, `JtoyeWorkItemProvider` (stub) |
| `PrReviewBot` | v3.0 (Phase 14) | `sigantry.pr_review_bots` | `AdoProvider`, `GithubProvider` |
| `NotificationSink` | v3.0 (Phase 16) | `sigantry.notification_sinks` | `TeamsNotificationSink`, `SlackNotificationSink`, `EmailNotificationSink`, `JtoyeNotificationSink` (stub) |
| `SecretStore` | v3.0 (Phase 16) | `sigantry.secret_stores` | `KeyVaultSecretStore`, `GithubSecretsSecretStore`, `AdoVariableGroupSecretStore` |
| `ApprovalGate` | v3.0 (Phase 16) | `sigantry.approval_gates` | `AdoEnvironmentsApprovalGate`, `GithubEnvironmentsApprovalGate`, `OpaApprovalGate` |

The reference impls span **all 11 seams** as of v3.0. Run
`sigantry doctor` to enumerate every plugin discovered on the current
PYTHONPATH (the authoritative, drift-free count).

---

## DeployProfile (v2.0, Phase 8)

```python
@runtime_checkable
class DeployProfile(Protocol):
    """Workspace deployment profile (plan then apply)."""

    name: str

    def plan(self, ctx: DeployContext) -> DeployPlan: ...
    def apply(self, ctx: DeployContext, plan: DeployPlan) -> DeployResult: ...
```

**Entry-point group:** `sigantry.deploy_profiles`
**Audit record:** `DeployRecord` -- `~/.sigantry/audit/deploys.jsonl`
(written by `sigantry release record`)

**Reference impls in `sigantry-core`:** none (consumer-supplied -- `sigantry-hs2`
ships `AimsDeployProfile`).

---

## DataQualityGate (v2.0, Phase 8)

```python
@runtime_checkable
class DataQualityGate(Protocol):
    """DQ suite runner producing a pass/fail GateResult."""

    name: str

    def run(self, suite: str, data_ref: DataRef) -> GateResult: ...
```

**Entry-point group:** `sigantry.dq_gates`
**Audit record:** none (gate result is logged via `TelemetrySink` not the
audit plane)

**Reference impls in `sigantry-core`:** `NoopGate` (testing); `sigantry-hs2`
ships `DqFrameworkGate`.

---

## TelemetrySink (v2.0, Phase 8)

```python
@runtime_checkable
class TelemetrySink(Protocol):
    """Structured telemetry emitter."""

    name: str

    def emit(self, event: TelemetryEvent) -> None: ...
    def flush(self, timeout_s: float = 5.0) -> None: ...
```

**Entry-point group:** `sigantry.telemetry_sinks`
**Audit record:** none (telemetry is the *business* observation plane;
audit is a separate non-pluggable plane -- see
[observation-planes.md](observation-planes.md))

**Reference impls in `sigantry-core`:** `InMemoryTelemetrySink` (testing);
`sigantry-hs2` ships `LogAnalyticsSink`.

---

## AuthProvider (v2.0, Phase 8)

```python
@runtime_checkable
class AuthProvider(Protocol):
    """Token minting for a given scope."""

    name: str

    def get_token(self, scope: str, *, tenant_id: str | None = None) -> Secret: ...
```

**Entry-point group:** `sigantry.auth_providers`
**Audit record:** none (auth is below the audit plane)

**Reference impls in `sigantry-core`:** `FakeAuth` (testing); the
`TokenProvider` chain in `sigantry_core.auth` is the production
default (DefaultAzureCredential + federated workload identity).

---

## RunbookRegistry (v2.0, Phase 8)

```python
@runtime_checkable
class RunbookRegistry(Protocol):
    """Resolve an alert name to a runbook URL (or None if unknown)."""

    name: str

    def resolve(self, alert_name: str) -> str | None: ...
```

**Entry-point group:** `sigantry.runbook_registries`
**Audit record:** none

**Reference impls in `sigantry-core`:** `StaticRunbookRegistry` (testing);
`sigantry-hs2` ships `Hs2TeamsRunbookRegistry`.

---

## CapacityPolicy (v2.0, Phase 8)

```python
@runtime_checkable
class CapacityPolicy(Protocol):
    """Capacity plan-then-apply policy."""

    name: str

    def plan(self, ctx: CapacityContext) -> list[CapacityAction]: ...
    def apply(self, ctx: CapacityContext, actions: list[CapacityAction]) -> CapacityApplyResult: ...
```

**Entry-point group:** `sigantry.capacity_policies`
**Audit record:** capacity actions flow through `@destructive_op` decorator
(see [observation-planes.md](observation-planes.md))

**Reference impls in `sigantry-core`:** `NoopCapacityPolicy` (testing);
`sigantry-hs2` ships `Hs2CapacityPolicy`.

---

## WorkItemProvider (v3.0, Phase 11)

```python
@runtime_checkable
class WorkItemProvider(Protocol):
    """Cross-VCS work-item integration seam (ADO Work Items / GitHub Issues)."""

    name: str

    def link_release(
        self,
        release_id: str,
        work_items: list[str],
        deploy_record: DeployRecord,
    ) -> None: ...

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]: ...
    def ping(self) -> None: ...
```

**Entry-point group:** `sigantry.work_item_providers`
**Audit record:** `DeployRecord` (Phase 11 wedge -- `link_release` carries
the audit hash forward into work-item comments so the trace is integrity-checked)

**Reference impls in `sigantry-core`:**

| Class | Module | Notes |
|-------|--------|-------|
| `AdoWorkItemProvider` | `sigantry_core.workitems.ado` | REST atop `BaseRestClient`; `DefaultAzureCredential` against scope `499b84ac-1321-427f-aa17-267ca6975798/.default`; comments POST uses `api-version=7.0-preview.3` (RESEARCH §Pitfall 1) |
| `GithubWorkItemProvider` | `sigantry_core.workitems.github` | REST atop `BaseRestClient`; PAT or GitHub App auth; App-auth via `sigantry_core.auth.github_app` (PyJWT[crypto] RS256) |
| `FakeWorkItemProvider` | `sigantry_core.testing.doubles` | In-memory test double |
| `JtoyeWorkItemProvider` | `sigantry-jtoye` (sibling pkg) | Stub demonstrating multi-org plugin authorship; JToye replaces with real impl on their fork |

**Runbook:** [Work-item traceability comment rendering](../runbooks/work-item-traceability/comment-rendering.md)

---

## NotificationSink (v3.0, Phase 16)

```python
@runtime_checkable
class NotificationSink(Protocol):
    """Operator-facing notification sink (Teams, Slack, email)."""

    name: str

    def send(self, event: NotificationEvent, *, channel: str | None = None) -> None: ...
    def ping(self) -> None: ...


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    title: str
    body: str
    level: NotificationLevel = "info"   # Literal["info","warning","error","critical"]
    properties: dict[str, Any] = field(default_factory=dict)
```

**Entry-point group:** `sigantry.notification_sinks`
**Audit record:** none (notifications are operator-facing, not audit-plane;
when emitted as part of a deploy the deploy itself is audited via
`DeployRecord`)

**Reference impls in `sigantry-core` + `sigantry-jtoye`:**

| Class | Module | Notes |
|-------|--------|-------|
| `TeamsNotificationSink` | `sigantry_core.notifications.teams` | Adaptive Card 1.5 default (Workflows webhook envelope per RESEARCH §Pitfall 1 / Office 365 Connectors deprecation May 2026); legacy MessageCard fallback via `format="messagecard"` constructor flag |
| `SlackNotificationSink` | `sigantry_core.notifications.slack` | Modern Block Kit `blocks` payload (legacy `attachments[].color` removed) |
| `EmailNotificationSink` | `sigantry_core.notifications.email` | stdlib `email.message.EmailMessage` (modern policy-aware API; replaces legacy `email.mime.text.MIMEText`) + `smtplib.SMTP` / `SMTP_SSL` |
| `FakeNotificationSink` | `sigantry_core.testing.doubles` | In-memory test double |
| `JtoyeNotificationSink` | `sigantry-jtoye` (sibling pkg) | Stub demonstrating multi-org plugin authorship |

The `sigantry_core.sync.notifications` legacy import path (Phase 13) is
preserved as an in-package deprecation shim; its removal is scheduled
via the V3.X-ROADMAP LEGACY-SURFACE-DROP candidate (Plan 16-01 Open-Q-1).

**Runbook:** none (configuration via `pydantic-settings`; per-impl
constructor docstrings carry the wire format)

---

## SecretStore (v3.0, Phase 16)

```python
@runtime_checkable
class SecretStore(Protocol):
    """Read-write secret persistence (KeyVault, GitHub, ADO)."""

    name: str

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...
    def ping(self) -> None: ...
```

**Entry-point group:** `sigantry.secret_stores`
**Audit record:** `SecretChangeRecord` -- written to
`~/.sigantry/audit/secret_changes.jsonl` on every `set` / `delete` call.
The record carries the operation + key + actor + timestamp;
**never the secret value** (Anti-Pattern: never log secrets).

**Reference impls in `sigantry-core`:**

| Class | Module | Notes |
|-------|--------|-------|
| `KeyVaultSecretStore` | `sigantry_core.secrets.key_vault` | `azure.keyvault.secrets.SecretClient` (Azure SDK uses `azure-core`, NOT httpx -- banned-API gate unaffected); soft-delete + LROPoller handled by SDK |
| `GithubSecretsSecretStore` | `sigantry_core.secrets.github_secrets` | libsodium SealedBox encryption via `pynacl>=1.6,<2.0` (canonical per [GitHub REST guide](https://docs.github.com/en/rest/guides/encrypting-secrets-for-the-rest-api)); `get` raises `SecretReadNotSupported` (API limitation per RESEARCH §Pitfall 5) |
| `AdoVariableGroupSecretStore` | `sigantry_core.secrets.ado_variable_group` | Read-modify-write semantics on `set` (preserves existing keys per RESEARCH §Pitfall 3); REST 7.1 `taskagent/variablegroups` |
| `FakeSecretStore` | `sigantry_core.testing.doubles` | In-memory test double |

**Audit-plane integration:** `SecretChangeRecord` is a frozen pydantic v2
model (`extra="forbid"`) in `sigantry_core.governance.records`. The
`emit_secret_change_record()` writer function in
`sigantry_core.governance.audit` appends to the jsonl with `fsync()` for
durability. The record never carries the secret value -- only the
operation type, key name, principal id, timestamp, and audit hash.

---

## ApprovalGate (v3.0, Phase 16)

```python
@runtime_checkable
class ApprovalGate(Protocol):
    """Approval-gate seam (ADO env / GitHub env / OPA)."""

    name: str

    def request(self, ctx: ApprovalContext) -> ApprovalRequest: ...
    def wait(self, request: ApprovalRequest, timeout_s: int = 3600) -> ApprovalOutcome: ...
    def ping(self) -> None: ...


# Outcome is the Literal["approved", "rejected", "timeout"]
ApprovalOutcome = Literal["approved", "rejected", "timeout"]
```

**Entry-point group:** `sigantry.approval_gates`
**Audit record:** `ApprovalRecord` -- written to
`~/.sigantry/audit/approvals.jsonl` on every `request` + `wait`. Distinguishes
client-side timeout from server-side timeout via `last_observed_status`.

**Reference impls in `sigantry-core`:**

| Class | Module | Notes |
|-------|--------|-------|
| `AdoEnvironmentsApprovalGate` | `sigantry_core.approval_gates.ado_environments` | Polls `pipelines/approvals/{id}` REST 7.1 every 30s (RESEARCH §Pitfall 4); deployer pauses on the YAML environments approval |
| `GithubEnvironmentsApprovalGate` | `sigantry_core.approval_gates.github_environments` | Polls `actions/runs/{run_id}/deployment_protection_rule` POST; same 30s cadence |
| `OpaApprovalGate` | `sigantry_core.approval_gates.opa_hook` | Synchronous OPA HTTP API (`POST /v1/data/<path>`); OSS-pure escape hatch per RESEARCH §Pitfall 4 |
| `FakeApprovalGate` | `sigantry_core.testing.doubles` | In-memory test double |

**Audit-plane integration:** `ApprovalRecord` is a frozen pydantic v2
model in `sigantry_core.governance.records`. The
`emit_approval_record()` writer function in
`sigantry_core.governance.audit` appends to the jsonl on every
`request` + `wait` call so the operator trace is integrity-checked.
(Unkeyed chain -- see [audit ledger threat model](audit-ledger-threat-model.md).)

**Runbook:** [OPA Approval Gate Quickstart](../runbooks/approval-gates/opa-quickstart.md)

---

## PrReviewBot (v3.0, Phase 14)

```python
@runtime_checkable
class PrReviewBot(Protocol):
    """Cross-VCS PR-comment seam (GitHub / Azure DevOps / future providers)."""

    name: str

    def ping(self) -> None: ...
    def get_pr(self, pr_id: str) -> PullRequest: ...
    def get_changed_files(self, pr_id: str) -> list[ChangedFile]: ...
    def post_comment(self, pr_id: str, body: str) -> str: ...
```

**Entry-point group:** `sigantry.pr_review_bots`
**CLI binding:** `sigantry pr-bot run --provider {github,ado,auto}` resolves a
`PrReviewBot` and calls `ping()` -> `get_pr()` -> `get_changed_files()` ->
`post_comment()`. The Markdown body comes from
`sigantry_core.pr_bot.payload.render_markdown` and is **byte-identical across
providers** (STARTER-07 invariant), enforced by
`tests/sigantry_core/pr_bot/test_provider_post_body_parity.py`.

**Value objects:** `PullRequest` and `ChangedFile` normalise GitHub's and ADO's
shapes onto one literal alphabet (`added`/`modified`/`removed`/`renamed`) so
cross-provider bot logic reads a single shape.

**Reference impls in `sigantry-core`:**

| Class | Module | Notes |
|-------|--------|-------|
| `AdoProvider` | `sigantry_core.pr_bot.providers.ado` | Azure DevOps PR threads REST; strips `refs/heads/` from refs |
| `GithubProvider` | `sigantry_core.pr_bot.providers.github` | GitHub Issues/PR comments REST |

> Historical note (Audit-2026-05-07 W2.5): the same shape lived as
> `sigantry_core.pr_bot.providers.base.Provider` since Phase 14; the W2.5 lift
> moved the canonical definition into `sigantry_core.protocols` (matching the
> registry group name and every other seam) with a re-export alias at the old
> path for back-compat.

---

## SemVer Commitment Statement

Each Protocol method signature in `sigantry_core.protocols` is **stable at
v3.0**. Adopters can rely on:

- Field additions to method signatures requiring SemVer-minor bumps
- Field removals or method removals requiring SemVer-major bumps
- The full method set + the `name: str` class variable surviving across
  v3.x patch releases unchanged
- The `api_version` field being **absent** at v3.0/v3.1 (cross-seam
  widening deferred to a later minor per ADR-0004; the documented
  contract is the Protocol method set, not a runtime version field)

The `runtime_checkable` decoration means `isinstance(plugin, SeamProtocol)`
returns `True` for any class that satisfies the structural contract,
regardless of inheritance. JToye's plugin authors can implement the
seam without importing Sigantry at module-load time -- they just match
the method shape.

---

## Discovery + Doctor CLI

To enumerate every plugin discovered on the current PYTHONPATH:

```bash
sigantry doctor
```

The output table lists Group / Name / Module / Version / Status columns
across all 11 entry-point groups. Plugin import errors are recorded but
do not crash the doctor run; `sigantry doctor --strict` exits 1 on any
import error for CI gating.

---

*Reference authored: 2026-04-28 (Phase 16 / Plan 16-05)*
*Source of truth: `sigantry_core/protocols.py`*
