"""Public protocol seams (PROD-01).

Eleven ``typing.Protocol`` classes defining the plugin surface. Each seam
carries ``name: str`` only; no ``api_version`` field at v2.0 (deferred to
v2.1 per PRODUCTIZATION.md YAGNI cut Section 10.1 -- no prior versions to
gate against at v2.0 birth). The seventh seam (``WorkItemProvider``,
introduced in v3.0, Phase 11) preserves that symmetry; cross-seam widening
to add ``api_version`` to all seams together is recorded as a deliberate
v3.1 candidate.

The eighth, ninth, and tenth seams (``NotificationSink``, ``SecretStore``,
``ApprovalGate``) introduced in v3.0 (Phase 16) preserve the same Phase 11
``name: str``-only convention. The eleventh (``PrReviewBot``, lifted to
this module by Audit-2026-05-07 W2.5; the same shape lived under
``sigantry_core.pr_bot.providers.base.Provider`` since Phase 14) follows
suit. Runtime Protocol membership does NOT enforce the api_version
commitment carried in ``docs/reference/seam-map.md`` as a documentation
convention. See ADR-0004 + 16-RESEARCH.md §3 + Open-Q-3 for the cross-seam
asymmetry rationale.

SemVer commitment: breaking changes to any protocol bump the major version of
``sigantry-core``. Plugin authors consume from
``sigantry_core.protocols`` ONLY (never an ``_internal`` module).

The eleven seams:

- ``DeployProfile`` -- "plan then apply" deployment of a Fabric workspace.
- ``DataQualityGate`` -- run a DQ suite against a data reference.
- ``TelemetrySink`` -- accept structured telemetry events.
- ``AuthProvider`` -- mint a token/secret for a scope.
- ``RunbookRegistry`` -- resolve an alert name to a runbook URL.
- ``CapacityPolicy`` -- "plan then apply" capacity actions.
- ``WorkItemProvider`` (v3.0, Phase 11) -- cross-VCS work-item linkage seam.
- ``NotificationSink`` (v3.0, Phase 16) -- operator-facing notification sink
  (Teams / Slack / email).
- ``SecretStore`` (v3.0, Phase 16) -- read-write secret persistence
  (KeyVault / GitHub / ADO).
- ``ApprovalGate`` (v3.0, Phase 16) -- approval-gate seam (ADO env /
  GitHub env / OPA).
- ``PrReviewBot`` (v3.0, Phase 14; lifted to this module by W2.5) --
  cross-VCS PR-comment seam (GitHub / Azure DevOps).

All supporting context/result types are ``@dataclass(frozen=True, slots=True)``
immutable value objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Forward-reference only -- ``DeployRecord`` lives in
    # ``sigantry_core.release.record`` (Plan 11-02). Importing it at runtime
    # would create a circular import; the Protocol method signature uses the
    # literal string ``"DeployRecord"`` so the seam stays definable
    # independently of any specific record-shape evolution.
    from sigantry_core.release.record import DeployRecord

# ---------------------------------------------------------------------------
# Supporting value objects (all frozen + slotted)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeployContext:
    """Runtime context passed into ``DeployProfile.plan`` / ``.apply``."""

    workspace_id: str
    environment: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeployPlan:
    """Output of ``DeployProfile.plan`` -- an ordered list of intended actions."""

    actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DeployResult:
    """Outcome of ``DeployProfile.apply``."""

    workspace_id: str
    items_published: int
    items_failed: int
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DataRef:
    """Opaque reference to the data a DQ gate evaluates."""

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of a DQ gate run."""

    suite: str
    success: bool
    violations: int
    evaluated: int
    run_id: str


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """Structured event emitted through a ``TelemetrySink``."""

    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None


