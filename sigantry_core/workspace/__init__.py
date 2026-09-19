"""sigantry_core.workspace - thin domain wrapper over Fabric Core /v1/workspaces.

Phase 3 Plan 03-01 ships:
- Workspace DTO + CRUD (WKSP-01)
- Item DTO + list_items (WKSP-02)
- assign_to_capacity LRO (WKSP-03)

Destructive ops (delete_workspace) are gated by the @destructive_op decorator
from sigantry_core.governance.audit (WKSP-06).
"""

from __future__ import annotations

from sigantry_core.workspace.capacity import assign_to_capacity
from sigantry_core.workspace.core import (
    Workspace,
    create_workspace,
    delete_workspace,
    get_workspace,
    list_workspaces,
    update_workspace,
)
from sigantry_core.workspace.folders import (
    Folder,
    create_folder,
    delete_folder,
    list_folders,
    move_item,
)
from sigantry_core.workspace.items import Item, list_items

__all__ = [
    "Folder",
    "Item",
    "Workspace",
    "assign_to_capacity",
    "create_folder",
    "create_workspace",
    "delete_folder",
    "delete_workspace",
    "get_workspace",
    "list_folders",
    "list_items",
    "list_workspaces",
    "move_item",
    "update_workspace",
]
