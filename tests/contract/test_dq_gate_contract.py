"""DataQualityGate contract tests.

Runs the contract against the ``NoopGate`` double: protocol conformance,
the ``name`` field, and the ``GateResult`` returned by ``run``.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    DataQualityGate,
    DataRef,
    GateResult,
)
from sigantry_core.testing.doubles import NoopGate


@pytest.mark.contract
def test_dq_gate_double_has_name(fdt_dq_gate_contract) -> None:
    fdt_dq_gate_contract(NoopGate())


@pytest.mark.contract
def test_dq_gate_double_run_returns_gate_result() -> None:
    gate = NoopGate()
    result = gate.run("suite-A", DataRef(name="ds", path="/tmp/ds.parquet"))
    assert isinstance(result, GateResult)
    assert result.success is True
    assert result.suite == "suite-A"


@pytest.mark.contract
def test_dq_gate_double_satisfies_runtime_protocol() -> None:
    assert isinstance(NoopGate(), DataQualityGate)
