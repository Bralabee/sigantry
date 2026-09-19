"""AdoEnvironmentsApprovalGate tests (Plan 16-03).

Poll loop observes ADO REST approval state; client-side timeout_s vs
server-side approval timeout per RESEARCH §Pitfall 4. Reference poll
interval 30s.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import respx

from sigantry_core.approval_gates.ado_environments import AdoEnvironmentsApprovalGate
from sigantry_core.auth import TokenProvider
from sigantry_core.protocols import ApprovalContext, ApprovalGate


def _make_token_provider() -> TokenProvider:
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "test-bearer-token"
    mp.tenant_id = "test-tenant-id"
    mp.last_credential_class.return_value = "MockCredential"
    return mp


def _make_gate() -> AdoEnvironmentsApprovalGate:
    return AdoEnvironmentsApprovalGate(
        organization="myorg",
        project="myproject",
        token_provider=_make_token_provider(),
    )


def _make_request(gate: AdoEnvironmentsApprovalGate, *, approval_id: str = "approval-1"):
    ctx = ApprovalContext(
        release_id="rel-2026-04-28",
        env="prod",
        approvers=["alice@example.com", "bob@example.com"],
        approval_id=approval_id,
    )
    return gate.request(ctx)


def test_satisfies_approval_gate_protocol() -> None:
    """AdoEnvironmentsApprovalGate satisfies the runtime_checkable Protocol."""
    gate = _make_gate()
    assert isinstance(gate, ApprovalGate)
    assert gate.name == "ado_environments"


def test_wait_terminal_approved(monkeypatch) -> None:
    """ADO approved -> outcome 'approved'; no extra polls after terminal status."""
    sleeps: list[int] = []
    monkeypatch.setattr(
        "sigantry_core.approval_gates.ado_environments.time.sleep",
        lambda s: sleeps.append(s),
    )
    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        get_route = router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "approval-1",
                    "status": "approved",
                    "approver": {"displayName": "alice@example.com"},
                },
            )
        )
        gate = _make_gate()
        request = _make_request(gate)
        outcome = gate.wait(request, timeout_s=3600)

    assert outcome == "approved"
    assert get_route.call_count == 1, "terminal status should stop the poll loop"
    # No sleeps issued because the first poll already saw a terminal status.
    assert sleeps == [], "no sleep between terminal status detection and return"


def test_wait_terminal_rejected(monkeypatch) -> None:
    """ADO rejected -> outcome 'rejected'."""
    monkeypatch.setattr("sigantry_core.approval_gates.ado_environments.time.sleep", lambda s: None)
    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "approval-1", "status": "rejected"},
            )
        )
        gate = _make_gate()
        outcome = gate.wait(_make_request(gate), timeout_s=3600)

    assert outcome == "rejected"


def test_wait_terminal_canceled_returns_rejected(monkeypatch) -> None:
    """ADO canceled / timedOut both map to 'rejected'."""
    monkeypatch.setattr("sigantry_core.approval_gates.ado_environments.time.sleep", lambda s: None)
    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "approval-1", "status": "canceled"},
            )
        )
        gate = _make_gate()
        outcome = gate.wait(_make_request(gate), timeout_s=3600)

    assert outcome == "rejected"


def test_wait_client_side_timeout_returns_timeout(monkeypatch) -> None:
    """Client-side timeout fires when ADO never returns terminal status.

    The test injects a monotonic-clock fake that advances by 31s on every
    call, so the deadline of `start + 60s` elapses after 2 polls. Sleep
    is a no-op so wall time is irrelevant.
    """
    fake_now = [0.0]

    def fake_monotonic() -> float:
        # Each call advances by 31s; deadline=60s elapses after 2 calls.
        fake_now[0] += 31.0
        return fake_now[0]

    monkeypatch.setattr(
        "sigantry_core.approval_gates.ado_environments.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr("sigantry_core.approval_gates.ado_environments.time.sleep", lambda s: None)

    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "approval-1", "status": "pending"},
            )
        )
        gate = _make_gate()
        outcome = gate.wait(_make_request(gate), timeout_s=60)

    assert outcome == "timeout"


def test_wait_emits_approval_record(monkeypatch, tmp_path: Path) -> None:
    """Every terminal outcome emits an ApprovalRecord with last_observed_status."""
    monkeypatch.setattr("sigantry_core.approval_gates.ado_environments.time.sleep", lambda s: None)

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_path
    try:
        with respx.mock(base_url="https://dev.azure.com/myorg") as router:
            router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": "approval-1",
                        "status": "approved",
                        "approver": {"displayName": "alice@example.com"},
                    },
                )
            )
            gate = _make_gate()
            outcome = gate.wait(_make_request(gate))
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    assert outcome == "approved"
    record = json.loads((tmp_path / "approvals.jsonl").read_text("utf-8").splitlines()[0])
    assert record["request_id"] == "approval-1"
    assert record["release_id"] == "rel-2026-04-28"
    assert record["env"] == "prod"
    assert record["outcome"] == "approved"
    # Pitfall 4 pin: last_observed_status carries the upstream state.
    assert record["last_observed_status"] == "approved"
    assert record["decided_by"] == "alice@example.com"
    assert record["audit_hash"]


def test_wait_timeout_record_carries_pending_status(monkeypatch, tmp_path: Path) -> None:
    """RESEARCH §Pitfall 4: client-side timeout records last_observed_status='pending'.

    This is the diagnostic that distinguishes 'we gave up waiting' from
    'ADO rejected'. Without the field operators reading the audit log
    cannot tell the two failure modes apart.
    """
    fake_now = [0.0]

    def fake_monotonic() -> float:
        fake_now[0] += 31.0
        return fake_now[0]

    monkeypatch.setattr(
        "sigantry_core.approval_gates.ado_environments.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr("sigantry_core.approval_gates.ado_environments.time.sleep", lambda s: None)

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_path
    try:
        with respx.mock(base_url="https://dev.azure.com/myorg") as router:
            router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
                return_value=httpx.Response(
                    200,
                    json={"id": "approval-1", "status": "pending"},
                )
            )
            gate = _make_gate()
            outcome = gate.wait(_make_request(gate), timeout_s=60)
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    assert outcome == "timeout"
    record = json.loads((tmp_path / "approvals.jsonl").read_text("utf-8").splitlines()[0])
    assert record["outcome"] == "timeout"
    assert record["last_observed_status"] == "pending", (
        "RESEARCH §Pitfall 4: client-side timeout MUST record the upstream "
        "status so operators distinguish 'gave up waiting' from 'ADO rejected'"
    )
    assert record["decided_by"] is None
    assert record["decided_at"] is None


def test_wait_polls_at_30s_interval(monkeypatch) -> None:
    """RESEARCH §Pitfall 4 + Anti-Patterns: poll interval is exactly 30s.

    Faster polling triggers ADO rate-limits with no business value;
    slower polling makes CI gates feel slow. 30s is the documented
    sync-poll default.
    """
    sleeps: list[int] = []
    monkeypatch.setattr(
        "sigantry_core.approval_gates.ado_environments.time.sleep",
        lambda s: sleeps.append(s),
    )

    # Drive the loop through 3 'pending' polls then 'approved' on the 4th.
    response_sequence = [
        httpx.Response(200, json={"id": "approval-1", "status": "pending"}),
        httpx.Response(200, json={"id": "approval-1", "status": "pending"}),
        httpx.Response(200, json={"id": "approval-1", "status": "pending"}),
        httpx.Response(200, json={"id": "approval-1", "status": "approved"}),
    ]

    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/pipelines/approvals/approval-1").mock(
            side_effect=response_sequence
        )
        gate = _make_gate()
        outcome = gate.wait(_make_request(gate), timeout_s=3600)

    assert outcome == "approved"
    # 3 'pending' polls -> 3 sleeps before terminal status on the 4th poll.
    assert len(sleeps) == 3, f"expected 3 sleeps before terminal poll; got {len(sleeps)}"
    # Every sleep is 30s exactly.
    assert all(s == 30 for s in sleeps), (
        f"every sleep must be exactly 30s (RESEARCH §Pitfall 4); got {sleeps}"
    )
