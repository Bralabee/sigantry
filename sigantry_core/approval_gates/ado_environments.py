"""AdoEnvironmentsApprovalGate -- ADO Environments approval observer (Plan 16-03).

Composes :class:`sigantry_core.client.base.BaseRestClient` for HTTP traffic
(no new ``import httpx`` exception is added -- the existing
``sigantry_core/client/**`` carve-out covers the inherited transport).

Observer pattern (RESEARCH §Common Operation 2)
------------------------------------------------
The YAML deploy pipeline IS the request -- ADO emits the approval id when
the YAML hits an Environments approval gate; sigantry's gate is the
**observer** of that approval. ``request()`` is therefore a no-op that
wraps ``ctx.approval_id`` (set by the YAML pipeline) into an
:class:`ApprovalRequest`. ``wait()`` polls the ADO REST endpoint
``/{org}/{project}/_apis/pipelines/approvals/{approvalId}?api-version=7.1``
every 30 seconds until a terminal status is observed or the client-side
``timeout_s`` deadline elapses.

Polling interval (RESEARCH §Pitfall 4 + Anti-Patterns)
------------------------------------------------------
The 30-second poll interval is ADO's documented sync-poll default --
faster polling triggers ADO rate-limits with no business value. The
client-side ``timeout_s`` (default 3600s = 1h) is INTENTIONALLY shorter
than ADO's per-environment server-side approval timeout (default 30
days). Operators set ``timeout_s`` based on their CI window, not ADO's
environment policy.

last_observed_status (RESEARCH §Pitfall 4)
------------------------------------------
The ApprovalRecord emitted on every terminal outcome carries the upstream
status at the LAST poll before the gate returned. This distinguishes the
two failure modes:

- **Client-side timeout**: ``outcome="timeout"`` +
  ``last_observed_status="pending"`` -- "we gave up waiting; ADO is still
  spinning".
- **Server-side rejection**: ``outcome="rejected"`` +
  ``last_observed_status="rejected"`` (or ``"canceled"`` / ``"timedOut"``)
  -- ADO terminally rejected the request.

Without ``last_observed_status`` operators reading ``approvals.jsonl``
cannot distinguish the two; with it they can triage with a single
``grep -E '"outcome":"timeout"' approvals.jsonl``.

Audit-plane integration
-----------------------
``wait()`` emits :class:`ApprovalRecord` via :func:`emit_approval_record`
synchronously on every terminal outcome (approved / rejected / timeout).
``request()`` does NOT emit (the request is the START of the audit-trace
not a recordable terminal event).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Final

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import AZURE_DEVOPS_SCOPE
from sigantry_core.client.base import BaseRestClient
from sigantry_core.governance.audit import emit_approval_record
from sigantry_core.governance.records import ApprovalRecord
from sigantry_core.protocols import (
    ApprovalContext,
    ApprovalOutcome,
    ApprovalRequest,
)

_API_VERSION: Final[str] = "7.1"
_POLL_INTERVAL_S: Final[int] = 30  # ADO documented sync-poll default
_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"approved", "rejected", "canceled", "timedOut"}
)


class AdoEnvironmentsApprovalGate:
    """Azure DevOps Environments approval observer (ApprovalGate impl).

    Constructor: ``(*, organization: str, project: str,
    token_provider: TokenProvider, http_client=None)`` where
    ``organization`` is the ADO org slug (e.g. ``"contoso-dataops"``)
    and ``project`` is the project name. The token-provider issues a
    bearer for ``AZURE_DEVOPS_SCOPE`` (Phase 11 precedent).

    Method semantics:

    - ``request(ctx)``: wraps ``ctx.approval_id`` (the YAML pipeline emits
      this id) into an :class:`ApprovalRequest`. No I/O.
    - ``wait(request, timeout_s=3600)``: polls
      ``GET /{org}/{project}/_apis/pipelines/approvals/{approvalId}?api-version=7.1``
      every 30s until ADO returns one of ``{approved, rejected, canceled,
      timedOut}`` OR ``time.monotonic()`` exceeds the client-side deadline.
      Outcome mapping: ADO ``approved`` -> ``"approved"``; any other
      terminal status -> ``"rejected"``; client-side deadline ->
      ``"timeout"``.
    - ``ping()``: ``GET /{org}/{project}/_apis/connectionData?api-version=7.1``
      -- lightweight reachability check that exercises the token chain.

    Every terminal ``wait()`` outcome emits an :class:`ApprovalRecord`
    carrying ``last_observed_status`` so operators distinguish a
    client-side give-up from an ADO rejection (RESEARCH §Pitfall 4).
    """

    name: str = "ado_environments"

    def __init__(
        self,
        *,
        organization: str,
        project: str,
        token_provider: TokenProvider,
        http_client: Any | None = None,
    ) -> None:
        self._organization = organization
        self._project = project
        self._client = BaseRestClient(
            token_provider=token_provider,
            base_url=f"https://dev.azure.com/{organization}",
            default_scope=AZURE_DEVOPS_SCOPE,
            http_client=http_client,
        )

    # ---- ApprovalGate Protocol surface --------------------------------

    def request(self, ctx: ApprovalContext) -> ApprovalRequest:
        """Wrap the ADO-supplied ``ctx.approval_id`` as an ApprovalRequest.

        No I/O -- the YAML pipeline is the actual request mechanism;
        sigantry's gate observes it.
        """
        return ApprovalRequest(
            request_id=ctx.approval_id,
            release_id=ctx.release_id,
            env=ctx.env,
            approvers=list(ctx.approvers),
            requested_at=datetime.now(tz=UTC),
        )

    def wait(self, request: ApprovalRequest, timeout_s: int = 3600) -> ApprovalOutcome:
        """Poll the ADO approval status until terminal or client-side timeout."""
        deadline = time.monotonic() + timeout_s
        last_status = "pending"

        while time.monotonic() < deadline:
            response = self._client.send(
                "GET",
                f"/{self._project}/_apis/pipelines/approvals/{request.request_id}",
                params={"api-version": _API_VERSION},
            )
            payload = response.json_body
            if isinstance(payload, dict):
                status_value = payload.get("status", "pending")
                if isinstance(status_value, str):
                    last_status = status_value

                if last_status in _TERMINAL_STATUSES:
                    outcome: ApprovalOutcome = (
                        "approved" if last_status == "approved" else "rejected"
                    )
                    decided_by = self._extract_decider(payload)
                    emit_approval_record(
                        ApprovalRecord(
                            request_id=request.request_id,
                            release_id=request.release_id,
                            env=request.env,
                            approvers=list(request.approvers),
                            outcome=outcome,
                            decided_by=decided_by,
                            decided_at=datetime.now(tz=UTC),
                            last_observed_status=last_status,
                        ).with_hash()
                    )
                    return outcome

            time.sleep(_POLL_INTERVAL_S)

        # Client-side timeout. last_observed_status carries the most-recent
        # observed state so operators can triage (RESEARCH §Pitfall 4).
        emit_approval_record(
            ApprovalRecord(
                request_id=request.request_id,
                release_id=request.release_id,
                env=request.env,
                approvers=list(request.approvers),
                outcome="timeout",
                decided_by=None,
                decided_at=None,
                last_observed_status=last_status,
            ).with_hash()
        )
        return "timeout"

    def ping(self) -> None:
        """Lightweight reachability + auth check.

        Hits ADO's connectionData endpoint which requires a valid token
        but does not enumerate any user-visible resources.
        """
        self._client.send(
            "GET",
            f"/{self._project}/_apis/connectionData",
            params={"api-version": _API_VERSION},
        )

    # ---- internal -----------------------------------------------------

    @staticmethod
    def _extract_decider(payload: dict[str, Any]) -> str | None:
        """Best-effort decider extraction from ADO approval payload.

        ADO's approval payload typically nests the approver as
        ``{"approver": {"displayName": "alice@x", ...}}``. Returns
        ``None`` if the field is absent or shaped unexpectedly.
        """
        approver = payload.get("approver")
        if isinstance(approver, dict):
            display = approver.get("displayName")
            if isinstance(display, str) and display:
                return display
        return None


__all__ = ["AdoEnvironmentsApprovalGate"]
