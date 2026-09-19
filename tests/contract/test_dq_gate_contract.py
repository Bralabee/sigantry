"""DataQualityGate contract tests.

Parametrises over (double, plugin) pairs. The plugin's
``DqFrameworkGate.run`` is not invoked here — it late-imports the peer
``dq_framework`` package at call-time and needs credentials for a real
suite — but its protocol conformance and ``name`` field are asserted.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    DataQualityGate,
    DataRef,
    GateResult,
)
from sigantry_core.testing.doubles import NoopGate


def _plugin_gate_or_skip():
    pytest.importorskip("sigantry_hs2")
    from sigantry_hs2.dq.dq_framework_gate import DqFrameworkGate

    return DqFrameworkGate()


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


@pytest.mark.contract
def test_dq_gate_plugin_has_name(fdt_dq_gate_contract) -> None:
    fdt_dq_gate_contract(_plugin_gate_or_skip())


@pytest.mark.contract
def test_dq_gate_plugin_satisfies_runtime_protocol() -> None:
    assert isinstance(_plugin_gate_or_skip(), DataQualityGate)


@pytest.mark.contract
def test_dq_gate_plugin_name_is_dq_framework() -> None:
    """Plugin's canonical registered name is the ``dq_framework`` entry point."""
    gate = _plugin_gate_or_skip()
    assert gate.name == "dq_framework"
