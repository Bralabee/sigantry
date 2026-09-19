"""GithubEnvironmentsApprovalGate -- GitHub Environments protection-rule
observer (Plan 16-03).

Composes :class:`sigantry_core.client.base.BaseRestClient` for HTTP
traffic; mirrors :class:`sigantry_core.workitems.github._NoopTokenProvider`
to bypass the OAuth-scope chain since GitHub's ``Authorization: Bearer
<pat>`` header is supplied per-request via ``extra_headers``.

Observer pattern
----------------
The GitHub Actions deployment_protection_rule IS the request -- a workflow
run pauses on the rule and emits a status update; sigantry's gate is the
**observer** of that decision. ``request()`` wraps the run id (which
identifies the protection-rule instance) as the ApprovalRequest's
``request_id``. ``wait()`` polls
``GET /repos/{owner}/{repo}/actions/runs/{run_id}`` every 30s until
``status == "completed"`` and then maps the ``conclusion`` field to an
ApprovalOutcome.

Conclusion mapping
------------------
- ``"success"`` -> ``"approved"`` (the protection rule allowed the run
  through)
- ``"failure"`` / ``"cancelled"`` / ``"timed_out"`` / ``"action_required"``
  / anything else -> ``"rejected"`` (the rule blocked the run, or the run
  failed for an unrelated reason -- treat as rejected because the deploy
  did not proceed)

Polling interval
----------------
30 seconds, mirroring :class:`AdoEnvironmentsApprovalGate`. GitHub does
not document a sync-poll default but 30s is well below their REST rate
limits and avoids polling churn.

Audit-plane integration
-----------------------
``wait()`` emits :class:`ApprovalRecord` synchronously on every terminal
outcome. ``decided_by`` is read from the payload's
``triggering_actor.login`` field (best effort -- may be absent on
forked-repo runs).
"""

from __future__ import annotations

import time
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

_GITHUB_BASE: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION_HEADER: Final[str] = "2022-11-28"
_POLL_INTERVAL_S: Final[int] = 30


class _PatTokenProvider:
    """Placeholder ``TokenProviderProtocol`` for PAT-based GitHub auth.

    Mirrors the carve-out used by
    :class:`sigantry_core.workitems.github._NoopTokenProvider` and
    :class:`sigantry_core.secrets.github_secrets._PatTokenProvider`:
    GitHub's auth model bypasses the OAuth-scope chain; the
    ``Authorization`` header is supplied per-request via
    ``extra_headers``. This shim satisfies BaseRestClient's contract
    without wiring DefaultAzureCredential.
    """

    tenant_id: str | None = None

    def __init__(self, pat: str) -> None:
        self._pat = pat

    def get_token(self, scope: str) -> str:
        del scope
        return ""

    def last_credential_class(self, scope: str) -> str | None:
        del scope
        return "GitHub-PAT"


# Import-time conformance check (mirror of workitems/github.py review-fix MD-02).
assert isinstance(_PatTokenProvider("dummy"), TokenProviderProtocol), (
    "_PatTokenProvider drifted from TokenProviderProtocol; "
    "BaseRestClient may now call methods that this shim does not implement."
)


class GithubEnvironmentsApprovalGate:
    """GitHub Environments protection-rule observer (ApprovalGate impl).

    Constructor: ``(*, owner: str, repo: str, run_id: int, environment: str,
    pat: str, http_client=None)`` where ``run_id`` is the workflow run that
    paused on the protection rule and ``environment`` is the target env
    name (recorded for audit context but not used in the URL itself --
    GitHub keys runs by id).

    Method semantics:

    - ``request(ctx)``: wraps ``str(self._run_id)`` as the request_id.
      No I/O.
    - ``wait(request, timeout_s=3600)``: polls
      ``GET /repos/{owner}/{repo}/actions/runs/{run_id}`` every 30s until
      ``status == "completed"`` OR client-side deadline elapses.
      ``conclusion`` field then drives the ApprovalOutcome mapping.
    - ``ping()``: ``GET /repos/{owner}/{repo}`` -- lightweight reachability
      check.
    """

    name: str = "github_environments"

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        run_id: int,
        environment: str,
        pat: str,
        http_client: Any | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._run_id = run_id
        self._environment = environment
        self._pat = pat
        self._client = BaseRestClient(
            token_provider=_PatTokenProvider(pat),
            base_url=_GITHUB_BASE,
            default_scope="",
            http_client=http_client,
        )

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._pat}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION_HEADER,
        }

    # ---- ApprovalGate Protocol surface --------------------------------

    def request(self, ctx: ApprovalContext) -> ApprovalRequest:
        """Wrap the bound ``run_id`` as the ApprovalRequest's request_id.

        The ``ctx`` carries the release/env/approvers; the run id is the
        observable identity for the protection-rule instance.
        """
        return ApprovalRequest(
            request_id=str(self._run_id),
            release_id=ctx.release_id,
            env=ctx.env or self._environment,
            approvers=list(ctx.approvers),
            requested_at=datetime.now(tz=UTC),
        )

    def wait(self, request: ApprovalRequest, timeout_s: int = 3600) -> ApprovalOutcome:
        """Poll the run status until completed or client-side timeout."""
        deadline = time.monotonic() + timeout_s
        last_status = "pending"

        while time.monotonic() < deadline:
            response = self._client.send(
                "GET",
                f"/repos/{self._owner}/{self._repo}/actions/runs/{self._run_id}",
                extra_headers=self._auth_headers(),
            )
            payload = response.json_body
            if isinstance(payload, dict):
                status_value = payload.get("status", "pending")
                if isinstance(status_value, str):
                    last_status = status_value

                if last_status == "completed":
                    conclusion = payload.get("conclusion")
                    outcome: ApprovalOutcome = "approved" if conclusion == "success" else "rejected"
                    decided_by = self._extract_decider(payload)
                    # last_observed_status records the conclusion (more
                    # informative than just "completed") so operators see
                    # the actual rejection cause.
                    observed = conclusion if isinstance(conclusion, str) else last_status
                    emit_approval_record(
                        ApprovalRecord(
                            request_id=request.request_id,
                            release_id=request.release_id,
                            env=request.env,
                            approvers=list(request.approvers),
                            outcome=outcome,
                            decided_by=decided_by,
                            decided_at=datetime.now(tz=UTC),
                            last_observed_status=observed,
                        ).with_hash()
                    )
                    return outcome

            time.sleep(_POLL_INTERVAL_S)

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
        """Lightweight reachability + auth check via the repo endpoint."""
        self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}",
            extra_headers=self._auth_headers(),
        )

    # ---- internal -----------------------------------------------------

    @staticmethod
    def _extract_decider(payload: dict[str, Any]) -> str | None:
        """Best-effort decider extraction from a workflow-run payload.

        GitHub's run payload nests the actor as
        ``{"triggering_actor": {"login": "alice", ...}}``. Returns
        ``None`` if absent or shaped unexpectedly.
        """
        actor = payload.get("triggering_actor")
        if isinstance(actor, dict):
            login = actor.get("login")
            if isinstance(login, str) and login:
                return login
        return None


__all__ = ["GithubEnvironmentsApprovalGate"]
