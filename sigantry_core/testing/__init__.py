"""In-memory doubles for the seven protocol seams (PROD-04).

Public re-exports used by tests and by the Plan 08-06 contract-test fixture
plugin. These doubles are intentionally trivial -- they exist so consumers
can instantiate ``FabricDataOps(...)`` in a pytest session without pulling in
any real plugin. ``FakeWorkItemProvider`` is the seventh double, added in
Phase 11 alongside the seventh seam (``WorkItemProvider``).
"""

from __future__ import annotations

from sigantry_core.testing.doubles import (
    FakeAuth,
    FakeDeployProfile,
    FakeWorkItemProvider,
    InMemoryTelemetrySink,
    NoopCapacityPolicy,
    NoopGate,
    StaticRunbookRegistry,
)

__all__ = [
    "FakeAuth",
    "FakeDeployProfile",
    "FakeWorkItemProvider",
    "InMemoryTelemetrySink",
    "NoopCapacityPolicy",
    "NoopGate",
    "StaticRunbookRegistry",
]
