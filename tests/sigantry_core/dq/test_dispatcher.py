"""Unit tests for :mod:`sigantry_core.dq.dispatcher`.

PROD-06 invariant: ``run_gate`` is a thin registry lookup + delegation.
"""

from __future__ import annotations

import pytest

from sigantry_core.dq.dispatcher import run_gate
from sigantry_core.protocols import DataRef, GateResult
from sigantry_core.registry import Registry
from sigantry_core.testing.doubles import NoopGate


def _ref() -> DataRef:
    return DataRef(name="bronze.orders", path="/Workspace/Bronze/orders")


def test_run_gate_with_injected_gate_returns_result() -> None:
    """Direct DI: gate.run output propagated verbatim."""
    result = run_gate("orders_suite", _ref(), gate=NoopGate())

    assert isinstance(result, GateResult)
    assert result.success is True
    assert result.suite == "orders_suite"
    assert result.violations == 0
    assert result.evaluated == 1


def test_run_gate_resolves_via_registry() -> None:
    """A named class in the registry is instantiated and invoked."""
    reg = Registry()
    reg.register("sigantry.dq_gates", "noop", NoopGate)

    result = run_gate("s", _ref(), gate_name="noop", registry=reg)
    assert result.success is True
    assert result.suite == "s"


def test_run_gate_resolves_instance_factories() -> None:
    """An already-instantiated gate registered under a name is used as-is."""
    pre_built = NoopGate()
    reg = Registry()
    reg.register("sigantry.dq_gates", "noop", pre_built)

    result = run_gate("s", _ref(), gate_name="noop", registry=reg)
    assert isinstance(result, GateResult)


def test_run_gate_missing_both_raises_value_error() -> None:
    """No gate, no gate_name -> ValueError naming the wiring options."""
    with pytest.raises(ValueError, match="gate_name"):
        run_gate("s", _ref())


def test_run_gate_unknown_name_raises_key_error() -> None:
    """Unregistered gate_name -> KeyError mentioning the name."""
    reg = Registry()
    with pytest.raises(KeyError, match="nope"):
        run_gate("s", _ref(), gate_name="nope", registry=reg)


class _BoomGate:
    name = "boom"

    def run(self, suite: str, data_ref: DataRef) -> GateResult:
        raise RuntimeError("gate crashed")


def test_run_gate_propagates_gate_exception() -> None:
    """Gate-raised exceptions propagate unchanged."""
    with pytest.raises(RuntimeError, match="gate crashed"):
        run_gate("s", _ref(), gate=_BoomGate())


class _CountingGate:
    name = "counting"

    def __init__(self) -> None:
        self.calls: list[tuple[str, DataRef]] = []

    def run(self, suite: str, data_ref: DataRef) -> GateResult:
        self.calls.append((suite, data_ref))
        return GateResult(suite=suite, success=True, violations=0, evaluated=7, run_id="r-1")


def test_run_gate_passes_suite_and_dataref_verbatim() -> None:
    """The dispatcher does not mutate its inputs before forwarding."""
    gate = _CountingGate()
    ref = _ref()
    run_gate("bronze_orders", ref, gate=gate)

    assert gate.calls == [("bronze_orders", ref)]
