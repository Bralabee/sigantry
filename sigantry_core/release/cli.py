"""Typer subapp ``sigantry release`` -- record / list / show / diff / verify.

The ``record`` subcommand (Plan 11-06, TRACE-05) is the operator-facing
surface of the work-item-traceability wedge:

1. Build a ``DeployRecord(...).with_hash()`` from CLI flags (release id,
   workspace, work-item ids, fabric items, test evidence, approver).
2. Persist via the non-pluggable observation plane
   (``sigantry_core.governance.audit.emit_deploy_record``) so the audit
   jsonl line lands AND the structured ``deploy_record`` log event fires
   regardless of any plugin telemetry sink.
3. Resolve the configured :class:`WorkItemProvider` (ADO or GitHub) from
   the ``--provider`` flag plus the provider-specific construction args
   and call ``provider.link_release(release_id, ids, deploy_record)`` to
   post structured comments on every linked work-item.

The ``list`` / ``show`` / ``diff`` subcommands (Plan 12-03, PIPELINE-04)
are thin wrappers over :mod:`sigantry_core.release.ledger`. They are
read-only over ``~/.sigantry/audit/deploys.jsonl`` and never construct
a :class:`WorkItemProvider`.

The ``verify`` subcommand exposes
:func:`sigantry_core.governance.audit_io.verify_audit_chain` so the
ledger's integrity can be checked by the operator who needs it,
rather than only by a caller willing to import the library. It reads
lines in FILE order via its own strict parser -- see
``_read_ledger_lines_strict`` for why it must not reuse ``iter_records``.

The ``release diff --json`` schema is SemVer-committed -- the keyset
``{release_a, release_b, added, removed, unchanged}`` with per-entry
``{logical_name, item_type, fabric_item_id}`` is asserted at the full
keyset (not subset) by ``tests/release/test_release_diff_cli.py::
test_diff_json_schema``. Adding a new field requires a SemVer-minor
bump. Phase 13's drift JSON reuses the same shape.

The subapp is registered as the 13th top-level subapp on the root
``sigantry`` Typer app via ``sigantry_core.cli.app.add_typer``.

CLI design notes:

- ``--provider`` is mandatory (Typer ``...``); missing or invalid values
  exit 2 (Click standard for usage errors).
- ``--audit-dir`` overrides the default ``~/.sigantry/audit/`` directory
  for hermetic tests (``tmp_path``) and CI runners that need an explicit
  per-run audit location.
- Provider construction is lazy and import-late: the ADO and GitHub
  provider modules are imported only when their flag-set is selected.
  This keeps a ``sigantry release record --provider github`` invocation
  from importing ADO machinery (and vice-versa).
- ``--test-evidence`` is JSON-decoded and rejected if not a dict; the
  subapp uses ``typer.BadParameter`` so the failure surfaces as a clean
  exit 2 with the parser hint.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from sigantry_core._cli_settings import load_settings_for_cli
from sigantry_core._cli_tenant import resolve_tenant_id, stops_on_tenant_error
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.governance.audit_io import (
    _DEFAULT_AUDIT_DIR,
    resolve_audit_dir,
    verify_audit_chain,
)
from sigantry_core.release.ledger import (
    diff_records,
    find_by_release_id,
    iter_records,
)
from sigantry_core.release.record import DeployRecord

release_app = typer.Typer(
    help=(
        "Sigantry release records: work-item links and an integrity-checked, unkeyed deploy ledger."
    ),
    no_args_is_help=True,
)
_console = Console()


def _split_csv(value: str | None) -> list[str]:
    """Split a comma-separated string, dropping empty fragments + trimming whitespace."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_test_evidence(value: str | None) -> dict[str, str]:
    """Parse the ``--test-evidence`` flag as JSON; require a dict body.

    Empty / unset returns an empty dict. Non-JSON or non-dict values raise
    :class:`typer.BadParameter` so Typer surfaces a clean exit 2 with the
    parser hint.
    """
    if not value:
        return {}
    try:
        parsed = _json.loads(value)
    except _json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--test-evidence must be valid JSON; got: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--test-evidence must be a JSON object (dict[str, str]).")
    return {str(k): str(v) for k, v in parsed.items()}


