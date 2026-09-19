"""sigantry_core.capacity - Fabric capacity inspection + lifecycle.

Plan 03-02 ships:
- Capacity DTO + :func:`list_capacities` (WKSP-04, Fabric Core ``/v1/capacities``).
- :func:`suspend_capacity` + :func:`resume_capacity` (WKSP-05, ARM LRO) -
  gated by ``@destructive_op("capacity", "pause"|"resume")`` which requires
  BOTH ``force=True`` AND a non-empty ``runbook_id`` (Pitfall 11).
"""

from __future__ import annotations

from sigantry_core.capacity.core import Capacity, list_capacities
from sigantry_core.capacity.lifecycle import resume_capacity, suspend_capacity

__all__ = [
    "Capacity",
    "list_capacities",
    "resume_capacity",
    "suspend_capacity",
]
