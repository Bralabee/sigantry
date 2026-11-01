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


_APPROVALS = "/repos/example-org/sigantry/actions/runs/12345/approvals"


def _review(login: str, *, state: str = "approved", env: str = "prod") -> dict:
    return {"state": state, "environments": [{"name": env}], "user": {"login": login}}


def _mock_approvals(router, reviews: list | None = None, *, status: int = 200):
    return router.get(_APPROVALS).mock(return_value=httpx.Response(status, json=reviews or []))


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
        _mock_approvals(router, [_review("bob")])
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
        _mock_approvals(router, [_review("bob")])
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
            _mock_approvals(router)
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
            _mock_approvals(router, [_review("bob")])
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
    # The reviewer GitHub recorded, never the run's triggering actor.
    assert record["decided_by"] == "bob"
    assert record["audit_hash"]


def _decided_by(
    monkeypatch, tmp_path: Path, *, conclusion: str, mock, run_attempt: int | None = None
) -> str | None:
    """Run one completed wait() and return the recorded ``decided_by``."""
    monkeypatch.setattr(
        "sigantry_core.approval_gates.github_environments.time.sleep", lambda s: None
    )
    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_path
    try:
        with respx.mock(
            base_url="https://api.github.com", assert_all_called=run_attempt in (None, 1)
        ) as router:
            router.get("/repos/example-org/sigantry/actions/runs/12345").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": 12345,
                        "status": "completed",
                        "conclusion": conclusion,
                        "triggering_actor": {"login": "alice"},
                        **({} if run_attempt is None else {"run_attempt": run_attempt}),
                    },
                )
            )
            mock(router)
            gate = _make_gate()
            gate.wait(_make_request(gate))
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original
    lines = (tmp_path / "approvals.jsonl").read_text("utf-8").splitlines()
    return json.loads(lines[-1])["decided_by"]


def test_decided_by_is_the_last_reviewer_of_the_environment(monkeypatch, tmp_path: Path) -> None:
    reviews = [_review("carol", state="rejected"), _review("bob"), _review("dave", env="staging")]
    got = _decided_by(
        monkeypatch, tmp_path, conclusion="success", mock=lambda r: _mock_approvals(r, reviews)
    )
    assert got == "bob"


def test_decided_by_on_rejection_is_the_rejecting_reviewer(monkeypatch, tmp_path: Path) -> None:
    reviews = [_review("bob"), _review("carol", state="rejected")]
    got = _decided_by(
        monkeypatch, tmp_path, conclusion="failure", mock=lambda r: _mock_approvals(r, reviews)
    )
    assert got == "carol"


def test_decided_by_ignores_reviews_of_another_environment(monkeypatch, tmp_path: Path) -> None:
    reviews = [_review("dave", env="staging")]
    got = _decided_by(
        monkeypatch, tmp_path, conclusion="success", mock=lambda r: _mock_approvals(r, reviews)
    )
    assert got is None


def test_decided_by_is_none_when_history_unreadable(monkeypatch, tmp_path: Path) -> None:
    got = _decided_by(
        monkeypatch,
        tmp_path,
        conclusion="success",
        mock=lambda r: _mock_approvals(r, {"message": "Not Found"}, status=404),
    )
    assert got is None


def test_decided_by_is_none_and_record_written_on_network_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """Retry exhaustion re-raises the raw httpx error; the record must still land."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
    got = _decided_by(
        monkeypatch,
        tmp_path,
        conclusion="success",
        mock=lambda r: r.get(_APPROVALS).mock(side_effect=httpx.ConnectError("down")),
    )
    assert got is None


def test_decided_by_after_approval_then_failure_is_the_approver(
    monkeypatch, tmp_path: Path
) -> None:
    """One attempt: bob approved, then a deploy step failed.

    The run maps to ``rejected``, but bob decided the gate.
    """
    got = _decided_by(
        monkeypatch,
        tmp_path,
        conclusion="failure",
        run_attempt=1,
        mock=lambda r: _mock_approvals(r, [_review("bob")]),
    )
    assert got == "bob"


def test_decided_by_on_a_rerun_is_none_not_another_attempts_reviewer(
    monkeypatch, tmp_path: Path
) -> None:
    """Reviews carry no attempt number, so a re-run is never attributed.

    Attempt 1: bob approved, a deploy step failed. Attempt 2: the review was
    cancelled with no reviewer. Naming bob would credit him with a decision
    he did not make; the same holds for carol's attempt-1 rejection when bob
    approved attempt 2.
    """
    for conclusion, reviews in (
        ("cancelled", [_review("bob")]),
        ("failure", [_review("carol", state="rejected"), _review("bob")]),
        ("success", [_review("carol", state="rejected"), _review("bob")]),
    ):
        routes: list = []
        got = _decided_by(
            monkeypatch,
            tmp_path,
            conclusion=conclusion,
            run_attempt=2,
            mock=lambda r, reviews=reviews, routes=routes: routes.append(
                _mock_approvals(r, reviews)
            ),
        )
        assert got is None, (conclusion, got)
        # Not attributed by policy, not by a failed or unmocked read.
        assert routes[0].call_count == 0


def test_decided_by_matches_environment_case_insensitively(monkeypatch, tmp_path: Path) -> None:
    got = _decided_by(
        monkeypatch,
        tmp_path,
        conclusion="success",
        mock=lambda r: _mock_approvals(r, [_review("bob", env="Prod")]),
    )
    assert got == "bob"


def test_unreadable_history_is_logged(monkeypatch, tmp_path: Path, caplog) -> None:
    with caplog.at_level("WARNING", logger="sigantry_core.approval_gates.github_environments"):
        got = _decided_by(
            monkeypatch,
            tmp_path,
            conclusion="success",
            mock=lambda r: _mock_approvals(r, {"message": "Forbidden"}, status=403),
        )
    assert got is None
    assert "approval_decider_unreadable" in caplog.text