def _build_provider(
    provider_kind: str | None,
    *,
    ado_organization: str | None,
    ado_project: str | None,
    ado_tenant_id: str | None,
    github_owner: str | None,
    github_repo: str | None,
    github_pat: str | None,
    github_app_id: str | None,
    github_private_key_pem: str | None,
    github_installation_id: str | None,
) -> Any:
    """Resolve a :class:`WorkItemProvider` from the CLI flag-set and `[release]` settings.

    Imports of provider modules are lazy so a ``--provider=github``
    invocation never imports ADO machinery (and vice-versa). Returns the
    constructed provider; raises :class:`typer.BadParameter` for any
    invalid combination of flags.
    """
    release_cfg: Any = None
    if (
        provider_kind is None
        or (
            provider_kind == "ado"
            and (not ado_organization or not ado_project or not ado_tenant_id)
        )
        or (provider_kind == "github" and (not github_owner or not github_repo))
    ):
        try:
            release_cfg = load_settings_for_cli().release
        except Exception:
            release_cfg = None

    effective_kind = provider_kind or (release_cfg.provider if release_cfg is not None else None)
    if not effective_kind:
        raise typer.BadParameter(
            "Missing option '--provider' (or configure [release] provider in .sigantry.toml).",
            param_hint="'--provider'",
        )

    if effective_kind == "ado":
        from sigantry_core._ado_organization import AdoOrganizationError
        from sigantry_core.workitems.ado import AdoWorkItemProvider

        ado_cfg = (release_cfg.ado if release_cfg is not None else {}) or {}
        eff_org = ado_organization or ado_cfg.get("organization")
        eff_proj = ado_project or ado_cfg.get("project")
        eff_tid = resolve_tenant_id(ado_tenant_id or ado_cfg.get("tenant_id"))
        if not eff_org or not eff_proj:
            raise typer.BadParameter(
                "--ado-organization and --ado-project are required when --provider=ado."
            )
        try:
            return AdoWorkItemProvider.from_defaults(
                organization=str(eff_org),
                project=str(eff_proj),
                tenant_id=eff_tid,
            )
        except AdoOrganizationError as exc:
            raise typer.BadParameter(str(exc), param_hint="--ado-organization") from exc
    if effective_kind == "github":
        from sigantry_core.workitems.github import GithubWorkItemProvider

        gh_cfg = (release_cfg.github if release_cfg is not None else {}) or {}
        eff_owner = github_owner or gh_cfg.get("owner")
        eff_repo = github_repo or gh_cfg.get("repo")
        eff_pat = github_pat if github_pat is not None else gh_cfg.get("pat")
        eff_app_id = github_app_id or gh_cfg.get("app_id")
        eff_pem = github_private_key_pem or gh_cfg.get("private_key_pem")
        eff_install_id = github_installation_id or gh_cfg.get("installation_id")
        if not eff_owner or not eff_repo:
            raise typer.BadParameter(
                "--github-owner and --github-repo are required when --provider=github."
            )
        kwargs: dict[str, str] = {"owner": str(eff_owner), "repo": str(eff_repo)}
        if eff_pat is not None:
            kwargs["pat"] = str(eff_pat)
        else:
            if not (eff_app_id and eff_pem and eff_install_id):
                raise typer.BadParameter(
                    "Provide --github-pat OR all three of --github-app-id, "
                    "--github-private-key-pem, --github-installation-id."
                )
            kwargs["app_id"] = str(eff_app_id)
            kwargs["private_key_pem"] = str(eff_pem)
            kwargs["installation_id"] = str(eff_install_id)
        return GithubWorkItemProvider(**kwargs)  # type: ignore[arg-type]
    raise typer.BadParameter(f"--provider must be 'ado' or 'github'; got {effective_kind!r}.")


