"""Phase 16 ApprovalGate reference implementations (Plan 16-03).

Three first-class reference impls of the ApprovalGate Protocol seam:

- :class:`AdoEnvironmentsApprovalGate` -- observer of ADO Environments
  approvals; polls ``/pipelines/approvals/{id}`` every 30s
  (RESEARCH §Pitfall 4 + Common Operation 2).
- :class:`GithubEnvironmentsApprovalGate` -- observer of GitHub
  Environments protection-rule decisions; polls
  ``/repos/.../actions/runs/{run_id}`` every 30s.
- :class:`OpaApprovalGate` -- OSS-pure synchronous decision via OPA HTTP
  API ``/v1/data/{policy_path}`` (RESEARCH §Common Operation 3).

All three compose :class:`sigantry_core.client.base.BaseRestClient`
(no new ``import httpx`` exception). All three emit
:class:`sigantry_core.governance.records.ApprovalRecord` via
:func:`sigantry_core.governance.audit.emit_approval_record` on every
terminal outcome (approved/rejected/timeout); ``last_observed_status``
distinguishes client-side timeout from server-side rejection per
RESEARCH §Pitfall 4.
"""

from sigantry_core.approval_gates.ado_environments import AdoEnvironmentsApprovalGate
from sigantry_core.approval_gates.github_environments import (
    GithubEnvironmentsApprovalGate,
)
from sigantry_core.approval_gates.opa_hook import OpaApprovalGate

__all__ = [
    "AdoEnvironmentsApprovalGate",
    "GithubEnvironmentsApprovalGate",
    "OpaApprovalGate",
]
