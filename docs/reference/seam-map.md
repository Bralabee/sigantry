# Sigantry Seam Map

**Status:** v3.0 working draft (2026-04-24, milestone v3.0 Phase 10, BRIEF-04)
**Companion:** [`protocols.md`](protocols.md) — the authoritative typing.Protocol definitions. This document describes **why** each seam exists and **when** it was / will be introduced.

## What is a seam?

A **seam** in Sigantry is a `typing.Protocol` class in `sigantry_core.protocols` that another package (a "plugin") can implement to swap out part of Sigantry's behaviour without touching `sigantry-core`.

Plugins register their implementations via Python entry points (discovered through `importlib.metadata`). The `sigantry_core.registry` resolves the configured implementation at runtime from `.sigantry.toml`. See [`protocols.md`](protocols.md) for the SemVer commitment model and the `api_version` policy (per [ADR-0004](../decisions/ADR-0004-api-version-policy.md)).

The **observation plane** — `governance.audit` and the `DeployRecord` write-through — is deliberately **not a seam**. It writes through standard-library logging to an immutable log so a misconfigured `TelemetrySink` cannot suppress audit evidence.

## Seam inventory

### Existing seams (shipped in v2.0.0, six total)

| Seam | Purpose | Introduced | SemVer | Reference implementation |
|------|---------|------------|--------|-------------------------|
| **`DeployProfile`** | How a workspace's items get assembled and handed to `fabric-cicd` for deploy | Phase 8 (v2.0.0) | v2.0 | `sigantry_hs2.AimsDeployProfile` (HS2 AIMS wheel-install pattern) |
| **`DataQualityGate`** | Pre/post-deploy quality checks that can block a promotion | Phase 8 (v2.0.0) | v2.0 | `sigantry_hs2.DqFrameworkGate` (HS2 DQ Framework integration) |
| **`TelemetrySink`** | Pluggable telemetry destination for deploys, DQ outcomes, capacity events | Phase 8 (v2.0.0) | v2.0 | `sigantry_hs2.LogAnalyticsSink` (DCR-based Log Analytics); `sigantry_core.testing.InMemoryTelemetrySink` |
| **`AuthProvider`** | How tokens are acquired for Fabric / ADO / GitHub / Key Vault across laptop, ADO, GHA, Fabric-notebook runtimes | Phase 8 (v2.0.0) | v2.0 | `sigantry_hs2.Hs2EntraGroupAuth` (HS2 SPN in `sg-fabric-automation`); base ships `DefaultAzureCredential` adapter |
| **`RunbookRegistry`** | Maps alert or event types to runbook URLs (+ Teams channel, owner, severity) | Phase 8 (v2.0.0) | v2.0 | `sigantry_hs2.Hs2TeamsRunbookRegistry` (HS2 wiki runbooks + Teams channels) |
| **`CapacityPolicy`** | Pause/resume/scale decisions for Fabric capacities (thresholds, hours, policy hooks) | Phase 8 (v2.0.0) | v2.0 | `sigantry_hs2.Hs2CapacityPolicy` (HS2 capacity SKU + quiet-hours rules) |

### New v3 seams (planned — not yet shipped)

These are introduced across v3.0. Each landing phase corresponds to a REQ block.

| Seam | Purpose | Introducing phase | SemVer at introduction | Reference implementations (target) |
|------|---------|-------------------|------------------------|------------------------------------|
| **`WorkItemProvider`** | Link a release to ADO work items or GitHub issues, write deploy-record comments back | Phase 11 (TRACE-01) | v3.0 | `AdoWorkItemProvider`, `GithubWorkItemProvider` (both ship in `sigantry-core`) |
| **`NotificationSink`** | Outbound notifications for drift, deploy events, approvals | Phase 16 (SEAM-01) | v3.0 | Teams, Slack, email (all in `sigantry-core`); JToye plugin ships one if needed |
| **`SecretStore`** | Resolve secrets for pipelines (Key Vault, GH secrets, ADO variable groups) | Phase 16 (SEAM-02) | v3.0 | Azure Key Vault, GitHub secrets, ADO variable group (all in `sigantry-core`) |
| **`ApprovalGate`** | Gate a promotion on a named approver or policy decision | Phase 16 (SEAM-03) | v3.0 | ADO environments, GitHub environments, OPA policy hook (all in `sigantry-core`) |
| **`PrReviewBot`** | Post semantic-model / Lakehouse schema diffs on a PR, on ADO and GitHub | Phase 14 (STARTER-05..07) | v3.0 | Single bot binary; ADO PR adapter + GitHub PR adapter |

