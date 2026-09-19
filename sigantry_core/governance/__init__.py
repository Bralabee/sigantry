"""sigantry_core.governance - cross-cutting governance primitives.

Plan 03-01 ships:
- destructive_op decorator + DestructiveOpError (WKSP-06)

Plan 03-03 adds:
- labels (LabelInventoryEntry + LabelSyncOutcome + apply_label_to_workspace_items
  + inventory_labels)
- rbac (RbacRow + audit + workspace role CRUD + write_csv)
- principal_expansion (expand_group_members via MS Graph)

Plan 03-04 adds:
- tenant_settings (TenantSettingBaseline + export_baseline + write_baseline;
  Fabric admin ``/v1/admin/tenantsettings`` baseline capture, GOV-05).
"""

from __future__ import annotations

from sigantry_core.governance.audit import DestructiveOpError, destructive_op
from sigantry_core.governance.labels import (
    LabelInventoryEntry,
    LabelSyncOutcome,
    apply_label_to_workspace_items,
    inventory_labels,
)
from sigantry_core.governance.principal_expansion import expand_group_members
from sigantry_core.governance.rbac import (
    RbacRow,
    add_role_assignment,
    audit,
    delete_role_assignment,
    list_role_assignments,
    write_csv,
)
from sigantry_core.governance.tenant_settings import (
    TenantSettingBaseline,
    export_baseline,
    write_baseline,
)

__all__ = [
    "DestructiveOpError",
    "LabelInventoryEntry",
    "LabelSyncOutcome",
    "RbacRow",
    "TenantSettingBaseline",
    "add_role_assignment",
    "apply_label_to_workspace_items",
    "audit",
    "delete_role_assignment",
    "destructive_op",
    "expand_group_members",
    "export_baseline",
    "inventory_labels",
    "list_role_assignments",
    "write_baseline",
    "write_csv",
]