@release_app.command("record")
@stops_on_tenant_error(exit_code=1)
def record_cmd(
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Work-item provider kind: 'ado' or 'github' (falls back to [release] provider).",
    ),
    release_id: str = typer.Option(
        ..., "--release-id", help="Release identifier (any operator-meaningful string)."
    ),
    workspace: str = typer.Option(
        ..., "--workspace", help="Fabric workspace id (the deploy target)."
    ),
    work_items: str = typer.Option(
        ...,
        "--work-items",
        help="Comma-separated work-item / issue ids (e.g. '1234,5678').",
    ),
    approver: str = typer.Option(..., "--approver", help="Approver email or principal name."),
    fabric_items: str = typer.Option(
        "",
        "--fabric-items",
        help="Comma-separated Fabric items changed (e.g. 'nb.Notebook,lh.Lakehouse').",
    ),
    test_evidence: str = typer.Option(
        "{}",
        "--test-evidence",
        help='JSON object of test-evidence summary, e.g. \'{"smoke":"passed"}\'.',
    ),
    audit_dir: str | None = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/ (used in tests + CI).",
    ),
    ado_organization: str | None = typer.Option(
        None,
        "--ado-organization",
        help=(
            "ADO organisation name, or its collection URL such as "
            "$(System.CollectionUri) (required for --provider=ado)."
        ),
    ),
    ado_project: str | None = typer.Option(
        None, "--ado-project", help="ADO project (required for --provider=ado)."
    ),
    ado_tenant_id: str | None = typer.Option(
        None,
        "--ado-tenant-id",
        help=(
            "Entra tenant ID (GUID) to pin for the Azure DevOps token: a token from "
            "any other tenant is refused."
        ),
    ),
    github_owner: str | None = typer.Option(
        None,
        "--github-owner",
        help="GitHub owner / organisation (required for --provider=github).",
    ),
    github_repo: str | None = typer.Option(
        None,
        "--github-repo",
        help="GitHub repository name (required for --provider=github).",
    ),
    github_pat: str | None = typer.Option(
        None,
        "--github-pat",
        envvar="GITHUB_TOKEN",
        help=(
            "GitHub PAT (mutually exclusive with --github-app-*). "
            "Falls back to the GITHUB_TOKEN environment variable when the "
            "flag is omitted -- mirrors the convention used by the gh CLI "
            "and sigantry pr-bot."
        ),
    ),
    github_app_id: str | None = typer.Option(
        None, "--github-app-id", help="GitHub App id (with --github-private-key-pem)."
    ),
    github_private_key_pem: str | None = typer.Option(
        None,
        "--github-private-key-pem",
        help="PEM string OR kv://<vault>/<secret> URI.",
    ),
    github_installation_id: str | None = typer.Option(
        None,
        "--github-installation-id",
        help="GitHub App installation id (with --github-app-id).",
    ),
) -> None:
    """Record a Sigantry release: build DeployRecord, audit it, link work items.

    The full lifecycle of a single ``record`` invocation:

    1. Parse and validate flags (work-item ids, JSON evidence body).
    2. Build the appropriate :class:`WorkItemProvider`.
    3. Build a :class:`DeployRecord` with ``created_at = datetime.now(UTC)``
       and call :meth:`with_hash` to populate the SHA-256 audit hash.
    4. Persist via :func:`emit_deploy_record` (non-pluggable audit plane).
    5. Call :meth:`provider.link_release` to post the structured comment
       on every linked work-item / issue.
    6. Print a one-line operator-facing summary on stdout.

    Exit codes:
      - 0 on success
      - 1 on provider runtime errors (network, auth, etc.)
      - 2 on flag-validation errors (Typer ``BadParameter``)
    """
    ids = _split_csv(work_items)
    if not ids:
        raise typer.BadParameter("--work-items must contain at least one id.")
    items_changed = _split_csv(fabric_items)
    evidence = _parse_test_evidence(test_evidence)

    provider_obj = _build_provider(
        provider,
        ado_organization=ado_organization,
        ado_project=ado_project,
        ado_tenant_id=ado_tenant_id,
        github_owner=github_owner,
        github_repo=github_repo,
        github_pat=github_pat,
        github_app_id=github_app_id,
        github_private_key_pem=github_private_key_pem,
        github_installation_id=github_installation_id,
    )
    audit_dir_path = resolve_audit_dir(audit_dir)

    record = DeployRecord(
        workspace=workspace,
        release_id=release_id,
        work_items=ids,
        fabric_items_changed=items_changed,
        test_evidence=evidence,
        approver=approver,
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()

    emit_deploy_record(record, audit_dir=audit_dir_path)
    provider_obj.link_release(release_id, ids, record)

    # The record is written and the work items commented on; the release id is
    # free text, so it goes to Rich as Text, which is not read as markup.
    _console.print(
        Text.assemble(
            "Recorded release ",
            (release_id, "bold"),
            " with audit_hash ",
            (record.audit_hash, "dim"),
            f"; commented on {len(ids)} work items.",
        )
    )


# ---------------------------------------------------------------------------
# Plan 12-03 / PIPELINE-04 -- read-side ledger subcommands
#
# All three are thin Typer wrappers over sigantry_core.release.ledger.
# They are read-only over the Phase 11 audit jsonl: no WorkItemProvider
# construction, no emit_deploy_record call. Open Q5 (locked): list defaults
# to NEWEST-FIRST.
# ---------------------------------------------------------------------------


@release_app.command("list")
def list_cmd(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "--env",
        help=(
            "Filter records by workspace substring. ``--env`` is a "
            "deprecated alias that matches the same field "
            "(DeployRecord.workspace) as ``--workspace``. "
            "``DeployRecord`` carries no separate environment label, so "
            "filtering by environment is not supported yet."
        ),
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help="Max records to show; most recent first.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON array instead of table.",
    ),
    audit_dir: str | None = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/ (used in tests + CI).",
    ),
) -> None:
    """List recorded releases (most recent first).

    The ``--workspace`` flag (alias: ``--env``) does a substring match
    against ``DeployRecord.workspace``. Note that in production this is
    a Fabric workspace GUID -- a literal env label like ``prod`` will
    NOT match unless the workspace GUID happens to contain that
    substring (unlikely). The flag is honest about what it filters; a
    real env-aware filter requires adding an ``environment`` field to
    ``DeployRecord`` (a SemVer-minor schema change), and is deferred.
    """
    audit_dir_path = resolve_audit_dir(audit_dir)
    records = list(iter_records(audit_dir=audit_dir_path))
    records.sort(key=lambda r: r.created_at, reverse=True)
    if workspace:
        records = [r for r in records if workspace in r.workspace]
    records = records[:limit]
    if json_output:
        _console.print_json(data=[r.model_dump(mode="json") for r in records])
        return
    table = Table(title=f"Sigantry releases ({len(records)} shown)")
    table.add_column("release_id")
    table.add_column("workspace")
    table.add_column("approver")
    table.add_column("items")
    table.add_column("created_at")
    for r in records:
        # Ledger text goes to Rich as Text: a str cell is read as markup.
        table.add_row(
            Text(r.release_id),
            Text(r.workspace),
            Text(r.approver),
            str(len(r.fabric_items_changed)),
            Text(r.created_at.isoformat()),
        )
    _console.print(table)


