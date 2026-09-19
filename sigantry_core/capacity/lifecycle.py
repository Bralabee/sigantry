"""Capacity suspend + resume via ARM (Plan 03-02, WKSP-05, Pitfall 11).

Both entry points are decorated with ``@destructive_op("capacity", ...)``:
force=True AND a non-empty runbook_id are required before any ARM call
fires (Pitfall 11). On success the decorator emits a single audit record
via the Phase 2 correlated logger - see :mod:`sigantry_core.governance.audit`.

The ARM path (:class:`FabricArmRestClient`) is used exclusively here;
Fabric Core does NOT expose suspend/resume (Pitfall 2: wrong audience).

Resource id default: when the caller does not supply ``resource_id``, the
module falls back to the canonical ARM resource id derived from the three
positional args, so the audit record always identifies the target capacity
unambiguously.
"""

from __future__ import annotations

from typing import Any

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricArmRestClient
from sigantry_core.governance.audit import destructive_op


def _resource_id(subscription_id: str, resource_group: str, capacity_name: str) -> str:
    """Canonical ARM resource id (used as the audit record ``resource_id``
    when the caller does not supply one)."""
    return (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Fabric/capacities/{capacity_name}"
    )


def _lifecycle_path(
    subscription_id: str,
    resource_group: str,
    capacity_name: str,
    action: str,
) -> str:
    return (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Fabric/capacities/{capacity_name}/{action}"
    )


@destructive_op("capacity", "pause")
def _suspend_decorated(
    client: FabricArmRestClient,
    subscription_id: str,
    resource_group: str,
    capacity_name: str,
    *,
    force: bool,
    runbook_id: str | None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
) -> Any:
    """POST ``.../capacities/{name}/suspend`` after the destructive-op gate."""
    _ = (principal, token_provider, resource_id)  # consumed by the decorator
    path = _lifecycle_path(subscription_id, resource_group, capacity_name, "suspend")
    return client.send_arm_lro("POST", path)


@destructive_op("capacity", "resume")
def _resume_decorated(
    client: FabricArmRestClient,
    subscription_id: str,
    resource_group: str,
    capacity_name: str,
    *,
    force: bool,
    runbook_id: str | None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
) -> Any:
    """POST ``.../capacities/{name}/resume`` after the destructive-op gate."""
    _ = (principal, token_provider, resource_id)
    path = _lifecycle_path(subscription_id, resource_group, capacity_name, "resume")
    return client.send_arm_lro("POST", path)


def suspend_capacity(
    client: FabricArmRestClient,
    subscription_id: str,
    resource_group: str,
    capacity_name: str,
    *,
    force: bool,
    runbook_id: str | None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
) -> Any:
    """POST ``.../Microsoft.Fabric/capacities/{name}/suspend`` - ARM 202 LRO.

    Both ``force=True`` AND a non-empty ``runbook_id`` are required
    (Pitfall 11) - the inner decorated function raises
    :class:`DestructiveOpError` before the ARM call if either gate fails,
    and no audit record is emitted.

    If the caller omits ``resource_id``, the canonical ARM id derived from
    ``subscription_id`` + ``resource_group`` + ``capacity_name`` is recorded
    in the audit trail so the target capacity is always unambiguous.
    """
    effective_resource_id = resource_id or _resource_id(
        subscription_id, resource_group, capacity_name
    )
    return _suspend_decorated(
        client,
        subscription_id,
        resource_group,
        capacity_name,
        force=force,
        runbook_id=runbook_id,
        principal=principal,
        token_provider=token_provider,
        resource_id=effective_resource_id,
    )


def resume_capacity(
    client: FabricArmRestClient,
    subscription_id: str,
    resource_group: str,
    capacity_name: str,
    *,
    force: bool,
    runbook_id: str | None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
) -> Any:
    """POST ``.../Microsoft.Fabric/capacities/{name}/resume`` - ARM 202 LRO.

    Symmetric to :func:`suspend_capacity`; same destructive-op gate + same
    canonical ARM ``resource_id`` fallback.
    """
    effective_resource_id = resource_id or _resource_id(
        subscription_id, resource_group, capacity_name
    )
    return _resume_decorated(
        client,
        subscription_id,
        resource_group,
        capacity_name,
        force=force,
        runbook_id=runbook_id,
        principal=principal,
        token_provider=token_provider,
        resource_id=effective_resource_id,
    )
