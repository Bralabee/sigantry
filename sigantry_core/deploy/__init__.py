"""sigantry_core.deploy - thin wrapper over fabric-cicd 1.0.0 + Fabric REST Git/VL APIs.

Plan 04-01 shipped:
  - ``deploy_workspace`` (DEPLOY-01)
  - ``load_and_validate`` (parameters.yml validator, DEPLOY-03)
  - ``validate_order`` (dependency cycle detector, DEPLOY-04)

Plan 04-02 added:
  - ``copy_item`` (DEPLOY-02 — Fabric item folder duplicator with logicalId
    regeneration + LF enforcement; Pitfall 3 core mitigation).

Plan 04-03 adds:
  - ``git_integration`` — 7 Fabric Core Git REST wrappers (DEPLOY-05)
  - ``variable_library`` — full CRUD + 4-part InlineBase64 definition builder (DEPLOY-06)
  - ``environment.sync_wheel`` — Fabric Environment wheel upload primitive
    (Pitfall 6 mitigation; Phase 7 INTEG-01 consumes it)
"""

from __future__ import annotations

from sigantry_core.deploy.core import DeployResult, deploy_workspace
from sigantry_core.deploy.dependency import (
    DependencyCycleError,
    validate_order,
)
from sigantry_core.deploy.environment import (
    WheelHashMismatchError,
    WheelTooLargeError,
    WheelUploadResult,
    sync_wheel,
)
from sigantry_core.deploy.git_integration import (
    GitConnection,
    commit_to_git,
    connect_azdo,
    connect_or_reconnect,
    disconnect,
    get_connection,
    get_status,
    initialize_connection,
    update_from_git,
)
from sigantry_core.deploy.item_copy import ItemCopyError, copy_item
from sigantry_core.deploy.parameters import (
    HardcodedGuidError,
    ParametersConfig,
    load_and_validate,
)
from sigantry_core.deploy.variable_library import (
    Variable,
    VariableLibrary,
    build_definition,
    create_variable_library,
    delete_variable_library,
    get_variable_library,
    list_variable_libraries,
    update_variable_library,
)

__all__ = [
    "DependencyCycleError",
    "DeployResult",
    "GitConnection",
    "HardcodedGuidError",
    "ItemCopyError",
    "ParametersConfig",
    "Variable",
    "VariableLibrary",
    "WheelHashMismatchError",
    "WheelTooLargeError",
    "WheelUploadResult",
    "build_definition",
    "commit_to_git",
    "connect_azdo",
    "connect_or_reconnect",
    "copy_item",
    "create_variable_library",
    "delete_variable_library",
    "deploy_workspace",
    "disconnect",
    "get_connection",
    "get_status",
    "get_variable_library",
    "initialize_connection",
    "list_variable_libraries",
    "load_and_validate",
    "sync_wheel",
    "update_from_git",
    "update_variable_library",
    "validate_order",
]
