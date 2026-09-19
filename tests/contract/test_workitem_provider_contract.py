"""WorkItemProvider Protocol contract tests (Phase 11 / TRACE-01..03).

The seventh entry in the per-seam contract suite. Mirrors the canonical
``tests/contract/test_telemetry_sink_contract.py`` shape (six sibling
files in this directory) and replaces the Wave 0 stub with a parametrised
contract battery exercising:

- :class:`sigantry_core.testing.doubles.FakeWorkItemProvider`
- :class:`sigantry_core.workitems.ado.AdoWorkItemProvider`
- :class:`sigantry_core.workitems.github.GithubWorkItemProvider`

Per the planner-mapper resolution (Plan 11-07): WorkItemProvider
implementations do NOT declare an ``api_version`` class attribute at
v3.0. The asymmetry against the future v3.1 cross-seam widening is
deliberate -- the fixture ``fdt_work_item_provider_contract`` enforces
the deferral, and a dedicated standalone test in this file pins the
absence on every concrete implementation. ADR-0004 + 11-RESEARCH.md
record the rationale.

No xfail markers remain in this file. The Wave 0 deferrals (ADO/GitHub
provider impls landing in Plans 11-04 / 11-05) are now fully resolved.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from sigantry_core.auth import TokenProvider
from sigantry_core.protocols import WorkItemProvider
from sigantry_core.testing.doubles import FakeWorkItemProvider

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Provider factories (one per impl). Each factory constructs the provider
# without exercising network -- the contract battery is a pure-Python check
# of name / Protocol membership / signature shape.
# ---------------------------------------------------------------------------


def _fake_provider() -> WorkItemProvider:
    return FakeWorkItemProvider()


def _ado_provider() -> WorkItemProvider:
    pytest.importorskip("sigantry_core.workitems.ado")
    from sigantry_core.workitems.ado import AdoWorkItemProvider

    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "test-token"
    mp.tenant_id = "test-tenant-id"
    return AdoWorkItemProvider(
        organization="contract-org",
        project="contract-proj",
        token_provider=mp,
    )


def _github_provider() -> WorkItemProvider:
    pytest.importorskip("sigantry_core.workitems.github")
    from sigantry_core.workitems.github import GithubWorkItemProvider

    return GithubWorkItemProvider(
        owner="contract-owner",
        repo="contract-repo",
        pat="fake-pat-contract-test",
    )


def _jtoye_provider() -> WorkItemProvider:
    """Construct the sigantry-jtoye stub provider for contract battery (Plan 16-04).

    Skipped when sigantry-jtoye is not editable-installed in the active
    env. SEAM-05 closure proves multi-org plugin authorship works
    against the Phase 11 WorkItemProvider seam.
    """
    pytest.importorskip("sigantry_jtoye.workitems")
    from sigantry_jtoye.workitems import JtoyeWorkItemProvider

    return JtoyeWorkItemProvider()


# ---------------------------------------------------------------------------
# Parametrised contract battery -- the lock on TRACE-01 / TRACE-02 / TRACE-03.
# All three providers MUST pass via the shared ``fdt_work_item_provider_contract``
# fixture. Adding a new provider to the seam means adding one factory above
# and one entry to the parametrize list -- no other code changes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(_fake_provider, id="fake"),
        pytest.param(_ado_provider, id="ado"),
        pytest.param(_github_provider, id="github"),
        pytest.param(
            _jtoye_provider,
            id="jtoye",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("sigantry_jtoye") is None,
                reason="sigantry-jtoye not editable-installed in this env",
            ),
        ),
    ],
)
def test_provider_satisfies_contract(factory, fdt_work_item_provider_contract) -> None:
    """Every concrete WorkItemProvider passes the shared contract assertions."""
    provider = factory()
    fdt_work_item_provider_contract(provider)


# ---------------------------------------------------------------------------
# Standalone tests around the FakeWorkItemProvider -- the recorded-invocation
# behaviour is the value of the double, so it gets a sanity test alongside
# the contract battery.
# ---------------------------------------------------------------------------


def test_fake_provider_records_link_release_invocations() -> None:
    """The double records (release_id, work_items, deploy_record) tuples."""
    pytest.importorskip("sigantry_core.release.record")
    from sigantry_core.release.record import DeployRecord

    record = DeployRecord(
        workspace="contract-ws",
        release_id="R-contract",
        work_items=["1"],
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, tzinfo=UTC),
    ).with_hash()

    provider = FakeWorkItemProvider()
    provider.link_release("R-contract", ["1", "2"], record)
    assert len(provider.linked) == 1
    rid, ids, rec = provider.linked[0]
    assert rid == "R-contract"
    assert ids == ["1", "2"]
    assert rec.audit_hash == record.audit_hash


def test_fake_provider_records_ping_invocations() -> None:
    """The double counts ping calls in ``self.pinged``."""
    provider = FakeWorkItemProvider()
    provider.ping()
    provider.ping()
    provider.ping()
    assert provider.pinged == 3


def test_fake_provider_records_fetch_invocations() -> None:
    """The double records each ``ids`` argument in ``self.fetch_calls``."""
    provider = FakeWorkItemProvider()
    provider.fetch_work_items(["1", "2"])
    provider.fetch_work_items(["3"])
    assert provider.fetch_calls == [["1", "2"], ["3"]]


# ---------------------------------------------------------------------------
# Planner-mapper resolution: NO concrete provider declares api_version
# at v3.0. Adding it to one provider without coordinating across all seven
# seams is a contract break (would unbalance the symmetry recorded in
# protocols.py module docstring). The standalone test pins this on every
# real impl, complementing the same assertion in the contract fixture.
# ---------------------------------------------------------------------------


def test_no_provider_declares_api_version_at_v3_0() -> None:
    """Per planner-mapper resolution: cross-seam widening deferred to v3.1."""
    pytest.importorskip("sigantry_core.workitems.ado")
    pytest.importorskip("sigantry_core.workitems.github")
    from sigantry_core.workitems.ado import AdoWorkItemProvider
    from sigantry_core.workitems.github import GithubWorkItemProvider

    for cls in (AdoWorkItemProvider, GithubWorkItemProvider, FakeWorkItemProvider):
        attr = getattr(cls, "api_version", None)
        assert not isinstance(attr, str), (
            f"{cls.__name__}.api_version must NOT be a class-level string at "
            "v3.0 -- cross-seam widening is deferred to v3.1 per planner-mapper "
            "resolution. See sigantry_core/protocols.py module docstring."
        )
