"""Greenfield workspace materialiser (BOOTSTRAP-XX, Phase 13.5).

Config-driven workspace bootstrap: a single ``sigantry workspace bootstrap
workspace.yml`` invocation runs the 5-call REST sequence

    create workspace -> bind capacity -> create folders -> connect Git -> initialize

against a Fabric tenant. Each step is **probe-before-act**: the toolkit
inspects current state, no-ops when already converged, and only POSTs when
state diverges from the manifest. This is the load-bearing property --
re-running a bootstrap on a partly-bootstrapped workspace must converge,
never error or drift.

Design notes:

- Folder layouts come from a named blueprint in :mod:`.blueprints`
  (``minimal_starter``: ``00_control`` / ``10_intake`` / ... /
  ``99_retired``) or from an explicit ``folders.list``.
- sigantry 1.0.1 changed the blueprint folder names, and bootstrap never
  renames or deletes a folder. When a blueprint run will create any of its
  folders at the top level of a workspace that already has top-level
  folders with other names, bootstrap still creates them beside those
  folders, but warns first and points at ``folders.list``, which keeps an
  existing layout. The check compares the workspace only with the
  blueprint's own names.
- Stage markers (``[DEV]`` / ``[TEST]`` / ``[PREPROD]`` / ``[PROD]`` /
  ``[F] <branch>``) prefix the workspace display name at bootstrap time
  when the operator opts in.
- ``workspace.yml`` is validated with jsonschema at parse time. The schema
  is shipped as a Python literal here (operator-readable error messages, no
  runtime filesystem dependency on a separate ``schemas/workspace.json``).

Related primitives:

- :func:`sigantry_core.workspace.core.delete_workspace` accepts
  ``pbi_fallback=True``: when the Fabric DELETE fails with the transient
  ``UnknownError``, it retries through the Power BI API.
- :func:`sigantry_core.deploy.git_integration.connect_or_reconnect` handles
  disconnect-before-reconnect on Git binding mismatch. Used unchanged for
  step 4.
- :func:`sigantry_core.client.pagination.paginate` refuses to follow a
  ``continuationToken`` it has already followed. Used implicitly by
  ``list_folders`` for the probe step.

Out of scope (deferred):

- Multi-stage Dev/Test/Prod/Pipeline orchestration ("onboard" verb).
- Branch-isolated ``feature-workspace`` lifecycle (auto-create/auto-destroy).
- Pipeline user management (Power BI API surface).
- Native deployment-pipeline integration.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.deploy.git_integration import connect_or_reconnect, initialize_connection
from sigantry_core.workspace.blueprints import get_blueprint
from sigantry_core.workspace.capacity import assign_to_capacity
from sigantry_core.workspace.core import (
    Workspace,
    create_workspace,
    get_workspace,
    list_workspaces,
)
from sigantry_core.workspace.folders import Folder, create_folder, list_folders
from sigantry_core.workspace.records import (
    BootstrapRecord,
    StepOutcome,
    emit_bootstrap_record,
)

logger = logging.getLogger("sigantry_core.workspace.bootstrap")

#: JSONSchema for ``workspace.yml``. Draft 2020-12. Operator-readable
#: validation -- jsonschema raises ``ValidationError`` with a path that
#: points at the offending key in the source document.
#:
#: Schema design choices:
#:
#: - ``additionalProperties: false`` everywhere -- typos surface as
#:   "Additional properties not allowed" rather than silently passing.
#: - ``schema_version`` is required + literal ``"1.0"`` -- gives us a
#:   forward-compat hook when v2 lands.
#: - ``workspace.stage`` is the conventional set plus ``FEATURE``
#:   (the ``[F]`` branch-isolated case).
#: - ``folders.blueprint`` and ``folders.list`` are mutually-exclusive
#:   (``oneOf``); if both supplied, validation fails.
#: - ``git.enabled`` is required at the ``git`` block level. When ``true``,
#:   the five Git fields are required; when ``false``, none are allowed.
WORKSPACE_SCHEMA: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "workspace"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "workspace": {
            "type": "object",
            "required": ["name", "capacity_id"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string", "maxLength": 1000},
                "stage": {
                    "type": "string",
                    "enum": ["DEV", "TEST", "PREPROD", "PROD", "FEATURE", "NONE"],
                    "default": "NONE",
                },
                "stage_marker_in_name": {
                    "type": "boolean",
                    "default": False,
                },
                "feature_branch": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_./-]+$",
                    "maxLength": 100,
                },
                "capacity_id": {
                    "type": "string",
                    "pattern": (
                        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
                    ),
                },
                "domain_id": {
                    "type": ["string", "null"],
                },
            },
        },
        "folders": {
            "type": "object",
            "additionalProperties": False,
            "oneOf": [
                {"required": ["blueprint"]},
                {"required": ["list"]},
                {"not": {"anyOf": [{"required": ["blueprint"]}, {"required": ["list"]}]}},
            ],
            "properties": {
                "blueprint": {"type": "string", "minLength": 1},
                "list": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                    "minItems": 1,
                },
            },
        },
        "git": {
            "type": "object",
            "required": ["enabled"],
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean"},
                "provider": {"type": "string", "enum": ["ado"]},
                "organization_name": {"type": "string", "minLength": 1},
                "project_name": {"type": "string", "minLength": 1},
                "repository_name": {"type": "string", "minLength": 1},
                "branch_name": {"type": "string", "minLength": 1},
                "directory_name": {"type": "string", "minLength": 1},
                "git_connection_id": {"type": "string", "minLength": 1},
                "force_reconnect": {"type": "boolean", "default": False},
            },
            "if": {"properties": {"enabled": {"const": True}}, "required": ["enabled"]},
            "then": {
                "required": [
                    "provider",
                    "organization_name",
                    "project_name",
                    "repository_name",
                    "branch_name",
                    "directory_name",
                    "git_connection_id",
                ],
            },
        },
    },
}

#: Stage-marker prefixes, applied when ``stage_marker_in_name`` is true.
_STAGE_MARKERS: Final[dict[str, str]] = {
    "DEV": "[DEV]",
    "TEST": "[TEST]",
    "PREPROD": "[PREPROD]",
    "PROD": "[PROD]",
    "FEATURE": "[F]",
    "NONE": "",
}


class BootstrapValidationError(ValueError):
    """Raised when ``workspace.yml`` fails JSONSchema validation.

    ``__str__`` returns an operator-readable message: the failing key
    path + the validation rule that triggered the failure.
    """


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    """Validated ``workspace.yml`` payload.

    The raw doc is preserved on ``raw`` for rare cases where the caller
    needs to inspect a field this dataclass doesn't surface yet (forward
    compat). Everything load-bearing is on the typed properties.
    """

    raw: dict[str, Any]
    path: str
    workspace_name: str
    workspace_description: str | None
    stage: str
    stage_marker_in_name: bool
    feature_branch: str | None
    capacity_id: str
    domain_id: str | None
    blueprint: str | None
    folder_list: tuple[str, ...]
    git_enabled: bool
    git_target: dict[str, str] | None
    git_force_reconnect: bool

    @property
    def stage_marked_name(self) -> str:
        """Workspace display name with stage marker prefix when opted in.

        If ``stage_marker_in_name=False`` (default), returns ``workspace_name``
        unchanged. If ``True`` and stage is ``FEATURE``, the marker is
        ``[F] <feature_branch>`` and the branch is appended.
        """
        if not self.stage_marker_in_name or self.stage == "NONE":
            return self.workspace_name
        marker = _STAGE_MARKERS[self.stage]
        if self.stage == "FEATURE" and self.feature_branch:
            return f"{marker} {self.feature_branch} {self.workspace_name}".strip()
        return f"{marker} {self.workspace_name}".strip()


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Outcome of a single bootstrap run.

    ``record`` is the audit-plane :class:`BootstrapRecord` already
    appended to the ledger. The other fields surface step-by-step state
    for callers that want to print or assert without re-reading the
    ledger. ``warnings`` holds the text of every warning the run raised,
    in order; it is empty on a normal run and is not part of ``record``.
    """

    workspace: Workspace
    folders: tuple[Folder, ...]
    record: BootstrapRecord
    step_outcomes: dict[str, StepOutcome] = field(default_factory=dict)
    dry_run: bool = False
    warnings: tuple[str, ...] = ()


