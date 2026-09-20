# Protocol seams (v2.0 / v3.0)

`sigantry-core` ships **11** `typing.Protocol` classes that define the plugin
surface for the agnostic base: the **six v2 seams** (landed v2.0) plus the **five
v3 seams** (`WorkItemProvider` Phase 11, `PrReviewBot` Phase 14, and
`NotificationSink` / `SecretStore` / `ApprovalGate` Phase 16). Plugins implement
one or more seams, register under the matching `sigantry.<seam>` entry-point group,
and are composed by `FabricDataOps.from_config()`.

This page details the **v2 contracts** (the six below) plus `WorkItemProvider`.
The **authoritative full catalogue for all 11 seams** — including the other v3
seams and their reference implementations — is [`seams.md`](seams.md).

> Entry-point groups use the canonical `sigantry.<seam>` namespace. The legacy
> `fabric_dataops_toolkits.*` groups were dropped from first-party packages per
> [ADR-0011](../decisions/ADR-0011-rename-to-sigantry.md) (v3.1 shim-drop); core
> still dual-reads them as a third-party grace window.

## Table of seams

| Seam | Entry-point group | Responsibility |
|------|-------------------|----------------|
| `DeployProfile` | `sigantry.deploy_profiles` | Plan-then-apply workspace deployment |
| `DataQualityGate` | `sigantry.dq_gates` | Run a DQ suite against a data reference |
| `TelemetrySink` | `sigantry.telemetry_sinks` | Accept structured telemetry events |
| `AuthProvider` | `sigantry.auth_providers` | Mint tokens/secrets for a scope |
| `RunbookRegistry` | `sigantry.runbook_registries` | Resolve alert name to runbook URL |
| `CapacityPolicy` | `sigantry.capacity_policies` | Plan-then-apply capacity actions |
| `WorkItemProvider` | `sigantry.work_item_providers` | Link a release to ADO Work Items / GitHub Issues (v3.0) |
| `PrReviewBot` | `sigantry.pr_review_bots` | Post TMDL / Lakehouse diffs on a PR (v3.0; see [`seams.md`](seams.md)) |
| `NotificationSink` | `sigantry.notification_sinks` | Send deploy/alert notifications (v3.0; see [`seams.md`](seams.md)) |
| `SecretStore` | `sigantry.secret_stores` | Get/set secrets via a backend (v3.0; see [`seams.md`](seams.md)) |
| `ApprovalGate` | `sigantry.approval_gates` | Request/await deployment approval (v3.0; see [`seams.md`](seams.md)) |

## DeployProfile

Plan-then-apply deployment of a Fabric workspace. Plugin packages
implement this seam and register the class under
`sigantry.deploy_profiles`.

```python
@runtime_checkable
class DeployProfile(Protocol):
    name: str
    def plan(self, ctx: DeployContext) -> DeployPlan: ...
    def apply(self, ctx: DeployContext, plan: DeployPlan) -> DeployResult: ...
```

## DataQualityGate

Run a DQ suite against a `DataRef`. Returns a `GateResult` that the
caller uses to fail fast before promotion.

```python
@runtime_checkable
class DataQualityGate(Protocol):
    name: str
    def run(self, suite: str, data_ref: DataRef) -> GateResult: ...
```

## TelemetrySink

Accept structured telemetry events. Plugins wrap Log Analytics, App
Insights, OTel, etc.

```python
@runtime_checkable
class TelemetrySink(Protocol):
    name: str
    def emit(self, event: TelemetryEvent) -> None: ...
    def flush(self, timeout_s: float = 5.0) -> None: ...
```

`sigantry_core.testing.InMemoryTelemetrySink` is a drop-in
double for unit tests.

## AuthProvider

Mint a token or secret for a given scope. Plugins wrap
`DefaultAzureCredential`, workload identity, `kv://` URIs, etc.

```python
@runtime_checkable
class AuthProvider(Protocol):
    name: str
    def get_token(self, scope: str, *, tenant_id: str | None = None) -> Secret: ...
```

## RunbookRegistry

Resolve a fired alert name to a runbook URL. Returns `None` if no
runbook is mapped; callers degrade to a default.

```python
@runtime_checkable
class RunbookRegistry(Protocol):
    name: str
    def resolve(self, alert_name: str) -> str | None: ...
```

`sigantry_core.testing.StaticRunbookRegistry` wraps a
`dict[str, str]` and is the simplest useful implementation.

## CapacityPolicy

Symmetric to `DeployProfile`: plan a list of actions, then apply them.
Used for capacity scaling, pause/resume, auto-scale policy enforcement.