Total when v3.0 ships: **11 seams** (6 existing + 5 new).

## Method-set sketches (v3 seams)

These are the SemVer-committed surfaces. Concrete type signatures land in `sigantry_core.protocols` during Phase 11 (TRACE-01) and Phase 16 (SEAM-01..03). The Phase 14 `PrReviewBot` surface lands as part of Phase 14 (STARTER-05).

### `WorkItemProvider` (Phase 11 — TRACE-01)

```python
@runtime_checkable
class WorkItemProvider(Protocol):
    name: str
    api_version: str = "v3.0"

    def link_release(
        self, release_id: str, work_items: list[str], deploy_record: DeployRecord
    ) -> None:
        """Write a structured comment back to each work item referencing release_id + audit_hash."""

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]:
        """Resolve work-item metadata (id, title, type, state, owner) for the given IDs."""

    def ping(self) -> None:
        """Contract: succeeds when the provider can reach its backend; raises on auth/network failure."""
```

Implementations: `AdoWorkItemProvider` (ADO REST, SPN or PAT auth) and `GithubWorkItemProvider` (GitHub REST, GitHub App or PAT auth). Both are in `sigantry-core` — required for dual-CI parity.

### `NotificationSink` (Phase 16 — SEAM-01)

```python
@runtime_checkable
class NotificationSink(Protocol):
    name: str
    api_version: str = "v3.0"

    def notify(self, event: NotificationEvent) -> None:
        """Send a notification event (drift, deploy, approval-requested, alert-routed, ...)."""

    def flush(self, timeout_s: float = 5.0) -> None: ...
```

Reference impls in `sigantry-core`: Teams (webhook or Workflow adapter), Slack (webhook or bot token), email (SMTP or SendGrid adapter).

### `SecretStore` (Phase 16 — SEAM-02)

```python
@runtime_checkable
class SecretStore(Protocol):
    name: str
    api_version: str = "v3.0"

    def get_secret(self, reference: str) -> str: ...

    def put_secret(self, reference: str, value: str) -> None:
        """Optional: raise NotImplementedError if the backing store is read-only."""
```

Reference impls in `sigantry-core`: Azure Key Vault (via `azure-keyvault-secrets`), GitHub Actions secrets (resolved via `gh` API during a run), ADO variable groups (resolved via `az devops` CLI / REST).

### `ApprovalGate` (Phase 16 — SEAM-03)

```python
@runtime_checkable
class ApprovalGate(Protocol):
    name: str
    api_version: str = "v3.0"

    def request_approval(self, context: ApprovalContext) -> ApprovalDecision:
        """Request approval; blocks or polls until an approver decides or the gate times out."""
```

Reference impls in `sigantry-core`: ADO environments (native approval stage), GitHub environments (required reviewers), OPA policy hook (declarative policy check — auto-approves or rejects based on input).

### `PrReviewBot` (Phase 14 — STARTER-05..07)

```python
@runtime_checkable
class PrReviewBot(Protocol):
    name: str
    api_version: str = "v3.0"

    def post_comment(self, pr: PullRequest, comment: Comment) -> None: ...

    def list_changed_files(self, pr: PullRequest) -> list[ChangedFile]: ...
```

Reference impls: one binary, two adapters — ADO PRs and GitHub PRs. Comment payload structure is identical on both (STARTER-07 enforces via diffable snapshot test).

## Why these five?

Each of the five v3 seams is **motivated by a specific requirement**, not speculative extension:

- `WorkItemProvider` is the **wedge**. Nothing off-the-shelf links Fabric deploys to work items. TRACE-01..08.
- `NotificationSink` is needed because **drift detection** (Phase 13) has to post findings somewhere, and **approval gates** (Phase 16) have to notify approvers. Introducing it in Phase 16 is a slight delay; Phase 13 uses a hard-coded fallback until Phase 16 lands. (See *Ordering note* below.)
- `SecretStore` is needed because JToye (Phase 16) may use a different secret backing than HS2 (Key Vault). SEAM-02 gives them a clean replacement point instead of forking.
- `ApprovalGate` is needed because ADO-environments-only gating is insufficient for GitHub CI paths, and policy-as-code (OPA) is a valid third option.
- `PrReviewBot` is needed because ADO PRs and GitHub PRs have different comment APIs but we want one bot binary; the Protocol is the abstraction boundary.

No extension point is added "just because". YAGNI is enforced.

## Ordering note — Phase 13 and `NotificationSink`

Phase 13 (Drift Detection) logically wants `NotificationSink` to post findings, but `NotificationSink` is introduced in Phase 16 (SEAM-01). Options:

1. **Chosen:** Phase 13 uses a hard-coded fallback (Teams webhook via the HS2 plugin's existing runbook-registry wiring). Phase 16 replaces the hard-code with `NotificationSink` resolution. The migration is internal; no user-facing breakage.
2. **Rejected:** Pull `NotificationSink` forward to Phase 13. Adds scope creep to Phase 13 and reduces the motivation for Phase 16.

The internal migration is documented in `.planning/phases/13-drift-detection/CONTEXT.md` when that phase plans.

## SemVer and `api_version`

Per [ADR-0004 — API version policy](../decisions/ADR-0004-api-version-policy.md), the
`api_version: str` class var is **deferred**: it is **absent** on every seam at v3.0/v3.1
and lands later as a single cross-seam change (cross-seam widening is preferred over
per-seam asymmetry). Until then, a plugin targets the version at which its seam was born
(v2.0 or v3.0) implicitly, and **must not** declare an `api_version` class var. This
matches the authoritative [`seams.md`](seams.md) SemVer statement.

When `api_version` does land, seam authors will **add methods** by bumping the seam's
`api_version` (`v3.1`, `v4.0`, …); removing methods requires a major-version bump.

See `protocols.md` for the `_check_api_compat` behaviour at resolve time.

## Plugin discovery quick reference

Entry-point groups (post-rename):

| Group | Seams (most common) |
|-------|---------------------|
| `sigantry.deploy_profiles` | `DeployProfile` |
| `sigantry.dq_gates` | `DataQualityGate` |
| `sigantry.telemetry_sinks` | `TelemetrySink` |
| `sigantry.auth_providers` | `AuthProvider` |
| `sigantry.runbook_registries` | `RunbookRegistry` |
| `sigantry.capacity_policies` | `CapacityPolicy` |
| `sigantry.work_item_providers` | `WorkItemProvider` *(new)* |
| `sigantry.notification_sinks` | `NotificationSink` *(new)* |
| `sigantry.secret_stores` | `SecretStore` *(new)* |
| `sigantry.approval_gates` | `ApprovalGate` *(new)* |
| `sigantry.pr_review_bots` | `PrReviewBot` *(new)* |

Legacy v2 groups (`fabric_dataops_toolkits.deploy_profiles` etc.): first-party
packages stopped declaring them in v3.1 per [ADR-0011](../decisions/ADR-0011-rename-to-sigantry.md).
The core registry still dual-reads the six legacy groups so third-party plugins
keep resolving; that read is scheduled for removal (V3.X-ROADMAP LEGACY-SURFACE-DROP).

## See also

- [`protocols.md`](protocols.md) — authoritative typing.Protocol definitions.
- [ADR-0004 — API version policy](../decisions/ADR-0004-api-version-policy.md) — how seams evolve in SemVer.
- [ADR-0010 — Commercial Model](../decisions/ADR-0010-commercial-model.md) — why all seam implementations ship in OSS `sigantry-core`, not a commercial package.
- [ADR-0011 — Rename to Sigantry](../decisions/ADR-0011-rename-to-sigantry.md) — entry-point group rename + shim strategy.
- [`observation-planes.md`](observation-planes.md) — why the audit plane is not a seam.
- [`dual-ci-strategy.md`](dual-ci-strategy.md) — why every ADO + GHA implementation must ship together.
- [PRODUCT-BRIEF.md](../PRODUCT-BRIEF.md) — the why behind the seams.

---

*Updated: 2026-04-24 (milestone v3.0 Phase 10, BRIEF-04).*