@release_app.command("show")
def show_cmd(
    release_id: str = typer.Argument(..., help="Release id to display."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Reserved for future divergence; show currently always emits "
            "pretty-printed JSON. Kept on the surface so CI consumers can "
            "pin --json today and stay green when a human-mode renderer "
            "lands."
        ),
    ),
    audit_dir: str | None = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/.",
    ),
    html: bool = typer.Option(
        False,
        "--html",
        help="Emit standalone interactive HTML report.",
    ),
    html_out: str | None = typer.Option(
        None,
        "--html-out",
        help="Write HTML report to this file path instead of stdout.",
    ),
) -> None:
    """Show one DeployRecord by release_id.

    Output: pretty-printed JSON via ``rich.console.Console.print_json``, or
    standalone interactive HTML when ``--html`` is specified.
    """
    audit_dir_path = resolve_audit_dir(audit_dir)
    record = find_by_release_id(release_id, audit_dir=audit_dir_path)
    if record is None:
        _console.print(
            Text.assemble(
                ("No release ", "red"), (release_id, "bold red"), (" found in ledger.", "red")
            )
        )
        raise typer.Exit(code=1)
    payload = record.model_dump(mode="json")
    if html:
        from sigantry_core.reports.html import render_release_html_report

        html_content = render_release_html_report(payload)
        if html_out:
            Path(html_out).write_text(html_content, encoding="utf-8")
            _console.print(
                Text.assemble(("HTML release report written to:", "green"), " ", html_out)
            )
        else:
            typer.echo(html_content)
        return
    del json_output  # explicit no-op acknowledgement; see docstring
    _console.print_json(data=payload)