@runtime_checkable
class Closeable(Protocol):
    """Optional contract for plugins holding resources that need explicit release.

    Plugins that hold an HTTP connection pool, a background thread, or any
    other resource that will leak on GC should implement ``close()``. The
    ``FabricDataOps`` context manager calls ``close()`` on every seam that
    satisfies this protocol at ``__exit__`` time.

    Not required: plugins without resources (e.g. ``NoopGate``) should not
    implement this -- zero-arg ``isinstance(plugin, Closeable)`` returns
    ``False`` and the context manager skips them.
    """

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class Secret:
    """Opaque secret/token returned by ``AuthProvider.get_token``.

    The string value is the token itself; ``expires_on`` (if known) is the UTC
    timestamp at which the token stops being valid.

    ``__repr__`` and ``__str__`` are scrubbed so the token cannot leak into
    exception messages, log lines, or a telemetry ``properties`` dict when a
    consumer accidentally puts a Secret there. Pass ``.value`` explicitly
    when a caller genuinely needs the token bytes.
    """

    value: str
    expires_on: datetime | None = None

    def __repr__(self) -> str:
        expires = f", expires_on={self.expires_on!r}" if self.expires_on is not None else ""
        return f"Secret(value='***'{expires})"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True, slots=True)
class CapacityContext:
    """Runtime context passed into ``CapacityPolicy.plan`` / ``.apply``."""

    capacity_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class CapacityAction:
    """One planned capacity action (e.g. scale up, pause, resume)."""

    kind: str
    target: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CapacityApplyResult:
    """Outcome of applying a list of ``CapacityAction``."""

    applied: int
    skipped: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WorkItem:
    """Cross-VCS work-item value object (ADO Work Item / GitHub Issue).

    Field set is intentionally minimal -- only fields that exist across
    every ADO process template (Agile / Scrum / CMMI / Basic) AND across
    GitHub Issues. Provider-specific extras flow through ``raw_fields``.

    Source: 11-RESEARCH.md Pitfall 2 (process-template variance).
    """

    id: str
    title: str
    work_item_type: str
    state: str
    assigned_to: str | None
    provider_name: str
    raw_fields: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol seams
# ---------------------------------------------------------------------------


@runtime_checkable
class DeployProfile(Protocol):
    """Workspace deployment profile (plan then apply)."""

    name: str

    def plan(self, ctx: DeployContext) -> DeployPlan: ...

    def apply(self, ctx: DeployContext, plan: DeployPlan) -> DeployResult: ...


