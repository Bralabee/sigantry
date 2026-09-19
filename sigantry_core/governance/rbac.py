"""Workspace + capacity + item RBAC audit (GOV-03 + GOV-04).

Three-layer schema (RESEARCH Pitfall 12): every row carries
``(layer, resource_id, resource_name, principal_id, principal_type,
principal_name, role, access_status)``. NEVER flattened into a single
"who can access what" view.

The item layer emits a placeholder row per workspace because no stable
Fabric REST surface exists for per-item ACL read as of 2026-04 (A5). The
placeholder keeps the audit truthful: callers know an item layer exists but
cannot be enumerated programmatically.

Capacity 403 → ``access_status="forbidden-admin-only"`` row (Pitfall 5);
non-admin SPs lack ``Power BI Admin`` and the audit reflects that explicitly
rather than silently dropping the layer.

Group principals expand transitively via MS Graph
``/v1.0/groups/{id}/transitiveMembers``. Member rows carry
``access_status="via-group:<group displayName or id>"`` so auditors can
trace the inherited permission. T-3-08 mitigation: member identities flow
to the CSV / JSON output ONLY — never to the correlated log stream.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from sigantry_core.client import FabricRestClient, HttpError, PowerBIRestClient
from sigantry_core.governance.audit import destructive_op
from sigantry_core.governance.principal_expansion import (
    _GraphClient,
    expand_group_members,
)

_VALID_ROLES: frozenset[str] = frozenset({"Admin", "Member", "Contributor", "Viewer"})
_VALID_PRINCIPAL_TYPES: frozenset[str] = frozenset({"User", "Group", "ServicePrincipal"})

_CSV_HEADER: tuple[str, ...] = (
    "layer",
    "resource_id",
    "resource_name",
    "principal_id",
    "principal_type",
    "principal_name",
    "role",
    "access_status",
)


@dataclass(frozen=True, slots=True)
class RbacRow:
    """One row in the three-layer RBAC audit.

    ``layer`` is one of ``"capacity"`` | ``"workspace"`` | ``"item"``.
    ``access_status`` is one of:

    - ``"ok"`` - direct, readable assignment.
    - ``"via-group:<name>"`` - inherited through the named Entra group.
    - ``"forbidden-admin-only"`` - the layer requires admin scope the
      caller does not possess (Pitfall 5).
    - ``"not-accessible-via-rest"`` - no programmatic ACL read surface
      exists (item layer placeholder, A5).
    """

    layer: str
    resource_id: str
    resource_name: str
    principal_id: str
    principal_type: str  # "User" | "ServicePrincipal" | "Group" | ""
    principal_name: str
    role: str
    access_status: str


# ---------------------------------------------------------------------------
# Workspace role CRUD (GOV-03)
# ---------------------------------------------------------------------------


def add_role_assignment(
    fabric: FabricRestClient,
    workspace_id: str,
    *,
    principal_id: str,
    principal_type: str,
    role: str,
) -> dict[str, Any]:
    """``POST /v1/workspaces/{id}/roleAssignments`` - 201 sync.

    Note (Pitfall 10): group role assignments take 5-15 minutes to
    propagate across Entra. Callers must not assert immediate effect.

    Raises:
        ValueError: ``role`` not in
            ``{Admin, Member, Contributor, Viewer}`` or ``principal_type``
            not in ``{User, Group, ServicePrincipal}``.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}; got {role!r}")
    if principal_type not in _VALID_PRINCIPAL_TYPES:
        raise ValueError(
            "principal_type must be one of "
            f"{sorted(_VALID_PRINCIPAL_TYPES)}; got {principal_type!r}"
        )
    resp = fabric.send(
        "POST",
        f"/v1/workspaces/{workspace_id}/roleAssignments",
        json={
            "principal": {"id": principal_id, "type": principal_type},
            "role": role,
        },
    )
    body = resp.json_body if isinstance(resp.json_body, dict) else None
    return body or {}


@destructive_op(
    "role_assignment",
    "delete",
    resource_arg=("workspace_id", "principal_id"),
)
def delete_role_assignment(
    fabric: FabricRestClient,
    workspace_id: str,
    principal_id: str,
    *,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
) -> None:
    """``DELETE /v1/workspaces/{id}/roleAssignments/{principalId}`` - 200 sync.

    Audit-2026-05-07 W1.7: prior to remediation this verb bypassed the
    destructive-op gate entirely — no ``force=True`` requirement, no
    audit emission, no runbook reference. The contradiction with
    CLAUDE.md's "destructive ops require force=True + audit entry"
    posture was a critical trust-boundary break: an operator could
    revoke any principal's role on any workspace without leaving an
    audit trail.

    Decorator-enforced: ``force=True`` MUST be passed at call site.
    ``runbook_id`` is recommended (``RBAC-DELETE-ROLE-001``-style) and
    ``principal`` is best-effort identification of the operator
    initiating the revocation.
    """
    fabric.send(
        "DELETE",
        f"/v1/workspaces/{workspace_id}/roleAssignments/{principal_id}",
    )