@release_app.command("diff")
def diff_cmd(
    release_id_1: str = typer.Argument(..., help="Earlier release id."),
    release_id_2: str = typer.Argument(..., help="Later release id."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the diff as JSON (its schema follows SemVer).",
    ),
    audit_dir: str | None = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/.",
    ),
    html: bool = typer.Option(
        False,
        "--html",
        help="Emit standalone interactive HTML report.",
    ),
    html_out: str | None = typer.Option(
        None,
        "--html-out",
        help="Write HTML report to this file path instead of stdout.",
    ),
) -> None:
    """Diff fabric_items_changed between two releases (added / removed / unchanged).

    JSON schema (SemVer-committed via test_diff_json_schema)::

        {
          "release_a": "<id>",
          "release_b": "<id>",
          "added":     [{"logical_name", "item_type", "fabric_item_id"}, ...],
          "removed":   [...],
          "unchanged": [...]
        }
    """
    audit_dir_path = resolve_audit_dir(audit_dir)
    a = find_by_release_id(release_id_1, audit_dir=audit_dir_path)
    b = find_by_release_id(release_id_2, audit_dir=audit_dir_path)
    if a is None:
        _console.print(
            Text.assemble(
                ("No release ", "red"), (release_id_1, "bold red"), (" in ledger.", "red")
            )
        )
        raise typer.Exit(code=1)
    if b is None:
        _console.print(
            Text.assemble(
                ("No release ", "red"), (release_id_2, "bold red"), (" in ledger.", "red")
            )
        )
        raise typer.Exit(code=1)
    diff = diff_records(a, b)
    if html:
        from sigantry_core.reports.html import render_release_diff_html_report

        payload = {
            "release_a": release_id_1,
            "release_b": release_id_2,
            **diff,
        }
        html_content = render_release_diff_html_report(payload)
        if html_out:
            Path(html_out).write_text(html_content, encoding="utf-8")
            _console.print(
                Text.assemble(("HTML release diff report written to:", "green"), " ", html_out)
            )
        else:
            typer.echo(html_content)
        return
    if json_output:
        payload = {
            "release_a": release_id_1,
            "release_b": release_id_2,
            **diff,
        }
        _console.print_json(data=payload)
        return
    _console.print(
        f"[green]+ {len(diff['added'])}[/green] / "
        f"[red]- {len(diff['removed'])}[/red] / "
        f"[dim]= {len(diff['unchanged'])}[/dim]"
    )
    for entry in diff["added"]:
        _console.print(Text(f"  + {entry['fabric_item_id']}", style="green"))
    for entry in diff["removed"]:
        _console.print(Text(f"  - {entry['fabric_item_id']}", style="red"))
    # WR-02 (review fix): human-mode previously emitted only `added`
    # (green +) and `removed` (red -) detail lines, leaving operators
    # who asked "which items did NOT change?" with the count header but
    # no detail. JSON-mode always carried the full ``unchanged`` bucket;
    # the human / JSON shapes are now symmetric. Use [dim] to keep the
    # delta entries visually dominant.
    for entry in diff["unchanged"]:
        _console.print(Text(f"  = {entry['fabric_item_id']}", style="dim"))