@runtime_checkable
class DataQualityGate(Protocol):
    """DQ suite runner producing a pass/fail ``GateResult``."""

    name: str

    def run(self, suite: str, data_ref: DataRef) -> GateResult: ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Structured telemetry emitter."""

    name: str

    def emit(self, event: TelemetryEvent) -> None: ...

    def flush(self, timeout_s: float = 5.0) -> None: ...


@runtime_checkable
class AuthProvider(Protocol):
    """Token minting for a given scope."""

    name: str

    def get_token(self, scope: str, *, tenant_id: str | None = None) -> Secret: ...


@runtime_checkable
class RunbookRegistry(Protocol):
    """Resolve an alert name to a runbook URL (or ``None`` if unknown)."""

    name: str

    def resolve(self, alert_name: str) -> str | None: ...


@runtime_checkable
class CapacityPolicy(Protocol):
    """Capacity plan-then-apply policy."""

    name: str

    def plan(self, ctx: CapacityContext) -> list[CapacityAction]: ...

    def apply(self, ctx: CapacityContext, actions: list[CapacityAction]) -> CapacityApplyResult: ...


@runtime_checkable
class WorkItemProvider(Protocol):
    """Cross-VCS work-item integration seam (ADO Work Items / GitHub Issues).

    Introduced in v3.0 (Phase 11, the productisation wedge). The method set
    is SemVer-stable at v3.0 -- additions bump api_version when the cross-seam
    widening lands in v3.1 (currently deferred per the planner-mapper open-Q
    resolution in Phase 11; the existing six seams also omit api_version, so
    keeping symmetry is the consistency call).
    """

    name: str

    def link_release(
        self,
        release_id: str,
        work_items: list[str],
        deploy_record: DeployRecord,
    ) -> None:
        """Write a structured comment linking ``release_id`` to each work item.

        The ``deploy_record`` carries the ``audit_hash`` that proves the comment
        wasn't tampered with after-the-fact. Implementations MUST use the
        canonical comment formatter (``sigantry_core.workitems._payload``) so
        TRACE-06 byte-identical-comment parity holds across providers.
        """
        ...

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]:
        """Return the WorkItems for the given IDs.

        IDs are strings to span ADO integer IDs and GitHub issue numbers
        without coercing across providers. Order of the returned list is
        not required to match input order; consumers index by ``id``.
        """
        ...

    def ping(self) -> None:
        """Preflight authentication + connectivity check.

        Raises a typed exception (``AuthError`` for 401, ``HttpError`` for
        anything else) when the provider cannot reach the work-item service.
        Returns ``None`` on success.
        """
        ...


# ---------------------------------------------------------------------------
# v3.0 Phase 16 -- three new seams: NotificationSink, SecretStore, ApprovalGate.
# Each follows the Phase 11 WorkItemProvider precedent: ``name: str`` class var
# only, NO ``api_version`` field. Cross-seam widening to add api_version to all
# ten seams together is a v3.1 candidate (ADR-0004; 16-RESEARCH.md §3 +
# Open-Q-3 corrections to CONTEXT.md). The seam-map.md docstrings carry the
# api_version commitment as a documentation-only convention; runtime Protocol
# membership does NOT enforce it.
# ---------------------------------------------------------------------------


NotificationLevel = Literal["info", "warning", "error", "critical"]


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """Structured event the NotificationSink sends to the channel.

    ``level`` maps to provider-native severity in each impl (Teams
    ``themeColor`` for legacy MessageCard / Adaptive Card colour token,
    Slack ``attachments[].color`` or block accent, email ``X-Priority``
    header). ``properties`` is an open key/value dict allowing
    operator-defined fields (release_id, env, actor, ...).
    """

    title: str
    body: str
    level: NotificationLevel = "info"
    properties: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NotificationSink(Protocol):
    """Operator-facing notification sink (Teams, Slack, email).

    Reference impls (Plan 16-01) compose ``BaseRestClient`` for HTTP-based
    sinks (Teams / Slack); the email impl uses stdlib ``smtplib`` +
    ``email.message.EmailMessage``. Per 16-RESEARCH.md §Pattern 3 the
    Teams default payload is Adaptive Card 1.5 with a MessageCard fallback
    selectable via ``format="messagecard"`` (RESEARCH §Pitfall 1: Office 365
    Connectors webhooks deprecated May 2026 -- Teams users SHOULD migrate to
    Workflow webhook URLs).
    """

    name: str

    def send(self, event: NotificationEvent, *, channel: str | None = None) -> None: ...

    def ping(self) -> None: ...


@runtime_checkable
class SecretStore(Protocol):
    """Read-write secret persistence (KeyVault, GitHub, ADO).

    ``get`` returns ``str | None`` (None when the key is absent in stores
    that allow that semantic). Some backends do NOT permit reading values --
    notably GitHub Actions secrets, where ``get`` raises
    ``SecretReadNotSupported`` (16-RESEARCH.md §Pitfall 5). ADO variable
    groups follow a read-modify-write semantic on ``set`` so existing keys
    are preserved (RESEARCH §Pitfall 3).

    ``set`` and ``delete`` write a structured audit line via the audit-plane
    (Phase 11 wedge); the line carries the operation + key + actor +
    timestamp BUT NEVER the secret value (Anti-Pattern: never log secrets).
    """

    name: str

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def list_keys(self, prefix: str = "") -> list[str]: ...

    def ping(self) -> None: ...


ApprovalOutcome = Literal["approved", "rejected", "timeout"]


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """Input to ``ApprovalGate.request`` -- caller's view of the release.

    ``approval_id`` is supplied by ADO's environments approval mechanism
    (the deployer pauses on the YAML and ADO assigns an approval id); for
    OPA / GitHub-environments impls the ``request_id`` returned by
    ``request()`` is generated client-side via ``uuid4()``.
    """

    release_id: str
    env: str
    approvers: list[str] = field(default_factory=list)
    approval_id: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Returned by ``ApprovalGate.request`` -- handle for the pending approval."""

    request_id: str
    release_id: str
    env: str
    approvers: list[str]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Decision payload (deciding party + timestamp + comment).

    Used by audit-plane records; the Protocol's ``wait`` method returns the
    ``ApprovalOutcome`` literal directly to keep the seam minimal.
    """

    outcome: ApprovalOutcome
    decided_by: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None


# ---------------------------------------------------------------------------
# PrReviewBot seam (Phase 14 / Audit-2026-05-07 W2.5)
# ---------------------------------------------------------------------------
#
# The ``sigantry.pr_review_bots`` registry group is reserved for plugins that
# cross-VCS post a deploy-summary comment on a pull request. Phase 14 shipped
# the seam under the alias ``Provider`` inside
# ``sigantry_core.pr_bot.providers.base``; W2.5 lifts it to the top-level
# ``sigantry_core.protocols`` module so the Protocol matches the registry
# group name and joins the canonical 11-protocol surface that
# ``FabricDataOps`` widens to in W2.4. The legacy ``Provider`` /
# ``ChangedFile`` / ``PullRequest`` symbols in ``pr_bot.providers.base``
# remain as re-export aliases for back-compat.


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """A single file changed in a pull request.

    The change-type vocabulary normalises GitHub's ``status`` strings
    (``added`` / ``modified`` / ``removed`` / ``renamed``) and ADO's
    ``changeType`` strings (``add`` / ``edit`` / ``delete`` / ``rename``)
    onto a common literal alphabet so cross-provider PR-bot logic can
    read one shape only.
    """

    path: str
    change_type: Literal["added", "modified", "removed", "renamed"]
    previous_path: str | None = None


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A pull-request value-object normalised across providers.

    ``base_ref`` / ``head_ref`` are unqualified branch names. ADO returns
    fully-qualified refs (``refs/heads/main``); the AdoProvider strips the
    prefix. ``provider_name`` is the fixed string ``"github"`` or ``"ado"``.
    """

    id: str
    title: str
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    provider_name: str


