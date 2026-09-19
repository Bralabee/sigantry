"""DataQualityGate dispatcher (PROD-06).

Thin registry lookup + invocation of the configured gate plugin.  The base
package does not import any DQ framework directly; plugin packages provide
the concrete :class:`DataQualityGate` implementation.

Resolution order:

1. An explicit ``gate`` kwarg bypasses registry lookup (direct DI -- the
   path tests usually take).
2. Otherwise ``gate_name`` (or ``settings.dq.gate``) is resolved via the
   plugin :class:`~sigantry_core.registry.Registry` under the
   ``sigantry.dq_gates`` entry-point group.
3. If neither is provided a :class:`ValueError` is raised with instructions
   for wiring it up.

Audit-2026-05-07 W2.2: registry probe goes through the canonical
:func:`sigantry_core._dispatch.resolve_seam` helper.
"""

from __future__ import annotations

from typing import cast

from sigantry_core._dispatch import resolve_seam
from sigantry_core.protocols import DataQualityGate, DataRef, GateResult
from sigantry_core.registry import GROUP_DQ_GATES, Registry


def run_gate(
    suite: str,
    data_ref: DataRef,
    *,
    gate: DataQualityGate | None = None,
    gate_name: str | None = None,
    registry: Registry | None = None,
) -> GateResult:
    """Resolve a :class:`DataQualityGate` and run a suite against ``data_ref``.

    Parameters
    ----------
    suite
        Name / identifier of the suite to execute (e.g. ``"bronze_trips"``).
    data_ref
        Dictionary referencing the dataset to validate.
    gate
        Explicit implementation instance.  When provided, registry lookup is
        skipped.
    gate_name
        Name of the plugin registered under ``sigantry.dq_gates``.
    registry
        Explicit :class:`~sigantry_core.registry.Registry`.  Defaults to
        :func:`default_registry`.

    Returns
    -------
    GateResult
        The gate implementation's result object, propagated verbatim.

    Raises
    ------
    ValueError
        If neither ``gate`` nor ``gate_name`` is provided.
    KeyError
        If ``gate_name`` is not registered under the ``dq_gates`` group.
    """
    if gate is None and gate_name is None:
        raise ValueError(
            "run_gate requires either a pre-built gate or a gate_name to "
            "resolve from the registry; set settings.dq.gate or pass "
            "gate= / gate_name= explicitly."
        )
    resolved = resolve_seam(
        GROUP_DQ_GATES,
        gate_name,
        impl=gate,
        registry=registry,
    )
    if resolved is None:
        raise KeyError(f"DQ gate {gate_name!r} could not be resolved from registry")
    return cast(GateResult, resolved.run(suite, data_ref))


__all__ = ["run_gate"]