def _resolve_record_class(filename: str) -> type[Any]:
    """Return the Pydantic record class corresponding to a ledger filename."""
    from sigantry_core.governance.records import (
        ApprovalRecord,
        DestructiveOpRecord,
        SecretChangeRecord,
    )
    from sigantry_core.workspace.records import BootstrapRecord

    mapping: dict[str, type[Any]] = {
        "deploys.jsonl": DeployRecord,
        "approvals.jsonl": ApprovalRecord,
        "secret_changes.jsonl": SecretChangeRecord,
        "destructive_ops.jsonl": DestructiveOpRecord,
        "bootstraps.jsonl": BootstrapRecord,
    }
    return mapping.get(filename, DeployRecord)


_STANDARD_LEDGERS: tuple[str, ...] = (
    "deploys.jsonl",
    "approvals.jsonl",
    "secret_changes.jsonl",
    "destructive_ops.jsonl",
    "bootstraps.jsonl",
)


def _read_ledger_lines_strict(
    path: Path,
    record_cls: type[Any] = DeployRecord,
) -> tuple[list[Any], list[str]]:
    """Parse every ledger line in FILE ORDER, reporting rather than skipping.

    This deliberately does NOT reuse :func:`sigantry_core.release.ledger.iter_records`.
    That reader's documented posture is to LOG AND SKIP any line that fails
    JSON / pydantic parsing or ``verify_hash()``, which is correct for a
    traversal API -- one hand-edited entry should not make the ledger
    unreadable. It is exactly wrong for a verifier: a tamper-detection
    command built on a reader that silently drops tampered lines would
    report a clean chain over the surviving subset, which is a check
    incapable of failing in the case it exists to catch.

    Returns
    -------
    tuple[list[Any], list[str]]
        ``(records, problems)`` -- records in file (append) order, and a
        list of human-readable per-line problems. A non-empty ``problems``
        list means the ledger is NOT verifiable, independently of whether
        the parsed subset happens to chain.
    """
    records: list[Any] = []
    problems: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = _json.loads(line)
            except _json.JSONDecodeError as exc:
                problems.append(f"line {lineno}: not valid JSON ({exc})")
                continue
            try:
                records.append(record_cls(**payload))
            except Exception as exc:
                # Broad by intent: ANY schema breakage must be reported to the
                # operator, not narrowed to the exception types we anticipated.
                problems.append(f"line {lineno}: does not parse as {record_cls.__name__} ({exc})")
    return records, problems


def _evaluate_ledger(path: Path) -> dict[str, Any]:
    """Verify a single JSONL ledger file and return its structured verdict."""
    if not path.exists():
        return {
            "valid": True,
            "records": 0,
            "vacuous": True,
            "ledger": str(path),
            "reason": "ledger file does not exist - nothing to verify",
        }
    record_cls = _resolve_record_class(path.name)
    records, problems = _read_ledger_lines_strict(path, record_cls=record_cls)
    if problems:
        return {
            "valid": False,
            "records": len(records),
            "vacuous": False,
            "ledger": str(path),
            "reason": "unparseable ledger lines",
            "problems": problems,
        }
    is_valid, bad_index, reason = verify_audit_chain(records)
    return {
        "valid": is_valid,
        "records": len(records),
        "vacuous": len(records) == 0,
        "ledger": str(path),
        "first_bad_index": bad_index,
        "reason": reason,
    }