```python
@runtime_checkable
class CapacityPolicy(Protocol):
    name: str
    def plan(self, ctx: CapacityContext) -> list[CapacityAction]: ...
    def apply(
        self, ctx: CapacityContext, actions: list[CapacityAction]
    ) -> CapacityApplyResult: ...
```

## WorkItemProvider (v3.0)

Cross-VCS work-item integration seam -- links a Sigantry release to ADO Work Items
or GitHub Issues. Introduced in v3.0 (Phase 11, the productisation wedge).
Reference implementations `AdoWorkItemProvider` and `GithubWorkItemProvider`
ship in `sigantry-core` itself (per [ADR-0011](../decisions/ADR-0011-rename-to-sigantry.md)
and the seam-map's "both impls in base" rule for dual-CI parity).

### Method set

```python
@runtime_checkable
class WorkItemProvider(Protocol):
    name: str

    def link_release(
        self,
        release_id: str,
        work_items: list[str],
        deploy_record: "DeployRecord",
    ) -> None: ...

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]: ...

    def ping(self) -> None: ...
```

Companion value object `WorkItem`:

```python
@dataclass(frozen=True, slots=True)
class WorkItem:
    id: str
    title: str
    work_item_type: str
    state: str
    assigned_to: str | None
    provider_name: str
    raw_fields: dict[str, Any] = field(default_factory=dict)
```

### SemVer commitment

**v3.0 stable.** Adding a method to the Protocol is a breaking change for
implementers; removing one is a breaking change for consumers. Both bump
the major version of `sigantry-core`. **All 11 seams omit `api_version` at
v3.0/v3.1** (per [ADR-0004](../decisions/ADR-0004-api-version-policy.md)); a
planned post-v3.1 cross-seam widening adds `api_version: str` to every seam
together rather than per-seam.

### Traceability

- Requirement: TRACE-01 (Phase 11 -- Work-Item Traceability Wedge).
- Audit-plane interaction: implementations call into the non-pluggable
  observation plane via `sigantry_core.governance.audit.emit_deploy_record`;
  see [observation-planes.md](observation-planes.md) for the rationale.

## Supporting value objects

Every context/result type is a `@dataclass(frozen=True, slots=True)`
immutable value object. Plugins must not mutate them in place.

- `DeployContext`, `DeployPlan`, `DeployResult`
- `DataRef`, `GateResult`
- `TelemetryEvent`, `Secret`
- `CapacityContext`, `CapacityAction`, `CapacityApplyResult`

## SemVer commitment

`sigantry-core` follows [Semantic Versioning 2.0][semver]. The
protocol shapes on this page define the plugin contract:

- **Breaking change** -- any signature change, field removal, or
  semantics change on a protocol bumps the **major** version of
  `sigantry-core`.
- **Additive change** -- a new protocol, a new optional field with a
  default, or a new helper method bumps the **minor** version.
- **Fix / doc** -- no signature change bumps the **patch** version.

[semver]: https://semver.org/spec/v2.0.0.html

### Why no `api_version` field?

Per the YAGNI watchlist and [ADR-0004](../decisions/ADR-0004-api-version-policy.md),
the protocols ship **without** an `api_version` class var — it remains **absent at
v3.0/v3.1**. Adding it at seam birth was speculative (no prior versions to gate
against); it is deferred to a single post-v3.1 cross-seam change rather than added
per-seam. Plugins must **not** declare `api_version` until then.

### Where breaking changes land

Breaking changes to any seam are announced in `CHANGELOG.md` under a
`### Breaking` subheading and block release until the migration guide
in `docs/migration/` is updated. See `docs/release-process.md` for the
full release checklist.

## Security note

Plugin installation is trust-equivalent to `pip install`. The base
toolkit cannot defend against hostile packages installed into the same
virtual environment. **Install plugins only from trusted feeds**
(Azure Artifacts, private PyPI, pinned git SHA). Entry-point discovery
runs plugin import code on the first `FabricDataOps.from_config()`
call.

Namespaced plugin tables in `.sigantry.toml` pass through to the
plugin's own pydantic model. **Do not put secrets in the TOML file.**
Use env-var overrides (`SIGANTRY_<SECTION>__<KEY>`) or `kv://` references
resolved by the configured `AuthProvider`.

## See also

- [`sigantry_core.registry`](../api/index.md) -- plugin
  registry and entry-point discovery.
- [`sigantry_core.config`](../api/index.md) --
  `pydantic-settings` loader for `.sigantry.toml`.
- [`sigantry_core.api.FabricDataOps`](../api/index.md) --
  public front door composing registry + config + seams.
- [`sigantry_core.testing`](../api/index.md) -- in-memory
  doubles for contract tests.
