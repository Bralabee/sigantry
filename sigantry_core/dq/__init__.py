"""sigantry_core.dq - Data Quality dispatcher (PROD-06).

The base package defines the :class:`DataQualityGate` protocol seam and a
thin :func:`run_gate` dispatcher that looks up a registered gate plugin. No
DQ framework is imported at the base layer; plugins supply concrete gate
implementations under the ``sigantry.dq_gates`` entry-point group (the
legacy ``fabric_dataops_toolkits.dq_gates`` group is dual-read during v3.0).
"""

from sigantry_core.dq.cli import dq_app
from sigantry_core.dq.dispatcher import run_gate
from sigantry_core.protocols import DataRef, GateResult

__all__ = [
    "DataRef",
    "GateResult",
    "dq_app",
    "run_gate",
]