def list_role_assignments(
    fabric: FabricRestClient,
    workspace_id: str,
) -> Iterator[dict[str, Any]]:
    """``GET /v1/workspaces/{id}/roleAssignments`` - paginated.

    Yields raw dicts; downstream :func:`audit` consumes the dict shape
    directly so we do not introduce a separate role-assignment DTO here.
    """
    yield from fabric.list_paginated(f"/v1/workspaces/{workspace_id}/roleAssignments")


# ---------------------------------------------------------------------------
# Audit generator (GOV-04)
# ---------------------------------------------------------------------------


def audit(
    fabric: FabricRestClient,
    powerbi: PowerBIRestClient,
    *,
    graph_client: _GraphClient | None = None,
    workspace_ids: Sequence[str] | None = None,
) -> Iterator[RbacRow]:
    """Yield every RbacRow visible to the caller across workspace + capacity
    + item layers.

    Order is deterministic: for each workspace (in listing order) yield all
    role-assignment rows (with group expansions inline), then the item-layer
    placeholder, then proceed to the next workspace. After all workspaces,
    emit capacity rows.

    ``workspace_ids`` scopes the audit: when given, only those workspaces
    are audited (resolved individually via ``GET /v1/workspaces/{id}`` so a
    typo'd id fails loudly with :class:`ValueError` instead of being
    silently skipped; a 401/403 on the resolve degrades to the standard
    ``forbidden-admin-only`` placeholder downstream). The capacity layer
    then narrows to the capacities those workspaces are assigned to --
    every scoped ``capacityId`` is audited even if the caller cannot see it
    in the tenant capacity listing, keeping the three-layer schema truthful.
    Default (``None``) is the tenant-wide sweep, unchanged.

    Rows are NEVER flattened. Placeholder rows (forbidden-admin-only,
    not-accessible-via-rest) make missing data visible to auditors instead
    of silently dropping the layer.
    """
    gc = graph_client or _GraphClient.from_defaults()

    scoped_capacity_ids: list[str] = []
    workspaces: Iterable[dict[str, Any]]
    if workspace_ids is None:
        workspaces = fabric.list_paginated("/v1/workspaces")
    else:
        workspaces = _resolve_scoped_workspaces(fabric, workspace_ids, scoped_capacity_ids)

    # ---- workspace + item layers ------------------------------------
    for ws in workspaces:
        ws_id = ws["id"]
        ws_name = ws.get("displayName", "")

        try:
            for ra in fabric.list_paginated(f"/v1/workspaces/{ws_id}/roleAssignments"):
                principal = ra.get("principal") or {}
                yield RbacRow(
                    layer="workspace",
                    resource_id=ws_id,
                    resource_name=ws_name,
                    principal_id=principal.get("id", ""),
                    principal_type=principal.get("type", ""),
                    principal_name=principal.get("displayName", ""),
                    role=ra.get("role", ""),
                    access_status="ok",
                )

                if principal.get("type") == "Group":
                    group_name = principal.get("displayName") or principal.get("id") or "?"
                    for member in expand_group_members(principal["id"], graph_client=gc):
                        yield RbacRow(
                            layer="workspace",
                            resource_id=ws_id,
                            resource_name=ws_name,
                            principal_id=member.get("id", ""),
                            principal_type=_member_type(member),
                            principal_name=member.get("displayName", ""),
                            role=ra.get("role", ""),
                            access_status=f"via-group:{group_name}",
                        )
        except HttpError as e:
            # SPN can list a workspace but lack a role on it -> 401/403 on
            # /roleAssignments. Emit a placeholder row so auditors see the
            # gap instead of aborting the whole audit. Same shape as the
            # capacity-layer admin-only handler below.
            if e.status_code in (401, 403):
                yield RbacRow(
                    layer="workspace",
                    resource_id=ws_id,
                    resource_name=ws_name,
                    principal_id="",
                    principal_type="",
                    principal_name="",
                    role="",
                    access_status="forbidden-admin-only",
                )
            else:
                raise

        # item-layer placeholder per workspace (A5 / Pitfall 12)
        yield RbacRow(
            layer="item",
            resource_id=ws_id,
            resource_name=ws_name,
            principal_id="",
            principal_type="",
            principal_name="",
            role="",
            access_status="not-accessible-via-rest",
        )

    # ---- capacity layer ---------------------------------------------
    capacities: Iterable[dict[str, Any]]
    if workspace_ids is None:
        capacities = fabric.list_paginated("/v1/capacities")
    else:
        capacities = _scoped_capacities(fabric, scoped_capacity_ids)

    for cap in capacities:
        cap_id = cap["id"]
        cap_name = cap.get("displayName", "")
        try:
            resp = powerbi.send("GET", f"/v1.0/myorg/admin/capacities/{cap_id}/users")
        except HttpError as e:
            # SPNs without Fabric/Power BI admin role return 401; tenant-scoped
            # admin-only refusal returns 403. Both map to the same operator
            # signal: "you can't read this without admin." Treat them together.
            if e.status_code in (401, 403):
                yield RbacRow(
                    layer="capacity",
                    resource_id=cap_id,
                    resource_name=cap_name,
                    principal_id="",
                    principal_type="",
                    principal_name="",
                    role="",
                    access_status="forbidden-admin-only",
                )
                continue
            raise

        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        for u in body.get("value", []):
            yield RbacRow(
                layer="capacity",
                resource_id=cap_id,
                resource_name=cap_name,
                principal_id=u.get("graphId") or u.get("identifier", ""),
                principal_type=u.get("principalType", ""),
                principal_name=u.get("displayName", ""),
                role=u.get("capacityUserAccessRight", ""),
                access_status="ok",
            )