#: Callback that receives a warning's text as soon as bootstrap knows it.
WarningSink = Callable[[str], None]


def _log_warning(message: str) -> None:
    logger.warning("%s", message)


def _folders_to_create(config: BootstrapConfig, existing: Mapping[str, Folder]) -> list[str]:
    """Return the desired folder names that the folder step creates, in order.

    ``existing`` maps display name to folder over the workspace's folder
    listing. A desired name is missing when no listed folder has it; this
    is the same test :func:`_ensure_folders` applies before it creates a
    folder, so the dry run and the warning count what a real run creates.
    """
    return [name for name in config.folder_list if name not in existing]


def _existing_layout_warning(
    config: BootstrapConfig,
    listed: Sequence[Folder],
    to_create: Sequence[str],
    *,
    dry_run: bool,
) -> str | None:
    """Return a warning when a blueprint adds top-level folders beside others.

    It fires when all three hold:

    - the folders come from ``folders.blueprint``, not ``folders.list``;
    - bootstrap creates at least one of the blueprint's folders
      (``to_create``, from :func:`_folders_to_create`); it creates every
      folder at the top level of the workspace;
    - the workspace already has at least one top-level folder whose name is
      not one of the blueprint's names.

    The check compares the workspace only with the blueprint's own names,
    so it holds no folder name from any earlier release. It fires for any
    such workspace, whoever created its folders, which is why it warns and
    never refuses: 1.0.0 allowed a first bootstrap into a workspace that
    already had folders. The text gives counts and names no folder.
    """
    if config.blueprint is None or not to_create:
        return None
    own = set(config.folder_list)
    others = sum(
        1 for folder in listed if folder.parent_folder_id is None and folder.display_name not in own
    )
    if others == 0:
        return None
    count = f"{others} top-level folder{'' if others == 1 else 's'}"
    total = len(config.folder_list)
    share = (
        f"all {total} of the blueprint's folders"
        if len(to_create) == total
        else f"{len(to_create)} of the blueprint's {total} folders"
    )
    action = "would create" if dry_run else "creates"
    return (
        f"workspace {config.stage_marked_name!r} already has {count} whose names are "
        f"not in blueprint {config.blueprint!r}, and bootstrap {action} {share} "
        "at the top level beside them. "
        "sigantry 1.0.1 changed the folder names of the minimal_starter and "
        "medallion blueprints, and bootstrap never renames or deletes a folder. "
        "To keep the folders an earlier bootstrap created, list their names under "
        f"folders.list in {config.path} instead of naming a blueprint."
    )


