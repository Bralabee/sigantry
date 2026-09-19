"""GithubEnvironmentsApprovalGate tests (Plan 16-03).

Polls GitHub Environments protection-rule decision via REST; treats
run.completed + step.run as approved, run.completed + step.skipped as
rejected.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import httpx
import respx

from sigantry_core.approval_gates.github_environments import (
    GithubEnvironmentsApprovalGate,
)
from sigantry_core.protocols import ApprovalContext, ApprovalGate


def _make_gate(*, run_id: int = 12345) -> GithubEnvironmentsApprovalGate:
    return GithubEnvironmentsApprovalGate(
        owner="example-org",
        repo="sigantry",
        run_id=run_id,
        environment="prod",
        pat="fake-pat-dummy",
    )


def _make_request(gate: GithubEnvironmentsApprovalGate):
    ctx = ApprovalContext(
        release_id="rel-2026-04-28",
        env="prod",
        approvers=["alice@example.com"],
    )
    return gate.request(ctx)


def test_satisfies_approval_gate_protocol() -> None:
    gate = _make_gate()
    assert isinstance(gate, ApprovalGate)
    assert gate.name == "github_environments"


def test_wait_observes_protection_rule_decision(monkeypatch) -> None:
    """status='completed' + conclusion='success' -> approved."""
    monkeypatch.setattr(
        "sigantry_core.approval_gates.github_environments.time.sleep", lambda s: None
    )
    with respx.mock(base_url="https://api.github.com") as router:
        get_route = router.get("/repos/example-org/sigantry/actions/runs/12345").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 12345,
                    "status": "completed",
                    "conclusion": "success",
                    "triggering_actor": {"login": "alice"},
                },
            )
        )
        gate = _make_gate()
        outcome = gate.wait(_make_request(gate), timeout_s=3600)

    assert outcome == "approved"
    assert get_route.call_count == 1


def test_wait_returns_approved_when_run_completed_and_step_ran(monkeypatch) -> None:
    """conclusion='success' -> approved (the protection rule allowed the run)."""
    monkeypatch.setattr(
        "sigantry_core.approval_gates.github_environments.time.sleep", lambda s: None
    )
    with respx.mock(base_url="https://api.github.com") as router:
        router.get("/repos/example-org/sigantry/actions/runs/12345").mock(
            return_value=httpx.Response(
                200,
                json={"id": 12345, "status": "completed", "conclusion": "success"},
            )
        )
        gate = _make_gate()
        assert gate.wait(_make_request(gate)) == "approved"


def test_wait_returns_rejected_when_run_completed_and_step_skipped(monkeypatch) -> None:
    """conclusion='failure' / 'cancelled' / 'timed_out' -> rejected.

    The GitHub Actions deployment_protection_rule path treats anything
    other than 'success' as a blocker -- the deploy did not proceed.
    """
    monkeypatch.setattr(
        "sigantry_core.approval_gates.github_environments.time.sleep", lambda s: None
    )
    for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
        with respx.mock(base_url="https://api.github.com") as router:
            router.get("/repos/example-org/sigantry/actions/runs/12345").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": 12345,
                        "status": "completed",
                        "conclusion": conclusion,
                    },
                )
            )
            gate = _make_gate()
            outcome = gate.wait(_make_request(gate))
            assert outcome == "rejected", (
                f"conclusion={conclusion!r} must map to rejected; got {outcome}"
            )


def test_wait_emits_approval_record(monkeypatch, tmp_path: Path) -> None:
    """Every terminal outcome emits an ApprovalRecord; conclusion in last_observed_status."""
    monkeypatch.setattr(
        "sigantry_core.approval_gates.github_environments.time.sleep", lambda s: None
    )

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_path
    try:
        with respx.mock(base_url="https://api.github.com") as router:
            router.get("/repos/example-org/sigantry/actions/runs/12345").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": 12345,
                        "status": "completed",
                        "conclusion": "success",
                        "triggering_actor": {"login": "alice"},
                    },
                )
            )
            gate = _make_gate()
            outcome = gate.wait(_make_request(gate))
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    assert outcome == "approved"
    record = json.loads((tmp_path / "approvals.jsonl").read_text("utf-8").splitlines()[0])
    assert record["outcome"] == "approved"
    assert record["request_id"] == "12345"
    # last_observed_status carries the GH conclusion (more informative than 'completed').
    assert record["last_observed_status"] == "success"
    assert record["decided_by"] == "alice"
    assert record["audit_hash"]