@runtime_checkable
class PrReviewBot(Protocol):
    """Cross-VCS PR-comment seam (GitHub / Azure DevOps / future providers).

    Reserved registry group: ``sigantry.pr_review_bots``. Bound to
    Phase 14's ``sigantry pr-bot run`` CLI: the orchestrator resolves a
    ``PrReviewBot`` from ``--provider {github,ado,auto}`` and calls
    ``ping()`` -> ``get_pr()`` -> ``get_changed_files()`` ->
    ``post_comment()``. The Markdown body passed to ``post_comment``
    comes from :func:`sigantry_core.pr_bot.payload.render_markdown` and
    is BYTE-IDENTICAL across providers (STARTER-07 invariant).

    The Protocol is :func:`typing.runtime_checkable` so ``isinstance(p,
    PrReviewBot)`` works -- exercised by the contract tests in
    ``tests/sigantry_core/pr_bot/providers/`` and the cross-provider
    parity test in ``tests/sigantry_core/pr_bot/test_provider_post_body_parity.py``.

    Audit-2026-05-07 W2.5 historical note: the same shape lived as
    ``sigantry_core.pr_bot.providers.base.Provider`` since Phase 14;
    the W2.5 lift moves the canonical definition here (matching the
    registry group name + the convention of every other seam) and
    leaves a re-export alias at the old path so existing callers keep
    working.
    """

    name: str

    def ping(self) -> None: ...
    def get_pr(self, pr_id: str) -> PullRequest: ...
    def get_changed_files(self, pr_id: str) -> list[ChangedFile]: ...
    def post_comment(self, pr_id: str, body: str) -> str: ...


@runtime_checkable
class ApprovalGate(Protocol):
    """Approval-gate seam (ADO env / GitHub env / OPA).

    ``timeout_s`` is the CLIENT-side poll-loop timeout. ADO and GitHub also
    apply server-side approval timeouts that the client does NOT control;
    when the server times out the gate reports ``"timeout"`` (16-RESEARCH.md
    §Pitfall 4). Reference poll interval is 30s (RESEARCH §Pitfall 4).
    """

    name: str

    def request(self, ctx: ApprovalContext) -> ApprovalRequest: ...

    def wait(self, request: ApprovalRequest, timeout_s: int = 3600) -> ApprovalOutcome: ...

    def ping(self) -> None: ...


__all__ = [
    "ApprovalContext",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalOutcome",
    "ApprovalRequest",
    "AuthProvider",
    "CapacityAction",
    "CapacityApplyResult",
    "CapacityContext",
    "CapacityPolicy",
    "ChangedFile",
    "Closeable",
    "DataQualityGate",
    "DataRef",
    "DeployContext",
    "DeployPlan",
    "DeployProfile",
    "DeployResult",
    "GateResult",
    "NotificationEvent",
    "NotificationLevel",
    "NotificationSink",
    "PrReviewBot",
    "PullRequest",
    "RunbookRegistry",
    "Secret",
    "SecretStore",
    "TelemetryEvent",
    "TelemetrySink",
    "WorkItem",
    "WorkItemProvider",
]
