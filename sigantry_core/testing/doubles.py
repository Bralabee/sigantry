"""In-memory protocol doubles (PROD-04).

Each double satisfies exactly one of the ten seams declared in
``sigantry_core.protocols`` and is usable as a drop-in in
``FabricDataOps(..., telemetry=InMemoryTelemetrySink(), ...)``. The doubles
are:

- :class:`InMemoryTelemetrySink` -- appends events to ``.events`` list.
- :class:`NoopGate` -- always returns ``success=True`` ``GateResult``.
- :class:`FakeAuth` -- returns a static ``Secret`` from ``get_token``.
- :class:`StaticRunbookRegistry` -- ``dict.get`` on an injected mapping.
- :class:`NoopCapacityPolicy` -- plan returns ``[]``; apply is a no-op.
- :class:`FakeDeployProfile` -- callable-wrapping deploy profile for
  integration-test authors.
- :class:`FakeWorkItemProvider` -- records ``ping`` / ``fetch_work_items``
  / ``link_release`` invocations into public fields (Phase 11, the
  seventh seam introduced in v3.0).
- :class:`FakeNotificationSink` -- records every ``send`` call into
  ``.calls`` (Phase 16, eighth seam).
- :class:`FakeSecretStore` -- in-memory secret store backed by a
  ``dict[str, str]`` (Phase 16, ninth seam).
- :class:`FakeApprovalGate` -- scripted-outcome approval gate; the
  ``scripted_outcome`` constructor kwarg lets tests select the result
  ``wait()`` returns (Phase 16, tenth seam).

All classes carry ``name: str`` so they round-trip through
``Registry.register(group, name, impl)``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sigantry_core.protocols import (
    ApprovalContext,
    ApprovalOutcome,
    ApprovalRequest,
    CapacityAction,
    CapacityApplyResult,
    CapacityContext,
    DataRef,
    DeployContext,
    DeployPlan,
    DeployResult,
    GateResult,
    NotificationEvent,
    Secret,
    TelemetryEvent,
)

if TYPE_CHECKING:
    # Forward references only -- WorkItem lives in protocols.py and
    # DeployRecord lives in sigantry_core.release.record. The
    # FakeWorkItemProvider Protocol method signatures use string
    # forward references so this module stays import-cycle-free.
    from sigantry_core.protocols import WorkItem
    from sigantry_core.release.record import DeployRecord


class InMemoryTelemetrySink:
    """Telemetry sink that records events in a list (for test assertions)."""

    name: str = "in_memory"

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []
        self.flushed: int = 0

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    def flush(self, timeout_s: float = 5.0) -> None:
        self.flushed += 1


class NoopGate:
    """DQ gate that always reports success (no violations, single row evaluated)."""

    name: str = "noop"

    def run(self, suite: str, data_ref: DataRef) -> GateResult:
        return GateResult(
            suite=suite,
            success=True,
            violations=0,
            evaluated=1,
            run_id="noop",
        )


class FakeAuth:
    """Auth provider that returns a static secret on every ``get_token`` call."""

    name: str = "fake"

    def __init__(self, token: str = "fake-token") -> None:
        self._token = token

    def get_token(self, scope: str, *, tenant_id: str | None = None) -> Secret:
        return Secret(value=self._token)


class StaticRunbookRegistry:
    """Runbook registry backed by an in-memory ``dict[str, str]`` mapping."""

    name: str = "static"

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping: dict[str, str] = dict(mapping or {})

    def resolve(self, alert_name: str) -> str | None:
        return self._mapping.get(alert_name)


class NoopCapacityPolicy:
    """Capacity policy that never plans anything (``[]``) and never errors."""

    name: str = "noop"

    def plan(self, ctx: CapacityContext) -> list[CapacityAction]:
        return []

    def apply(self, ctx: CapacityContext, actions: list[CapacityAction]) -> CapacityApplyResult:
        return CapacityApplyResult(applied=0, skipped=len(actions))


class FakeDeployProfile:
    """Deploy profile that wraps an injected callable for plan outputs.

    The ``plan_fn`` callable receives the ``DeployContext`` and returns a
    ``DeployPlan`` (defaults to an empty plan). ``apply`` always reports
    ``items_published = len(plan.actions)`` with no failures -- handy for
    integration-test authors staging deterministic results.
    """

    name: str = "fake"

    def __init__(
        self,
        plan_fn: Callable[[DeployContext], DeployPlan] | None = None,
    ) -> None:
        self._plan_fn: Callable[[DeployContext], DeployPlan] = (
            plan_fn if plan_fn is not None else (lambda _ctx: DeployPlan(actions=[]))
        )
        self.apply_calls: list[dict[str, Any]] = []

    def plan(self, ctx: DeployContext) -> DeployPlan:
        return self._plan_fn(ctx)

    def apply(self, ctx: DeployContext, plan: DeployPlan) -> DeployResult:
        self.apply_calls.append({"ctx": ctx, "plan": plan})
        return DeployResult(
            workspace_id=ctx.workspace_id,
            items_published=len(plan.actions),
            items_failed=0,
        )


class FakeWorkItemProvider:
    """Testing double for WorkItemProvider (Phase 11, seventh seam).

    Records every Protocol method invocation into public fields so
    contract + integration tests can assert on what was called and with
    what arguments. Unlike the other six doubles which are stateless
    operationally, the WorkItemProvider doubles' value lies precisely
    in the recorded history -- the contract suite treats the linked
    list as the proof-of-call surface.
    """

    name: str = "fake"

    def __init__(
        self,
        *,
        fetch_returns: list[WorkItem] | None = None,
    ) -> None:
        self.pinged: int = 0
        self.fetch_calls: list[list[str]] = []
        self.fetch_returns: list[WorkItem] = (
            list(fetch_returns) if fetch_returns is not None else []
        )
        self.linked: list[tuple[str, list[str], DeployRecord]] = []

    def ping(self) -> None:
        self.pinged += 1

    def fetch_work_items(self, ids: list[str]) -> list[WorkItem]:
        self.fetch_calls.append(list(ids))
        return list(self.fetch_returns)

    def link_release(
        self,
        release_id: str,
        work_items: list[str],
        deploy_record: DeployRecord,
    ) -> None:
        self.linked.append((release_id, list(work_items), deploy_record))


class FakeNotificationSink:
    """Testing double for NotificationSink (Phase 16, eighth seam).

    Records every ``send`` invocation into ``.calls`` so contract tests
    can assert on ``(event, channel)`` pairs without exercising any real
    webhook. Mirrors :class:`FakeWorkItemProvider`'s record-history
    pattern.
    """

    name: str = "fake_notification"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.pinged: int = 0

    def send(self, event: NotificationEvent, *, channel: str | None = None) -> None:
        self.calls.append({"event": event, "channel": channel})

    def ping(self) -> None:
        self.pinged += 1


class FakeSecretStore:
    """Testing double for SecretStore (Phase 16, ninth seam).

    Backed by an in-memory ``dict[str, str]``. ``get`` returns ``None``
    when the key is absent (matching KeyVault read semantics). ``list_keys``
    filters by prefix. The double does NOT raise
    ``SecretReadNotSupported`` -- that behaviour is specific to the
    ``GithubSecretsSecretStore`` impl (Plan 16-02; 16-RESEARCH.md
    §Pitfall 5) and is exercised by a dedicated GitHub-only test.
    """

    name: str = "fake_secrets"

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.pinged: int = 0

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._store if k.startswith(prefix)]

    def ping(self) -> None:
        self.pinged += 1


class FakeApprovalGate:
    """Testing double for ApprovalGate (Phase 16, tenth seam).

    ``scripted_outcome`` selects what ``wait`` returns -- "approved" /
    "rejected" / "timeout". ``request`` returns a deterministic
    ``ApprovalRequest`` with ``request_id="fake-req-1"`` and
    ``requested_at=datetime.now(tz=UTC)``.
    """

    name: str = "fake_approval"

    def __init__(self, *, scripted_outcome: ApprovalOutcome = "approved") -> None:
        self._outcome: ApprovalOutcome = scripted_outcome
        self.requests: list[ApprovalContext] = []
        self.waits: list[ApprovalRequest] = []
        self.pinged: int = 0

    def request(self, ctx: ApprovalContext) -> ApprovalRequest:
        self.requests.append(ctx)
        return ApprovalRequest(
            request_id="fake-req-1",
            release_id=ctx.release_id,
            env=ctx.env,
            approvers=list(ctx.approvers),
            requested_at=datetime.now(tz=UTC),
        )

    def wait(self, request: ApprovalRequest, timeout_s: int = 3600) -> ApprovalOutcome:
        self.waits.append(request)
        return self._outcome

    def ping(self) -> None:
        self.pinged += 1


__all__ = [
    "FakeApprovalGate",
    "FakeAuth",
    "FakeDeployProfile",
    "FakeNotificationSink",
    "FakeSecretStore",
    "FakeWorkItemProvider",
    "InMemoryTelemetrySink",
    "NoopCapacityPolicy",
    "NoopGate",
    "StaticRunbookRegistry",
]
