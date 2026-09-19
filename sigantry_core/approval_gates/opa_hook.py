"""OpaApprovalGate -- Open Policy Agent (OPA) HTTP API approval gate (Plan 16-03).

Composes :class:`sigantry_core.client.base.BaseRestClient` for HTTP traffic
(no new ``import httpx`` exception is added -- the existing
``sigantry_core/client/**`` carve-out covers the inherited transport).

The OSS-pure escape hatch (RESEARCH §Pitfall, opa-quickstart.md)
----------------------------------------------------------------
Adopters who do not run ADO or GitHub Actions need a non-vendor approval
gate. OPA is the OSS standard for policy decisions and ships an HTTP API
out of the box (``opa run --server``). This impl POSTs the release
context to ``/v1/data/{policy_path}`` (default
``sigantry/approval/allow``) and reads the ``result`` boolean. Operators
write Rego to express their approval policy -- see
``docs/runbooks/approval-gates/opa-quickstart.md`` for the default
template and production-hardening notes (mTLS, GitOps for policies).

Synchronous-decision contract (RESEARCH §Common Operation 3)
------------------------------------------------------------
OPA's policy evaluation is synchronous; ``wait()`` is a single POST
round-trip rather than a poll loop. ``timeout_s`` is therefore treated as
a safety bound on the POST itself (BaseRestClient's per-request timeout)
rather than as an outer poll-loop deadline.

Defensive fallback (RESEARCH §Assumption A2)
--------------------------------------------
If OPA returns ``200 OK`` without a ``result`` key (undefined decision),
``wait()`` reads ``False`` -- the safe default is rejection, not approval.
This is RESEARCH §Assumption A2: "OPA result key always present on 200
OK"; the fallback exists because Assumption A2 is documented as
LOW-confidence and the cost of the wrong default (approving on undefined)
is unacceptable.

Auth model
----------
OPA's HTTP API is conventionally credential-less in dev (``localhost:8181``)
and mTLS-fronted in prod. The constructor accepts an optional
``token_provider`` for production deployments where OPA is fronted by an
authenticating reverse proxy. The default is a no-op TokenProvider that
satisfies BaseRestClient's contract without minting an Azure token.

Audit-plane integration
-----------------------
``wait()`` emits :class:`ApprovalRecord` synchronously on every decision
with ``decided_by="opa-policy"`` (OPA does not surface the human
identity of who wrote/owns the policy; that linkage is operator-bound
and documented in ``opa-quickstart.md``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sigantry_core.auth import TokenProviderProtocol
from sigantry_core.client.base import BaseRestClient
from sigantry_core.governance.audit import emit_approval_record
from sigantry_core.governance.records import ApprovalRecord
from sigantry_core.protocols import (
    ApprovalContext,
    ApprovalOutcome,
    ApprovalRequest,
)

_DEFAULT_OPA_URL: Final[str] = "http://localhost:8181"
_DEFAULT_POLICY_PATH: Final[str] = "sigantry/approval/allow"


class _NoopTokenProvider:
    """Placeholder ``TokenProviderProtocol`` for credential-less OPA dev mode.

    Mirrors :class:`sigantry_core.workitems.github._NoopTokenProvider`:
    OPA's HTTP API is conventionally credential-less on ``localhost:8181``;
    production deployments front OPA with mTLS or a reverse proxy whose
    auth surface is operator-supplied (callers pass their own
    TokenProvider in that case). This shim satisfies BaseRestClient's
    contract without wiring DefaultAzureCredential.
    """

    tenant_id: str | None = None

    def get_token(self, scope: str) -> str:
        del scope
        return ""

    def last_credential_class(self, scope: str) -> str | None:
        del scope
        return "OPA-NoOp"


# Import-time conformance check (mirror of workitems/github.py review-fix MD-02).
assert isinstance(_NoopTokenProvider(), TokenProviderProtocol), (
    "_NoopTokenProvider drifted from TokenProviderProtocol; "
    "BaseRestClient may now call methods that this shim does not implement."
)


class OpaApprovalGate:
    """Open Policy Agent (OPA) HTTP API ApprovalGate impl.

    Constructor: ``(*, opa_url: str = "http://localhost:8181",
    policy_path: str = "sigantry/approval/allow",
    token_provider: TokenProviderProtocol | None = None,
    http_client=None)``. The default ``opa_url`` and ``policy_path`` match
    the runbook template; production deployments override both. The
    optional ``token_provider`` is for authenticating reverse proxies
    fronting OPA -- omit for credential-less local OPA daemons.

    Method semantics:

    - ``request(ctx)``: generates ``str(uuid.uuid4())`` as the request_id
      (OPA has no native request id). No I/O.
    - ``wait(request, timeout_s=3600)``: POSTs
      ``/v1/data/{policy_path}`` with body
      ``{"input": {"release_id": ..., "env": ..., "approvers": [...]}}``.
      Reads the ``result`` field; ``True`` -> ``"approved"``, ``False`` ->
      ``"rejected"``, missing key -> ``"rejected"`` (defensive per
      Assumption A2). The ``timeout_s`` parameter is accepted for
      Protocol parity but OPA decisions are synchronous so it is not used
      as a poll-loop deadline.
    - ``ping()``: ``GET /v1/policies`` -- OPA's diagnostic endpoint that
      lists currently loaded policies.
    """

    name: str = "opa"

    def __init__(
        self,
        *,
        opa_url: str = _DEFAULT_OPA_URL,
        policy_path: str = _DEFAULT_POLICY_PATH,
        token_provider: TokenProviderProtocol | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._opa_url = opa_url.rstrip("/")
        # Strip a leading slash so f-string composition does not double up.
        self._policy_path = policy_path.lstrip("/")
        provider: TokenProviderProtocol = token_provider or _NoopTokenProvider()
        self._client = BaseRestClient(
            token_provider=provider,
            base_url=self._opa_url,
            default_scope="opa",
            http_client=http_client,
        )

    # ---- ApprovalGate Protocol surface --------------------------------

    def request(self, ctx: ApprovalContext) -> ApprovalRequest:
        """Generate a uuid4 request_id (OPA has no native request id)."""
        return ApprovalRequest(
            request_id=str(uuid.uuid4()),
            release_id=ctx.release_id,
            env=ctx.env,
            approvers=list(ctx.approvers),
            requested_at=datetime.now(tz=UTC),
        )

    def wait(self, request: ApprovalRequest, timeout_s: int = 3600) -> ApprovalOutcome:
        """POST the request to OPA and map ``result`` to an ApprovalOutcome.

        OPA decisions are synchronous; ``timeout_s`` is accepted for
        Protocol parity and forwarded to :meth:`BaseRestClient.send` as a
        per-request timeout. It bounds the POST round-trip rather than
        acting as an outer poll-loop deadline (there is no poll loop --
        OPA returns the decision in a single response).
        """
        # Forward ``timeout_s`` as a per-request timeout so the operator's
        # Protocol-level value bounds the actual POST. Without this the
        # underlying BaseRestClient default (30s) would silently override
        # whatever the caller passed -- which contradicts the module
        # docstring at lines 21-23.
        response = self._client.send(
            "POST",
            f"/v1/data/{self._policy_path}",
            json={
                "input": {
                    "release_id": request.release_id,
                    "env": request.env,
                    "approvers": list(request.approvers),
                }
            },
            timeout=float(timeout_s),
        )
        payload = response.json_body
        # Defensive fallback (RESEARCH §Assumption A2): missing result -> rejected.
        decision: bool = False
        if isinstance(payload, dict):
            raw = payload.get("result", False)
            if isinstance(raw, bool):
                decision = raw
            # Non-bool result (e.g. dict): treat as rejected. A future
            # Plan-16-X refinement could parse {"allow": bool} shapes;
            # current default Rego template returns a bare bool.
        outcome: ApprovalOutcome = "approved" if decision else "rejected"
        emit_approval_record(
            ApprovalRecord(
                request_id=request.request_id,
                release_id=request.release_id,
                env=request.env,
                approvers=list(request.approvers),
                outcome=outcome,
                decided_by="opa-policy",
                decided_at=datetime.now(tz=UTC),
                last_observed_status=outcome,
            ).with_hash()
        )
        return outcome

    def ping(self) -> None:
        """OPA diagnostic endpoint -- lists currently loaded policies."""
        self._client.send("GET", "/v1/policies")


__all__ = ["OpaApprovalGate"]
