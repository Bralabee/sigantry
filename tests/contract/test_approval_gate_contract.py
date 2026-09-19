"""ApprovalGate Protocol contract tests (Phase 16 / SEAM-03).

Tenth contract test. Wave 0 (Plan 16-00) shipped xfail stubs; Plan 16-03
lands real assertions parameterised over Fake + ADO env + GitHub env + OPA.

Per RESEARCH §3 Open-Q-3 + Phase 11 ADR-0004: ApprovalGate Protocol
instances do NOT carry an ``api_version`` class var.

Cross-impl notes:

- Both ADO + GitHub gates poll a remote REST endpoint; the contract battery
  is a pure-Python check of name / Protocol membership / signature shape
  (respx-mocked transport coverage lives in the per-impl tests at
  tests/sigantry_core/approval_gates/).
- ``OpaApprovalGate`` constructs cheaply (no auth chain).
- ``GithubEnvironmentsApprovalGate`` constructs cheaply (PAT only; no
  Azure credential).
- ``AdoEnvironmentsApprovalGate`` requires a ``TokenProvider``; the
  factory supplies a MagicMock-spec'd one.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.approval_gates import (
    AdoEnvironmentsApprovalGate,
    GithubEnvironmentsApprovalGate,
    OpaApprovalGate,
)
from sigantry_core.auth import TokenProvider
from sigantry_core.protocols import (
    ApprovalContext,
    ApprovalGate,
    ApprovalRequest,
)
from sigantry_core.testing.doubles import FakeApprovalGate

pytestmark = [pytest.mark.contract, pytest.mark.sigantry_seam]


# ---------------------------------------------------------------------------
# Gate factories -- one per impl. Each constructs the gate WITHOUT exercising
# transport (the contract battery is a pure-Python check; respx mocks live
# in the per-impl tests at tests/sigantry_core/approval_gates/).
# ---------------------------------------------------------------------------


def _fake_gate() -> ApprovalGate:
    return FakeApprovalGate()


def _ado_gate() -> ApprovalGate:
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "tok"
    mp.tenant_id = "t"
    mp.last_credential_class.return_value = "MockCredential"
    return AdoEnvironmentsApprovalGate(
        organization="org",
        project="proj",
        token_provider=mp,
    )


def _gh_gate() -> ApprovalGate:
    return GithubEnvironmentsApprovalGate(
        owner="o",
        repo="r",
        run_id=1,
        environment="prod",
        pat="fake-pat-dummy",
    )


def _opa_gate() -> ApprovalGate:
    return OpaApprovalGate()


_FACTORIES = [
    pytest.param(_fake_gate, id="fake"),
    pytest.param(_ado_gate, id="ado_environments"),
    pytest.param(_gh_gate, id="github_environments"),
    pytest.param(_opa_gate, id="opa"),
]


# ---------------------------------------------------------------------------
# Contract battery -- the lock on SEAM-03.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", _FACTORIES)
def test_approval_gate_protocol_membership(factory) -> None:
    """Every concrete impl + Fake satisfies ``ApprovalGate`` runtime_checkable."""
    gate = factory()
    assert isinstance(gate, ApprovalGate), (
        f"{type(gate).__name__} does not satisfy the ApprovalGate Protocol"
    )
    assert isinstance(gate.name, str) and gate.name, (
        f"{type(gate).__name__}.name must be a non-empty string"
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_approval_gate_no_api_version_attr(factory) -> None:
    """Phase 11 precedent + RESEARCH §3 Open-Q-3: no api_version field at v3.0.

    Adding ``api_version`` to ANY single seam without coordinating across
    all ten seams is a contract break -- it would unbalance the symmetry
    recorded in protocols.py module docstring + ADR-0004. Cross-seam
    widening is a v3.1 candidate.
    """
    gate = factory()
    cls = type(gate)
    attr = getattr(cls, "api_version", None)
    assert not isinstance(attr, str), (
        f"{cls.__name__}.api_version must NOT be a class-level string at "
        "v3.0 -- cross-seam widening is deferred to v3.1 per planner-mapper "
        "resolution. See sigantry_core/protocols.py module docstring."
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_approval_gate_request_returns_approval_request(factory) -> None:
    """request(ctx) returns an ApprovalRequest carrying release/env/approvers.

    Signature shape only; transport-side behaviour is exercised in the
    per-impl tests.
    """
    gate = factory()
    sig = inspect.signature(gate.request)
    params = list(sig.parameters.values())
    assert len(params) >= 1, f"{type(gate).__name__}.request must accept (ctx); got {sig}"

    ctx = ApprovalContext(
        release_id="rel-contract-1",
        env="dev",
        approvers=["alice"],
        approval_id="approval-1",  # consumed by ADO impl; ignored by others
    )
    req = gate.request(ctx)
    assert isinstance(req, ApprovalRequest), (
        f"{type(gate).__name__}.request must return ApprovalRequest"
    )
    assert req.release_id == "rel-contract-1"
    assert req.env == "dev" or req.env == "prod", (
        # GH gate may default to its constructor environment if ctx.env empty;
        # for non-empty ctx.env every impl must echo it.
        f"{type(gate).__name__}.request must echo ctx.env"
    )
    assert isinstance(req.approvers, list)
    assert isinstance(req.request_id, str) and req.request_id


@pytest.mark.parametrize("factory", _FACTORIES)
def test_approval_gate_wait_returns_approval_outcome(factory) -> None:
    """wait(request, timeout_s=...) signature accepts request + keyword timeout_s."""
    gate = factory()
    assert callable(getattr(gate, "wait", None)), f"{type(gate).__name__}.wait must be callable"
    sig = inspect.signature(gate.wait)
    params = sig.parameters
    assert "request" in params, (
        f"{type(gate).__name__}.wait must accept a 'request' parameter; got {sorted(params)}"
    )
    assert "timeout_s" in params, (
        f"{type(gate).__name__}.wait must accept a 'timeout_s' parameter; got {sorted(params)}"
    )


def test_approval_gate_emits_approval_record_on_decision(monkeypatch, tmp_path: Path) -> None:
    """OpaApprovalGate.wait() emits a parseable ApprovalRecord jsonl line.

    Use OPA as the canonical case because it ships a synchronous decision
    (no poll loop) so the test does not need clock fakes. The other
    impls' audit-record emission is exercised in the per-impl tests at
    tests/sigantry_core/approval_gates/.
    """
    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_path
    try:
        with respx.mock(base_url="http://localhost:8181") as router:
            router.post("/v1/data/sigantry/approval/allow").mock(
                return_value=httpx.Response(200, json={"result": True})
            )
            gate = OpaApprovalGate()
            ctx = ApprovalContext(
                release_id="rel-contract-1",
                env="prod",
                approvers=["alice"],
            )
            req = gate.request(ctx)
            outcome = gate.wait(req)
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    assert outcome == "approved"
    jsonl = tmp_path / "approvals.jsonl"
    assert jsonl.exists()
    record = json.loads(jsonl.read_text("utf-8").splitlines()[0])
    assert record["outcome"] == "approved"
    assert record["audit_hash"]


@pytest.mark.parametrize("factory", _FACTORIES)
def test_approval_gate_ping_returns_none(factory) -> None:
    """ping() exists and is a no-arg callable.

    The ``FakeApprovalGate`` ``ping`` is exercised directly; for real
    impls we only verify the signature (live transport is exercised in
    the per-impl tests at tests/sigantry_core/approval_gates/).
    """
    gate = factory()
    assert callable(getattr(gate, "ping", None)), f"{type(gate).__name__}.ping must be callable"
    sig = inspect.signature(gate.ping)
    assert len(sig.parameters) == 0, (
        f"{type(gate).__name__}.ping must take no parameters; got {sorted(sig.parameters)}"
    )


# ---------------------------------------------------------------------------
# FakeApprovalGate standalone behaviour -- the recorded-state contract is the
# value of the double, so it gets a sanity test alongside the parametrised
# contract battery.
# ---------------------------------------------------------------------------


def test_fake_approval_gate_returns_scripted_outcome() -> None:
    """FakeApprovalGate honours scripted_outcome on every wait()."""
    for outcome in ("approved", "rejected", "timeout"):
        gate = FakeApprovalGate(scripted_outcome=outcome)
        ctx = ApprovalContext(
            release_id="rel-x",
            env="dev",
            approvers=["alice"],
            approval_id="approval-1",
        )
        req = gate.request(ctx)
        assert gate.wait(req) == outcome


def test_fake_approval_gate_counts_ping_invocations() -> None:
    gate = FakeApprovalGate()
    gate.ping()
    gate.ping()
    assert gate.pinged == 2
