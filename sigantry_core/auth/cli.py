"""Typer app exposed as the `diagnose-auth` console script.

Invocation paths:
- `diagnose-auth` (post `pip install`)
- `python -m sigantry_core.auth.cli` (any env where scripts aren't on PATH)
- `typer.testing.CliRunner` in tests

Exit codes:
- 0 = healthy
- 2 = degraded (token works but the tenant toggle is missing, or the Entra
      group check did not pass); Click also exits 2 on a usage error
      (unknown `--scope`, malformed arguments)
- 3 = broken (no credential returned a token)
- 4 = invalid `--output` value

The expected Entra group comes from `--expected-group`, else from
`auth.expected_group` in the settings (`SIGANTRY_AUTH__EXPECTED_GROUP`, or
`[auth] expected_group` in `.sigantry.toml`). With none set, the group check
is reported as `skipped` and does not change the exit code. If the settings
cannot be loaded and no `--expected-group` is given, the group check is
reported as `error` (exit 2): a configured check is never skipped silently.

Never logs the raw token. JSON output schema does not include a `token` key.
"""

from __future__ import annotations

import json
import logging
import tomllib
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from sigantry_core.auth.audiences import (
    AZURE_DEVOPS_SCOPE,
    AZURE_RM_SCOPE,
    FABRIC_SCOPE,
    GRAPH_SCOPE,
    POWERBI_SCOPE,
    PURVIEW_SCOPE,
)
from sigantry_core.auth.diagnose import (
    build_report,
    check_entra_group,
    check_tenant_toggles,
    decode_token_claims,
)
from sigantry_core.auth.errors import TokenAcquisitionError
from sigantry_core.auth.token_provider import TokenProvider
from sigantry_core.config import load_settings

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Diagnose Fabric authentication from the current runtime. "
    "Read-only. Never logs raw tokens."
)

console = Console()

_SCOPE_ALIASES = {
    "fabric": FABRIC_SCOPE,
    "powerbi": POWERBI_SCOPE,
    "graph": GRAPH_SCOPE,
    "purview": PURVIEW_SCOPE,
    "azurerm": AZURE_RM_SCOPE,
    "azuredevops": AZURE_DEVOPS_SCOPE,
}


def _resolve_scope(raw: str) -> str:
    low = raw.lower()
    if low in _SCOPE_ALIASES:
        return _SCOPE_ALIASES[low]
    if raw.endswith("/.default"):
        return raw
    raise typer.BadParameter(
        f"unknown scope {raw!r}. Use one of: {', '.join(_SCOPE_ALIASES)} "
        f"or a full scope ending in '/.default'."
    )


def _resolve_expected_group(flag: str | None) -> tuple[str | None, str | None]:
    """Return ``(group, settings_error)`` for the Entra group check.

    ``--expected-group`` wins and needs no settings; otherwise
    ``auth.expected_group`` from :func:`sigantry_core.config.load_settings`
    (``SIGANTRY_AUTH__EXPECTED_GROUP`` over ``[auth] expected_group`` in
    ``.sigantry.toml``). A blank value counts as unset, and ``(None, None)``
    means the check is skipped. If the settings cannot be loaded, the group is
    unknown rather than unset -- an env value is lost when the file fails to
    parse -- so ``settings_error`` describes the failure and the caller reports
    the check as an error instead of skipping it.
    """
    if flag is not None and flag.strip():
        return flag.strip(), None
    try:
        settings = load_settings()
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        ValidationError,
        SettingsError,
    ) as exc:
        logger.warning(
            "Could not load Sigantry settings (%s: %s); the expected Entra group "
            "is unknown, so the group check is reported as an error.",
            type(exc).__name__,
            exc,
        )
        return None, (
            f"not checked: settings could not be loaded ({type(exc).__name__}); "
            "pass --expected-group or fix the settings file"
        )
    configured = settings.auth.expected_group
    if configured is None or not configured.strip():
        return None, None
    return configured.strip(), None


