"""sigantry -- base platform for Sigantry plugins.

(Renamed from ``fabric-dataops-toolkits`` in v3.0 per ADR-0011.)

Public surface (Plan 08-01 Phase 8 productization):

- :class:`sigantry_core.api.FabricDataOps` -- front door (11 protocol
  seams post-Audit-2026-05-07 W2.4).
- :mod:`sigantry_core.protocols` -- ``typing.Protocol`` seams that
  plugins implement.
- :mod:`sigantry_core.registry` -- entry-point + direct-DI plugin
  registry.
- :mod:`sigantry_core.config` -- ``pydantic-settings`` TOML loader.
- :mod:`sigantry_core.testing` -- in-memory doubles for contract
  tests.

Legacy submodules (``auth``, ``client``, ``workspace``, ``capacity``,
``governance``, ``monitor``, ``deploy``, ``dq``, ``pipelines``, ``purview``,
``utils``, ``cli``) remain functional under the v1 surface; dispatcher
refactors onto the protocol seams land in Plan 08-02.

Legacy-name shim (post-Audit-2026-05-07 W2.7):
----------------------------------------------
The in-package ``_LegacyShimFinder`` meta-path finder that aliased
``fabric_dataops_toolkits.X`` -> ``sigantry_core.X`` was DROPPED in W2.7.
``import fabric_dataops_toolkits.X`` now works ONLY when the v2 shim
wheel (``shim/fabric-dataops-toolkits/`` published as
``fabric-dataops-toolkits==3.0.*``) is installed alongside ``sigantry``.
The dist shim was already the canonical migration path per ADR-0011 +
Plan 10-05; the in-package finder was a redundant duplicate.

The dist shim itself drops in Sigantry v3.1 per ADR-0011. It is NOT
considered public API.
"""

from __future__ import annotations

from sigantry_core._version import __version__
from sigantry_core.api import FabricDataOps
from sigantry_core.protocols import (
    ApprovalContext,
    ApprovalDecision,
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRequest,
    AuthProvider,
    CapacityAction,
    CapacityApplyResult,
    CapacityContext,
    CapacityPolicy,
    ChangedFile,
    Closeable,
    DataQualityGate,
    DataRef,
    DeployContext,
    DeployPlan,
    DeployProfile,
    DeployResult,
    GateResult,
    NotificationEvent,
    NotificationLevel,
    NotificationSink,
    PrReviewBot,
    PullRequest,
    RunbookRegistry,
    Secret,
    SecretStore,
    TelemetryEvent,
    TelemetrySink,
    WorkItem,
    WorkItemProvider,
)

__all__ = [
    "ApprovalContext",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalOutcome",
    "ApprovalRequest",
    "AuthProvider",
    "CapacityAction",
    "CapacityApplyResult",
    "CapacityContext",
    "CapacityPolicy",
    "ChangedFile",
    "Closeable",
    "DataQualityGate",
    "DataRef",
    "DeployContext",
    "DeployPlan",
    "DeployProfile",
    "DeployResult",
    "FabricDataOps",
    "GateResult",
    "NotificationEvent",
    "NotificationLevel",
    "NotificationSink",
    "PrReviewBot",
    "PullRequest",
    "RunbookRegistry",
    "Secret",
    "SecretStore",
    "TelemetryEvent",
    "TelemetrySink",
    "WorkItem",
    "WorkItemProvider",
    "__version__",
]