def _render_single_verdict(verdict: dict[str, Any]) -> None:
    """Print human-readable output for a single ledger verdict."""
    path = verdict["ledger"]
    if not verdict["valid"] and "problems" in verdict:
        _console.print(Text.assemble(("CHAIN UNVERIFIABLE", "red"), f" {path}"))
        for problem in verdict["problems"]:
            _console.print(Text(f"  - {problem}", style="red"))
        return
    if not verdict["valid"]:
        _console.print(Text.assemble(("CHAIN BROKEN", "red"), f" {path}"))
        _console.print(Text(f"  first bad record index: {verdict['first_bad_index']}", style="red"))
        _console.print(Text(f"  reason: {verdict['reason']}", style="red"))
        return
    if verdict["records"] == 0:
        if verdict.get("reason") == "ledger file does not exist - nothing to verify":
            _console.print(Text.assemble(("NOTHING TO VERIFY", "yellow"), f" no ledger at {path}"))
        else:
            _console.print(
                Text.assemble(
                    ("NOTHING TO VERIFY", "yellow"),
                    f" {path} holds 0 records (chain is vacuously valid)",
                )
            )
        return
    _console.print(
        Text.assemble(
            ("CHAIN VALID", "green"),
            f" {verdict['records']} record(s) verified in append order from {path}",
        )
    )


@release_app.command("verify")
def verify_cmd(
    audit_dir: str | None = typer.Option(
        None,
        "--audit-dir",
        help="Override ~/.sigantry/audit/ (used in tests + CI).",
    ),
    ledger_file: str = typer.Option(
        "deploys.jsonl",
        "--ledger-file",
        help="Ledger filename in --audit-dir (or path) to verify.",
    ),
    all_ledgers: bool = typer.Option(
        False,
        "--all",
        help="Verify all five audit ledgers (deploys, approvals, secret_changes, destructive_ops, bootstraps).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a JSON verdict instead of human-readable output.",
    ),
) -> None:
    """Verify the deploy ledger (or all audit ledgers) forms an unbroken SHA-256 chain.

    Exposes :func:`sigantry_core.governance.audit_io.verify_audit_chain` as an
    operator command. Before this existed, ``sigantry release`` offered
    ``record`` / ``list`` / ``show`` / ``diff`` and no way to check the
    integrity property the ledger is built for -- an auditor had to import the
    library and write Python. An integrity-checked record store whose integrity
    cannot be checked from the CLI is unfinished.

    What this proves, and what it does not: the chain is an UNKEYED SHA-256, so
    a valid result means the file is internally consistent -- not that it is the
    file that was written. See docs/reference/audit-ledger-threat-model.md.

    Records are read in FILE (append) order, which is the order the chain was
    written in. Note this differs from ``release list``, which sorts by
    ``created_at`` for display; verifying a sorted view would be meaningless.

    Exit codes
    ----------
    0
        Chain valid. NOTE: a ledger with zero records exits 0 -- there is
        nothing to falsify. The record count is always printed so a vacuous
        pass is visible rather than indistinguishable from a real one.
    1
        Chain invalid, a line failed to parse, or the ledger is unreadable.
    """
    audit_dir_path = resolve_audit_dir(audit_dir, default_dir=_DEFAULT_AUDIT_DIR)

    if all_ledgers:
        ledgers: dict[str, dict[str, Any]] = {}
        for name in _STANDARD_LEDGERS:
            ledgers[name] = _evaluate_ledger(audit_dir_path / name)
        overall_valid = all(v["valid"] for v in ledgers.values())
        total_records = sum(int(v["records"]) for v in ledgers.values())
        all_verdict = {
            "valid": overall_valid,
            "records": total_records,
            "vacuous": total_records == 0,
            "ledgers": ledgers,
        }
        if json_output:
            _console.print_json(data=all_verdict)
        else:
            for v in ledgers.values():
                _render_single_verdict(v)
        raise typer.Exit(0 if overall_valid else 1)

    candidate = Path(ledger_file)
    path = (
        candidate
        if candidate.is_absolute() or "/" in ledger_file or "\\" in ledger_file
        else audit_dir_path / ledger_file
    )
    verdict = _evaluate_ledger(path)
    if json_output:
        _console.print_json(data=verdict)
    else:
        _render_single_verdict(verdict)
    raise typer.Exit(0 if verdict["valid"] else 1)


__all__ = ("release_app",)
