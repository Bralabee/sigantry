"""``fabric-dataops label-sync`` + ``rbac-audit`` + ``tenant-settings`` Typer subcommands.

Plan 03-03 registers ``label_sync_app`` and ``rbac_audit_app``. Plan 03-04
appends ``tenant_settings_app`` (GOV-05) wired onto the same root CLI via
``sigantry_core/cli.py``.

CLI conventions (carried from Plan 03-01 / 03-02):
- Default output mode is opt-in by command (``label-sync`` defaults to
  JSON; ``rbac-audit`` defaults to CSV) since auditors want CSV out of
  the box and label-sync output is more readable as JSON.
- ``--tenant-id`` threads through to ``get_token_provider`` so multi-tenant
  callers pin the credential chain (Pitfall P1-6).
- SP detection probes ``TokenProvider.last_credential_class(FABRIC_SCOPE)``;
  ``--sp`` / ``--user`` overrides the auto-detection.
"""

from __future__ import annotations

import csv as _csv
import json as _json
import sys
from typing import Any

import typer
from rich.console import Console

from sigantry_core.auth import FABRIC_SCOPE, get_token_provider
from sigantry_core.client import FabricRestClient, PowerBIRestClient
from sigantry_core.governance.labels import apply_label_to_workspace_items
from sigantry_core.governance.rbac import audit, write_csv

label_sync_app = typer.Typer(
    help="Apply a sensitivity label to every item in a workspace (GOV-02).",
    no_args_is_help=False,
    invoke_without_command=True,
)
rbac_audit_app = typer.Typer(
    help="Emit workspace + capacity + item RBAC audit (GOV-04).",
    no_args_is_help=False,
    invoke_without_command=True,
)
tenant_settings_app = typer.Typer(
    help="Export Fabric admin tenant-settings baseline (GOV-05).",
    no_args_is_help=True,
)
_console = Console()

# Credential class names that imply Service Principal authentication.
# WorkloadIdentityCredential covers Azure DevOps WIF (PREREQ-04 / 05).
_SP_CREDENTIAL_CLASSES: frozenset[str] = frozenset(
    {
        "ClientSecretCredential",
        "CertificateCredential",
        "ManagedIdentityCredential",
        "WorkloadIdentityCredential",
    }
)


def _detect_sp(tenant_id: str | None) -> bool:
    """Probe ``TokenProvider`` for the credential class used at FABRIC_SCOPE.

    Returns ``True`` if the credential class implies Service Principal /
    workload-identity authentication. Returns ``False`` on any error so
    label-sync defaults to the more-permissive (Fabric admin) path; the
    Fabric admin call will then surface ``Failed`` if the SP truly lacks
    rights — visible to the user without surprising silent skips.
    """
    try:
        tp = get_token_provider(tenant_id=tenant_id)
        cls = tp.last_credential_class(FABRIC_SCOPE)
    except Exception:
        cls = None
    return bool(cls and cls in _SP_CREDENTIAL_CLASSES)


@label_sync_app.callback()
def label_sync(
    workspace_id: str = typer.Option(..., "--workspace-id", help="Workspace id (UUID)."),
    label_id: str = typer.Option(..., "--label-id", help="Sensitivity label id (GUID)."),
    tenant_id: str = typer.Option(None, "--tenant-id"),
    sp: bool = typer.Option(
        None,
        "--sp/--user",
        help="Override Service-Principal detection. Default: auto-detect from TokenProvider.",
    ),
    output: str = typer.Option("json", "--output", "-o", help="json|csv"),
) -> None:
    """Apply ``--label-id`` to every item in ``--workspace-id``.

    Per-item iteration; never the cascade (Pitfall 10). SP-only items
    surface as ``SP_NotSupported`` (Pitfall 4); 2000-item bulk batching
    (Pitfall 9).

    Note: ``workspace_id`` is exposed as an Option (``--workspace-id``)
    rather than a positional Argument because Typer's nested-group parser
    treats positional arguments after a sub-app name as the start of a
    sub-command, which prevents subsequent options from being parsed
    correctly. Documented as Rule 1 deviation in SUMMARY.
    """
    running_as_sp = sp if sp is not None else _detect_sp(tenant_id)

    with (
        FabricRestClient.from_defaults(tenant_id=tenant_id) as fabric,
        PowerBIRestClient.from_defaults(tenant_id=tenant_id) as powerbi,
    ):
        outcomes = list(
            apply_label_to_workspace_items(
                fabric, powerbi, workspace_id, label_id, running_as_sp=running_as_sp
            )
        )

    if output == "csv":
        w = _csv.writer(sys.stdout)
        w.writerow(["itemId", "itemType", "status"])
        for o in outcomes:
            w.writerow([o.item_id, o.item_type, o.status])
    else:
        rows = [
            {"itemId": o.item_id, "itemType": o.item_type, "status": o.status} for o in outcomes
        ]
        _console.print_json(data=rows)


