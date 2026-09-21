"""Top-level fabric-dataops CLI (console script: `fabric-dataops`).

Phase 1 shipped the empty Typer app; Phase 3 Plan 03-01 registered
``workspace``; Plan 03-02 added ``capacity``; Plan 03-03 added ``label-sync``
and ``rbac-audit``; Plan 03-04 appended ``tenant-settings``; Phase 4 Plan
04-01 appended ``deploy`` (sixth subapp); Plan 04-02 appended ``fabric-item``
(seventh subapp, DEPLOY-02); Phase 4 Plan 04-03 appends ``git``,
``variable-library``, and ``env`` (eighth, ninth, tenth subapps --
DEPLOY-05 + DEPLOY-06 + Pitfall 6 primitive). Phase 7 Plan 07-01 appends
``dq`` (INTEG-02) as the 11th top-level subapp. Phase 8 Plan 08-06 appends
``doctor`` (PROD-18) as the 12th top-level subapp for plugin discovery
diagnostics. Phase 11 Plan 11-06 appends ``release`` (TRACE-05) as the
13th top-level subapp -- the operator-facing surface of the
work-item-traceability wedge (``sigantry release record``).

Phase 13 Plan 13-04 appends ``sync`` (SYNC-04 / INTROSPECT-01) as the
14th top-level subapp -- ``sigantry sync apply`` / ``snapshot`` / ``pull``
(pull body lands in Plan 13-05). Phase 13 Plan 13-06 appends ``diff``
(DRIFT-01..02 / D-04) as the 15th top-level subapp -- ``sigantry diff
<env> --workspace-id ... --manifest sync.yml [--output json|human]
[--fail-on-drift]``. Drift gets its own top-level subapp (NOT under
``sync``) per the Phase 12 ``sigantry release diff`` symmetry pattern.

Phase 14 Plan 14-01 appends ``config`` (D-16 / RESEARCH section
"Pattern 5") as the 16th top-level subapp -- ``sigantry config validate
<file>`` wraps :func:`sigantry_core.deploy.parameters.load_and_validate`.

Phase 14 Plan 14-06 appends ``pr-bot`` (D-04 / D-07 / RESEARCH §Pattern
6) as the 17th top-level subapp -- ``sigantry pr-bot run`` is the
PR-review bot's CLI entry point: auto-detects CI provider from env
(``GITHUB_ACTIONS=true`` -> github / ``TF_BUILD=True`` -> ado),
diffs TMDL + Lakehouse metadata, and posts a byte-identical comment
through :class:`GithubProvider` or :class:`AdoProvider`.
"""

from __future__ import annotations

import typer

from sigantry_core.capacity.cli import capacity_app
from sigantry_core.config_cli import config_app
from sigantry_core.deploy.cli import (
    deploy_app,
    env_app,
    fabric_item_app,
    git_app,
    variable_library_app,
)
from sigantry_core.diff_cli import diff_app
from sigantry_core.doctor import doctor_app
from sigantry_core.dq.cli import dq_app
from sigantry_core.governance.cli import (
    label_sync_app,
    rbac_audit_app,
    tenant_settings_app,
)
from sigantry_core.pr_bot.cli import pr_bot_app
from sigantry_core.preflight.cli import preflight_app
from sigantry_core.release.cli import release_app
from sigantry_core.sync.cli import sync_app
from sigantry_core.workspace.cli import workspace_app

app = typer.Typer(
    help="Sigantry. Control-plane CRUD + governance for Microsoft Fabric.",
    no_args_is_help=True,
)
app.add_typer(workspace_app, name="workspace")
app.add_typer(capacity_app, name="capacity")
app.add_typer(label_sync_app, name="label-sync")
app.add_typer(rbac_audit_app, name="rbac-audit")
app.add_typer(tenant_settings_app, name="tenant-settings")
app.add_typer(deploy_app, name="deploy")
app.add_typer(fabric_item_app, name="fabric-item")
app.add_typer(git_app, name="git")
app.add_typer(variable_library_app, name="variable-library")
app.add_typer(env_app, name="env")
app.add_typer(dq_app, name="dq")
app.add_typer(doctor_app, name="doctor")
app.add_typer(release_app, name="release")
app.add_typer(sync_app, name="sync")
app.add_typer(diff_app, name="diff")
app.add_typer(config_app, name="config")
app.add_typer(pr_bot_app, name="pr-bot")
app.add_typer(preflight_app, name="preflight")


if __name__ == "__main__":
    app()