def _resolve_scoped_workspaces(
    fabric: FabricRestClient,
    workspace_ids: Sequence[str],
    capacity_ids_out: list[str],
) -> Iterator[dict[str, Any]]:
    """Resolve explicit workspace ids via ``GET /v1/workspaces/{id}``.

    - 404 raises :class:`ValueError`: an audit scoped to a workspace that
      does not exist must fail loudly (most likely a typo'd id), never
      silently produce an empty-but-green report.
    - 401/403 yields a name-less stub; the role-assignment call downstream
      then emits the standard ``forbidden-admin-only`` placeholder row.
    - ``capacityId`` values seen on reachable workspaces accumulate into
      ``capacity_ids_out`` (deduplicated, in resolution order) so the
      capacity layer can scope to them.
    - Duplicate ids are resolved once.
    """
    seen: set[str] = set()
    for wid in workspace_ids:
        if wid in seen:
            continue
        seen.add(wid)
        try:
            resp = fabric.send("GET", f"/v1/workspaces/{wid}")
        except HttpError as e:
            if e.status_code == 404:
                raise ValueError(
                    f"workspace {wid!r} not found (404) -- check the --workspace-id value"
                ) from e
            if e.status_code in (401, 403):
                yield {"id": wid, "displayName": ""}
                continue
            raise
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        cap_id = body.get("capacityId")
        if cap_id and cap_id not in capacity_ids_out:
            capacity_ids_out.append(cap_id)
        yield {"id": wid, "displayName": body.get("displayName", "")}


def _scoped_capacities(
    fabric: FabricRestClient,
    capacity_ids: Sequence[str],
) -> Iterator[dict[str, Any]]:
    """Yield a capacity stub per scoped id, with names from the listing.

    Every scoped id is yielded even when absent from (or denied in) the
    tenant capacity listing -- the per-capacity users call downstream
    produces the truthful row (``ok`` rows or the ``forbidden-admin-only``
    placeholder). Name lookup is case-insensitive because Fabric returns
    capacity GUIDs in different cases on different endpoints.
    """
    if not capacity_ids:
        return
    names: dict[str, str] = {}
    try:
        names = {
            str(c.get("id", "")).lower(): c.get("displayName", "")
            for c in fabric.list_paginated("/v1/capacities")
        }
    except HttpError as e:
        if e.status_code not in (401, 403):
            raise
    for cid in capacity_ids:
        yield {"id": cid, "displayName": names.get(cid.lower(), "")}


def _member_type(member: dict[str, Any]) -> str:
    """Map Graph ``@odata.type`` to our principal_type vocabulary."""
    odata = str(member.get("@odata.type", ""))
    if odata.endswith(".user"):
        return "User"
    if odata.endswith(".group"):
        return "Group"
    if odata.endswith(".servicePrincipal"):
        return "ServicePrincipal"
    if odata:
        # Fallback: take the suffix and capitalize.
        return odata.split(".")[-1].capitalize()
    return "User"


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def write_csv(rows: Iterable[RbacRow], out: TextIO) -> None:
    """Stream CSV: header + one row per ``RbacRow``.

    Uses ``csv.writer`` default dialect, which quotes fields containing
    commas, double-quotes, or newlines. Spreadsheet-formula injection
    (e.g. fields beginning with ``=``, ``+``, ``-``, ``@``) is a consumer
    concern; audit consumers are auditors using ``jq`` / ``awk`` /
    Excel with formula-evaluation disabled (T-3-05b accepted; revisit if
    auditor tooling changes).
    """
    w = csv.writer(out)
    w.writerow(_CSV_HEADER)
    for r in rows:
        w.writerow(
            [
                r.layer,
                r.resource_id,
                r.resource_name,
                r.principal_id,
                r.principal_type,
                r.principal_name,
                r.role,
                r.access_status,
            ]
        )