def _rbac_row_dict(r: Any) -> dict[str, Any]:
    """JSON shape shared by the stdout and file writers (camelCase keys)."""
    return {
        "layer": r.layer,
        "resourceId": r.resource_id,
        "resourceName": r.resource_name,
        "principalId": r.principal_id,
        "principalType": r.principal_type,
        "principalName": r.principal_name,
        "role": r.role,
        "accessStatus": r.access_status,
    }


@rbac_audit_app.callback()
def rbac_audit(
    tenant_id: str = typer.Option(None, "--tenant-id"),
    output: str = typer.Option("csv", "--output", "-o", help="csv|json"),
    workspace_id: list[str] = typer.Option(  # noqa: B008 -- typer convention: Option() lives in the default
        None,
        "--workspace-id",
        "-w",
        help=(
            "Scope the audit to this workspace id (repeatable for several). "
            "Default: the entire tenant."
        ),
    ),
    out: str = typer.Option(
        None,
        "--out",
        help="Write the audit to this file instead of stdout (parent dirs created).",
    ),
    out_dir: str = typer.Option(
        None,
        "--out-dir",
        help=(
            "Write the audit to a UTC-dated file in this directory: "
            "rbac-audit-<YYYYMMDDTHHMMSSZ>.<csv|json> (directory created). "
            "Mutually exclusive with --out."
        ),
    ),
) -> None:
    """Emit the three-layer RBAC audit (workspace + capacity + item).

    Default output is CSV (auditor-friendly). JSON output is opt-in.
    Capacity 403 → ``access_status="forbidden-admin-only"`` row;
    item layer → ``access_status="not-accessible-via-rest"`` placeholder
    per workspace (A5 / Pitfall 12).

    ``--workspace-id`` (repeatable) scopes the audit; the capacity layer
    then narrows to the capacities those workspaces are assigned to. With
    ``--out`` / ``--out-dir`` the rows go to a file and the console gets a
    one-line summary instead of the full dump -- the dated ``--out-dir``
    form is the recommended shape for audits that are filed and acted upon
    later (diffable across runs).
    """
    if out and out_dir:
        raise typer.BadParameter("--out and --out-dir are mutually exclusive")
    scope_ids = list(workspace_id) if workspace_id else None

    with (
        FabricRestClient.from_defaults(tenant_id=tenant_id) as fabric,
        PowerBIRestClient.from_defaults(tenant_id=tenant_id) as powerbi,
    ):
        try:
            # Materialise before any file is created: a typo'd workspace id
            # (ValueError from the 404 resolve) must not leave a partial file.
            rows = list(audit(fabric, powerbi, workspace_ids=scope_ids))
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e

    if out or out_dir:
        from datetime import UTC, datetime
        from pathlib import Path

        ext = "csv" if output == "csv" else "json"
        if out_dir:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            path = Path(out_dir) / f"rbac-audit-{stamp}.{ext}"
        else:
            path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        if output == "csv":
            with path.open("w", newline="", encoding="utf-8") as fh:
                write_csv(rows, fh)
        else:
            with path.open("w", encoding="utf-8") as fh:
                _json.dump([_rbac_row_dict(r) for r in rows], fh, indent=2)
                fh.write("\n")
        scope = f"{len(scope_ids)} workspace(s)" if scope_ids else "tenant-wide"
        _console.print(f"wrote {path} (rows={len(rows)}, scope={scope})")
    elif output == "csv":
        write_csv(rows, sys.stdout)
    else:
        _console.print_json(data=[_rbac_row_dict(r) for r in rows])


@tenant_settings_app.command("export")
def tenant_settings_export(
    output: str = typer.Option(
        None, "--output", help="File path to write baseline (default: stdout)."
    ),
    tenant_id: str = typer.Option(
        None, "--tenant-id", help="Optional AAD tenant id pinning the credential chain."
    ),
) -> None:
    """Export Fabric admin tenant-settings baseline (GOV-05).

    Calls ``GET /v1/admin/tenantsettings`` via the Phase 2 client, sorts the
    response by ``settingName``, computes a SHA-256 digest over the canonical
    JSON of the sorted list, and emits a ``{capturedAt, tenantId, digest,
    settings}`` envelope. With ``--output <path>``, writes canonical JSON to
    the file (parent dirs created on demand); otherwise prints the envelope
    to stdout via ``rich.Console.print_json``.
    """
    from pathlib import Path

    from sigantry_core.governance.tenant_settings import (
        TenantSettingBaseline,
        export_baseline,
        write_baseline,
    )

    with FabricRestClient.from_defaults(tenant_id=tenant_id) as fabric:
        envelope: TenantSettingBaseline = export_baseline(fabric, tenant_id=tenant_id or "")
    payload = envelope.to_dict()
    if output:
        write_baseline(envelope, Path(output))
        _console.print(
            f"wrote {output} (settings={len(payload['settings'])}, digest={payload['digest']})"
        )
    else:
        _console.print_json(data=payload)