def _report_warnings(
    config: BootstrapConfig,
    listed: Sequence[Folder],
    to_create: Sequence[str],
    *,
    dry_run: bool,
    on_warning: WarningSink,
) -> tuple[str, ...]:
    """Work out the run's warnings, hand each to ``on_warning``, return them.

    The dry run and the real run both call this, before any folder is
    created. The sink only reports: if it raises (a closed stderr, a
    failing callback), the error is logged at WARNING level and bootstrap
    carries on, so the run still finishes its steps and writes its audit
    record. The warning stays on :attr:`BootstrapResult.warnings`.
    """
    message = _existing_layout_warning(config, listed, to_create, dry_run=dry_run)
    if message is None:
        return ()
    try:
        on_warning(message)
    except Exception:
        logger.warning(
            "bootstrap.warning-sink-failed; bootstrap continues. warning=%s",
            message,
            exc_info=True,
        )
    return (message,)


# ---------------------------------------------------------------------------
# Loader + validator
# ---------------------------------------------------------------------------


def load_and_validate(path: str | Path) -> BootstrapConfig:
    """Read ``workspace.yml`` and return a typed :class:`BootstrapConfig`.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        BootstrapValidationError: JSONSchema validation failed -- message
            includes the offending JSON-path and rule name.
        ValueError: the document parses but a cross-field constraint
            (e.g. ``stage=FEATURE`` requires ``feature_branch``) is
            violated.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"workspace.yml not found at {p}")
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: top-level YAML must be a mapping; got {type(doc).__name__}")

    validator = Draft202012Validator(WORKSPACE_SCHEMA)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        path_str = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise BootstrapValidationError(
            f"{p}: schema validation failed at '{path_str}': "
            f"{first.message} (rule: {first.validator})"
        )

    workspace = doc["workspace"]
    folders = doc.get("folders") or {}
    git = doc.get("git") or {"enabled": False}

    stage = workspace.get("stage", "NONE")
    feature_branch = workspace.get("feature_branch")
    if stage == "FEATURE" and not feature_branch:
        raise ValueError(f"{p}: workspace.stage=FEATURE requires workspace.feature_branch")
    if stage != "FEATURE" and feature_branch:
        raise ValueError(
            f"{p}: workspace.feature_branch is only valid with workspace.stage=FEATURE"
        )

    blueprint_name = folders.get("blueprint")
    folder_list_raw = folders.get("list")
    if blueprint_name:
        folder_list = get_blueprint(blueprint_name)
    elif folder_list_raw:
        folder_list = tuple(folder_list_raw)
    else:
        # No folders block at all -- create the workspace with no folders.
        folder_list = ()

    git_target: dict[str, str] | None = None
    if git.get("enabled"):
        git_target = {
            "organization_name": git["organization_name"],
            "project_name": git["project_name"],
            "repository_name": git["repository_name"],
            "branch_name": git["branch_name"],
            "directory_name": git["directory_name"],
            "git_connection_id": git["git_connection_id"],
        }

    return BootstrapConfig(
        raw=doc,
        path=str(p),
        workspace_name=workspace["name"],
        workspace_description=workspace.get("description"),
        stage=stage,
        stage_marker_in_name=workspace.get("stage_marker_in_name", False),
        feature_branch=feature_branch,
        capacity_id=workspace["capacity_id"],
        domain_id=workspace.get("domain_id"),
        blueprint=blueprint_name,
        folder_list=folder_list,
        git_enabled=bool(git.get("enabled")),
        git_target=git_target,
        git_force_reconnect=bool(git.get("force_reconnect", False)),
    )


# ---------------------------------------------------------------------------
# Probe-before-act primitives
# ---------------------------------------------------------------------------


def _find_workspace_by_name(client: FabricRestClient, name: str) -> Workspace | None:
    """Return the first workspace whose displayName matches ``name`` exactly."""
    for ws in list_workspaces(client, roles="Admin,Contributor,Member"):
        if ws.display_name == name:
            return ws
    return None


def _ensure_workspace(
    client: FabricRestClient, config: BootstrapConfig
) -> tuple[Workspace, StepOutcome]:
    """Step 1: workspace exists. Probe by name; create if missing."""
    name = config.stage_marked_name
    existing = _find_workspace_by_name(client, name)
    if existing is not None:
        logger.info("bootstrap.workspace.already-converged id=%s", existing.id)
        return existing, "already-converged"

    created = create_workspace(
        client,
        display_name=name,
        capacity_id=config.capacity_id,
        description=config.workspace_description,
        domain_id=config.domain_id,
    )
    logger.info("bootstrap.workspace.created id=%s name=%r", created.id, name)
    return created, "created"


def _ensure_capacity(
    client: FabricRestClient, workspace: Workspace, config: BootstrapConfig
) -> StepOutcome:
    """Step 2: capacity binding matches manifest. Probe + assign on mismatch.

    ``create_workspace`` already accepts ``capacity_id`` and binds the
    capacity at creation time, so the common path on first-run is
    ``already-converged``. The branch we run is when an existing workspace
    was bound to a different capacity (or to none).
    """
    fresh = get_workspace(client, workspace.id)
    if fresh.capacity_id == config.capacity_id:
        logger.info(
            "bootstrap.capacity.already-converged workspace=%s capacity=%s",
            workspace.id,
            config.capacity_id,
        )
        return "already-converged"
    assign_to_capacity(client, workspace.id, config.capacity_id)
    logger.info(
        "bootstrap.capacity.bound workspace=%s capacity=%s previous=%s",
        workspace.id,
        config.capacity_id,
        fresh.capacity_id,
    )
    return "created"


def _ensure_folders(
    client: FabricRestClient,
    workspace: Workspace,
    config: BootstrapConfig,
    on_warning: WarningSink,
) -> tuple[tuple[Folder, ...], list[str], StepOutcome, tuple[str, ...]]:
    """Step 3: every desired folder exists. Probe + create missing ones.

    Order is preserved per ``BLUEPRINTS`` so the Fabric UI lists folders
    top-to-bottom in pipeline-flow order. Returns the resolved folder
    DTOs (existing + newly-created), the list of names *created* on this
    run (excludes already-converged ones), the step outcome, and the
    warnings from :func:`_report_warnings`, which runs before any folder
    is created.
    """
    if not config.folder_list:
        return (), [], "skipped", ()

    listed = list(list_folders(client, workspace.id))
    existing = {f.display_name: f for f in listed}
    warnings = _report_warnings(
        config,
        listed,
        _folders_to_create(config, existing),
        dry_run=False,
        on_warning=on_warning,
    )
    created_names: list[str] = []
    resolved: list[Folder] = []

    for desired in config.folder_list:
        if desired in existing:
            resolved.append(existing[desired])
            continue
        new_folder = create_folder(
            client, workspace.id, display_name=desired, parent_folder_id=None
        )
        resolved.append(new_folder)
        created_names.append(desired)
        logger.info(
            "bootstrap.folder.created workspace=%s folder=%r id=%s",
            workspace.id,
            desired,
            new_folder.id,
        )

    outcome: StepOutcome = "created" if created_names else "already-converged"
    return tuple(resolved), created_names, outcome, warnings


def _ensure_git(
    client: FabricRestClient, workspace: Workspace, config: BootstrapConfig
) -> StepOutcome:
    """Step 4: Git binding matches manifest. ``connect_or_reconnect`` is
    already idempotent; we just translate its return
    value to a :data:`StepOutcome`."""
    if not config.git_enabled or config.git_target is None:
        return "skipped"
    transition = connect_or_reconnect(
        client,
        workspace.id,
        organization_name=config.git_target["organization_name"],
        project_name=config.git_target["project_name"],
        repository_name=config.git_target["repository_name"],
        branch_name=config.git_target["branch_name"],
        directory_name=config.git_target["directory_name"],
        git_connection_id=config.git_target["git_connection_id"],
        force_reconnect=config.git_force_reconnect,
    )
    if transition == "already-connected":
        return "already-converged"
    if transition == "reconnected":
        return "reconnected"
    return "created"


def _ensure_initialized(
    client: FabricRestClient, workspace: Workspace, config: BootstrapConfig
) -> StepOutcome:
    """Step 5: workspace Git state initialized.

    Only meaningful when ``git.enabled=true``. The Fabric initialize endpoint
    is idempotent at the workspace level -- it returns the same
    ``requiredAction`` regardless of how many times we POST -- so we always
    call when Git is enabled. Translation table: a fresh ``connect`` triggers
    ``initialize`` with non-trivial ``requiredAction``; a re-connect against
    the same target returns ``None`` (no-op).
    """
    if not config.git_enabled:
        return "skipped"
    initialize_connection(client, workspace.id)
    return "created"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def bootstrap_workspace(
    config: BootstrapConfig,
    *,
    token_provider: TokenProvider | None = None,
    client: FabricRestClient | None = None,
    operator: str | None = None,
    audit_dir: Path | str | None = None,
    dry_run: bool = False,
    on_warning: WarningSink | None = None,
) -> BootstrapResult:
    """Run the 5-call probe-before-act sequence.

    Args:
        config: Validated :class:`BootstrapConfig` from
            :func:`load_and_validate`.
        token_provider: Optional :class:`TokenProvider`. Defaults to
            ``TokenProvider.from_defaults()`` (DefaultAzureCredential chain).
            Ignored when ``client`` is supplied.
        client: Optional pre-built :class:`FabricRestClient`. Used in
            tests; production callers pass ``token_provider`` (or rely on
            the default).
        operator: Identity recorded on the :class:`BootstrapRecord`.
            Defaults to ``$USER`` or ``"unknown"``.
        audit_dir: Override the audit-ledger directory. Used in tests +
            hermetic CI runners. Defaults to ``~/.sigantry/audit/``.
        dry_run: When ``True``, run every probe but skip every mutate
            step. The returned :class:`BootstrapResult.dry_run` is
            ``True`` and ``record`` is *not* appended to the ledger.
        on_warning: Called with each warning's text as soon as it is
            known, before bootstrap acts on what it warns about. Defaults
            to logging the text at WARNING level on the
            ``sigantry_core.workspace.bootstrap`` logger. If it raises,
            the error is logged at WARNING level on that logger and the
            run carries on. Today the only warning is the one for a
            blueprint that adds top-level folders beside a workspace's
            other top-level folders (see the module docstring).

    Returns:
        :class:`BootstrapResult` with the resolved workspace, folder list,
        sealed audit record, step-outcome map, and every warning raised.

    Raises:
        Anything raised by the underlying primitives. Bootstrap does NOT
        attempt automatic rollback on partial failure -- the operator
        chooses, via the CLI's ``--rollback-on-failure`` flag (Phase 13.5
        v2 work).
    """
    sink = on_warning or _log_warning
    if dry_run:
        return _dry_run(config, client=client, token_provider=token_provider, on_warning=sink)

    own_client = False
    if client is None:
        tp = token_provider or TokenProvider.from_defaults()
        client = FabricRestClient.from_defaults(tenant_id=tp.tenant_id)
        own_client = True

    try:
        outcomes: dict[str, StepOutcome] = {}

        ws, outcomes["workspace"] = _ensure_workspace(client, config)
        outcomes["capacity"] = _ensure_capacity(client, ws, config)
        folders, folders_created, outcomes["folders"], warnings = _ensure_folders(
            client, ws, config, sink
        )
        outcomes["git"] = _ensure_git(client, ws, config)
        outcomes["initialize"] = _ensure_initialized(client, ws, config)

        record = BootstrapRecord(
            workspace_id=ws.id,
            workspace_name=config.stage_marked_name,
            stage=config.stage,
            capacity_id=config.capacity_id,
            blueprint=config.blueprint or "explicit",
            folders_created=folders_created,
            folders_present=[f.display_name for f in folders],
            git_target=(
                {k: v for k, v in config.git_target.items() if k != "git_connection_id"}
                if config.git_target
                else None
            ),
            step_outcomes={k: str(v) for k, v in outcomes.items()},
            operator=operator or os.environ.get("USER") or "unknown",
            created_at=datetime.now(UTC),
        ).with_hash()

        emit_bootstrap_record(record, audit_dir=audit_dir)
        return BootstrapResult(
            workspace=ws,
            folders=folders,
            record=record,
            step_outcomes=outcomes,
            dry_run=False,
            warnings=warnings,
        )
    finally:
        if own_client:
            client.close()


def _dry_run(
    config: BootstrapConfig,
    *,
    client: FabricRestClient | None,
    token_provider: TokenProvider | None,
    on_warning: WarningSink,
) -> BootstrapResult:
    """Read-only probe: resolve current state, emit a synthetic record,
    do not POST anything and do not append to the ledger.
    """
    own_client = False
    if client is None:
        tp = token_provider or TokenProvider.from_defaults()
        client = FabricRestClient.from_defaults(tenant_id=tp.tenant_id)
        own_client = True
    try:
        existing = _find_workspace_by_name(client, config.stage_marked_name)
        if existing is None:
            ws = Workspace(
                id="00000000-0000-0000-0000-000000000000",
                display_name=config.stage_marked_name,
                description=config.workspace_description,
                type="Workspace",
                capacity_id=config.capacity_id,
                domain_id=config.domain_id,
            )
            listed: list[Folder] = []
        else:
            ws = existing
            listed = list(list_folders(client, ws.id))
        existing_folders = {f.display_name: f for f in listed}
        would_create = _folders_to_create(config, existing_folders)
        warnings = _report_warnings(
            config, listed, would_create, dry_run=True, on_warning=on_warning
        )

        outcomes: dict[str, StepOutcome] = {
            "workspace": "already-converged" if existing else "created",
            "capacity": "already-converged"
            if existing and existing.capacity_id == config.capacity_id
            else "created",
            "folders": "created" if would_create else "already-converged",
            "git": "skipped" if not config.git_enabled else "created",
            "initialize": "skipped" if not config.git_enabled else "created",
        }
        record = BootstrapRecord(
            workspace_id=ws.id,
            workspace_name=config.stage_marked_name,
            stage=config.stage,
            capacity_id=config.capacity_id,
            blueprint=config.blueprint or "explicit",
            folders_created=would_create,
            folders_present=list(config.folder_list),
            git_target=(
                {k: v for k, v in config.git_target.items() if k != "git_connection_id"}
                if config.git_target
                else None
            ),
            step_outcomes={k: str(v) for k, v in outcomes.items()},
            operator=os.environ.get("USER") or "unknown",
            created_at=datetime.now(UTC),
        ).with_hash()
        return BootstrapResult(
            workspace=ws,
            folders=tuple(existing_folders.values()),
            record=record,
            step_outcomes=outcomes,
            dry_run=True,
            warnings=warnings,
        )
    finally:
        if own_client:
            client.close()


__all__ = [
    "WORKSPACE_SCHEMA",
    "BootstrapConfig",
    "BootstrapResult",
    "BootstrapValidationError",
    "bootstrap_workspace",
    "load_and_validate",
]
