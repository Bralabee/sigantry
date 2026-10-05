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

The Entra group check runs only for the Fabric scope (the default,
`--scope fabric`). The expected group comes from `--expected-group`, else from
`auth.expected_group` in the settings (`SIGANTRY_AUTH__EXPECTED_GROUP`, or
`[auth] expected_group` in `.sigantry.toml`). `skipped` means neither named a
group; it does not change the exit code, and the skip writes nothing to
stderr. If the settings cannot be loaded and no `--expected-group` is given,
the group check is reported as `error` (exit 2): a configured check is never
skipped silently. Warnings from the settings loader are recorded and dropped,
so they neither raise under `PYTHONWARNINGS=error` nor reach stderr (see
`sigantry_core._cli_settings`).

Microsoft Graph receives only a second token, which the command requests from
the same credential for `https://graph.microsoft.com/.default`, and only for a
group check that has a group to check; the token for `--scope` is sent only to
the Fabric probe. A group check reported as `error` carries a `classification`
naming what stopped it, such as `token_unavailable` (no Graph token),
`token_rejected` (a 401 from Graph) or `permission_denied` (a 403), and makes
the exit code 2. Listing the principal's memberships takes a Graph permission:
for a user, at least delegated `User.Read`; for a service principal
(`--principal-id`), at least `Application.Read.All`. The classification
`names_hidden` means some memberships came back without their names and none
of the named ones is the group; its detail asks for `GroupMember.Read.All`.

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
from rich.table import Table
from rich.text import Text

from sigantry_core._cli_settings import load_settings_for_cli
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
    group_check_result,
)
from sigantry_core.auth.errors import TokenAcquisitionError
from sigantry_core.auth.token_provider import TokenProvider

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Diagnose Fabric authentication from the current runtime. "
    "Read-only. Never logs raw tokens."
)

console = Console()

#: ``entra_groups.detail`` of a skipped group check. It goes in the report,
#: never to stderr.
_SKIPPED_DETAIL = (
    "not checked: neither --expected-group nor the loaded settings named a group. "
    "sigantry 1.0.0 checked a built-in group name"
)

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
    ``.sigantry.toml``), loaded with its warnings recorded and dropped
    (:func:`sigantry_core._cli_settings.load_settings_for_cli`). A blank value
    counts as unset, and ``(None, None)`` means the check is skipped. If the
    settings cannot be loaded, the group is unknown rather than unset -- an env
    value is lost when the file fails to parse -- so ``settings_error``
    describes the failure and the caller reports the check as an error instead
    of skipping it.
    """
    if flag is not None and flag.strip():
        return flag.strip(), None
    try:
        settings = load_settings_for_cli()
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


def _check_group_with_graph_token(
    provider: TokenProvider, group: str, principal_id: str | None
) -> dict[str, Any]:
    """Run the group check with a Microsoft Graph token from the same credential.

    The token passed in for the Fabric probe is never sent to Graph: Graph
    accepts only a token issued for its own audience. If no Graph token can
    be acquired, the check is an error (the membership is unknown), not a
    missing membership; the Fabric result and the exit code 3 contract are
    unaffected, since a Fabric token was acquired.
    """
    try:
        graph_token = provider.get_token(GRAPH_SCOPE)
    except TokenAcquisitionError as exc:
        logger.warning("Could not acquire a Microsoft Graph token for the group check: %s", exc)
        return group_check_result(
            "error",
            "token_unavailable",
            expected=group,
            detail=(
                f"not checked: no Microsoft Graph token ({GRAPH_SCOPE}) could be acquired "
                "from the credential; the membership is unknown"
            ),
        )
    return check_entra_group(graph_token, principal_id=principal_id, expected_group=group)


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
                "Default: auth.expected_group in the loaded settings. Only a "
                "--scope fabric run checks a group; skipped means neither this "
                "flag nor the loaded settings named one."
            ),
        ),
    ] = None,
) -> None:
    """Diagnose token acquisition and tenant-setting visibility.

    The Entra group check runs only when an expected group is set.
    """
    if output not in ("table", "json"):
        console.print(Text(f"--output must be 'table' or 'json' (got {output!r})", style="red"))
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
            typer.echo(json.dumps(plan, indent=2))
        else:
            tbl = Table(title="diagnose-auth (dry-run)")
            tbl.add_column("key")
            tbl.add_column("value")
            for k, v in plan.items():
                tbl.add_row(Text(str(k)), Text(str(v)))
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
            entra_groups = group_check_result(
                "error", "settings_unreadable", expected=None, detail=settings_error
            )
        elif group is None:
            # Built here, not by check_entra_group(), which emits a
            # FutureWarning for library callers.
            entra_groups = group_check_result(
                "skipped", "skipped", expected=None, detail=_SKIPPED_DETAIL
            )
        else:
            entra_groups = _check_group_with_graph_token(provider, group, principal_id)

    # Tenant sanity check (Pitfall P1-6)
    claims = decode_token_claims(token)
    if tenant_id and claims.get("tid") and claims["tid"] != tenant_id:
        console.print(
            Text.assemble(
                ("WARNING", "yellow"),
                f": token tid {claims['tid']!r} does not match expected {tenant_id!r}",
            )
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
        typer.echo(json.dumps(payload, indent=2))
        return

    tbl = Table(title="diagnose-auth")
    tbl.add_column("Check")
    tbl.add_column("Status")
    tbl.add_column("Detail")

    tbl.add_row("credential_used", "-", Text(str(report.get("credential_used") or "unknown")))
    tbl.add_row("scope", "-", Text(str(report["scope"])))
    claims = report.get("token_claims") or {}
    for key in ("aud", "tid", "oid", "appid", "exp"):
        if key in claims:
            tbl.add_row(f"claim.{key}", "-", Text(str(claims[key])))

    toggles = report.get("tenant_toggles")
    if toggles:
        tbl.add_row(
            "tenant_toggles",
            Text(str(toggles.get("status", "?"))),
            Text(str(toggles.get("detail", ""))),
        )
    groups = report.get("entra_groups")
    if groups:
        tbl.add_row(
            "entra_groups",
            Text(str(groups.get("status", "?"))),
            Text(str(groups.get("detail", ""))),
        )
    if error:
        tbl.add_row("error", "broken", Text(error))
    tbl.add_row("exit_code", str(report["exit_code"]), "")

    console.print(tbl)


if __name__ == "__main__":  # pragma: no cover
    app()