@app.command()
def diagnose(
    scope: Annotated[str, typer.Option(help="Scope alias or full /.default scope")] = "fabric",
    tenant_id: Annotated[
        str | None,
        typer.Option("--tenant-id", help="Expected tenant id (warns on mismatch)"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan without hitting Azure")
    ] = False,
    output: Annotated[str, typer.Option("--output", help="table or json")] = "table",
    principal_id: Annotated[
        str | None,
        typer.Option(
            "--principal-id",
            help="Object id of the SP to look up in Graph (omit for /me path)",
        ),
    ] = None,
    expected_group: Annotated[
        str | None,
        typer.Option(
            "--expected-group",
            help=(
                "Expected Entra group display name, e.g. fabric-deployers. "
                "Default: auth.expected_group from .sigantry.toml or "
                "SIGANTRY_AUTH__EXPECTED_GROUP. When none is set, the group "
                "check is reported as skipped."
            ),
        ),
    ] = None,
) -> None:
    """Diagnose token acquisition and tenant-setting visibility.

    The Entra group check runs only when an expected group is set.
    """
    if output not in ("table", "json"):
        console.print(f"[red]--output must be 'table' or 'json' (got {escape(repr(output))})[/red]")
        raise typer.Exit(code=4)

    resolved_scope = _resolve_scope(scope)

    if dry_run:
        plan = {
            "mode": "dry-run",
            "scope": resolved_scope,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "output": output,
            "will_acquire_token": False,
            "will_probe_fabric_admin": False,
            "will_probe_graph": False,
        }
        if output == "json":
            console.print(json.dumps(plan, indent=2), markup=False, highlight=False, soft_wrap=True)
        else:
            tbl = Table(title="diagnose-auth (dry-run)")
            tbl.add_column("key")
            tbl.add_column("value")
            for k, v in plan.items():
                tbl.add_row(escape(str(k)), escape(str(v)))
            console.print(tbl)
        raise typer.Exit(code=0)

    # --- Live mode ---
    provider = TokenProvider(tenant_id=tenant_id)
    try:
        token = provider.get_token(resolved_scope)
    except TokenAcquisitionError as e:
        report = build_report(
            scope=resolved_scope,
            credential_used=e.credential_used,
            token=None,
            tenant_toggles=None,
            entra_groups=None,
        )
        _emit(output, report, error=str(e))
        raise typer.Exit(code=3) from e

    credential_used = provider.last_credential_class(resolved_scope)

    # Only probe Fabric admin + Graph when scope is Fabric.
    tenant_toggles = None
    entra_groups: dict[str, Any] | None = None
    if resolved_scope == FABRIC_SCOPE:
        tenant_toggles = check_tenant_toggles(token)
        group, settings_error = _resolve_expected_group(expected_group)
        if settings_error is not None:
            entra_groups = {
                "status": "error",
                "groups": [],
                "expected": None,
                "detail": settings_error,
            }
        else:
            entra_groups = check_entra_group(
                token,
                principal_id=principal_id,
                expected_group=group,
            )

    # Tenant sanity check (Pitfall P1-6)
    claims = decode_token_claims(token)
    if tenant_id and claims.get("tid") and claims["tid"] != tenant_id:
        console.print(
            f"[yellow]WARNING[/yellow]: token tid {escape(repr(claims['tid']))} "
            f"does not match expected {escape(repr(tenant_id))}"
        )

    report = build_report(
        scope=resolved_scope,
        credential_used=credential_used,
        token=token,
        tenant_toggles=tenant_toggles,
        entra_groups=entra_groups,
    )
    _emit(output, report)
    raise typer.Exit(code=report["exit_code"])


def _emit(output: str, report: dict, *, error: str | None = None) -> None:
    """Render the report. NEVER include the raw token in output."""
    # Defensive: strip any 'token' key that might have leaked into the dict.
    report.pop("token", None)

    if output == "json":
        payload = dict(report)
        if error:
            payload["error"] = error
        console.print(json.dumps(payload, indent=2), markup=False, highlight=False, soft_wrap=True)
        return

    tbl = Table(title="diagnose-auth")
    tbl.add_column("Check")
    tbl.add_column("Status")
    tbl.add_column("Detail")

    tbl.add_row("credential_used", "-", escape(str(report.get("credential_used") or "unknown")))
    tbl.add_row("scope", "-", escape(str(report["scope"])))
    claims = report.get("token_claims") or {}
    for key in ("aud", "tid", "oid", "appid", "exp"):
        if key in claims:
            tbl.add_row(f"claim.{key}", "-", escape(str(claims[key])))

    toggles = report.get("tenant_toggles")
    if toggles:
        tbl.add_row(
            "tenant_toggles",
            escape(str(toggles.get("status", "?"))),
            escape(str(toggles.get("detail", ""))),
        )
    groups = report.get("entra_groups")
    if groups:
        tbl.add_row(
            "entra_groups",
            escape(str(groups.get("status", "?"))),
            escape(str(groups.get("detail", ""))),
        )
    if error:
        tbl.add_row("error", "broken", escape(error))
    tbl.add_row("exit_code", str(report["exit_code"]), "")

    console.print(tbl)


if __name__ == "__main__":  # pragma: no cover
    app()
