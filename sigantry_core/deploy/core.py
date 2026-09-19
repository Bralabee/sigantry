"""sigantry_core.deploy.core - deploy_workspace wrapper (DEPLOY-01).

Wraps ``fabric-cicd`` 1.x (``>=1.0,<2.0``; delta-audited against 1.1.0 on
2026-06-11, re-audited against 1.3.0 on 2026-08-24) ``FabricWorkspace`` +
``publish_all_items``. Plumbs Phase 1
``TokenProvider`` into the 1.0.0+ mandatory ``token_credential`` kwarg
(Pitfall 4A — breaking change from 0.3.x to 1.0.0). Upstream contract
canaries live in ``tests/sigantry_core/deploy/test_fabric_cicd_contract.py``.

Destructive orphan cleanup wraps ``unpublish_all_orphan_items`` with
``@destructive_op`` (Pitfall 4B).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fabric_cicd import (
    FabricWorkspace,
    publish_all_items,
    unpublish_all_orphan_items,
)

from sigantry_core.auth import TokenProvider
from sigantry_core.deploy.dependency import validate_order
from sigantry_core.deploy.parameters import (
    load_and_validate,
    write_substituted_parameters,
)
from sigantry_core.governance.audit import destructive_op

logger = logging.getLogger("sigantry_core.deploy.core")


@dataclass(frozen=True, slots=True)
class DeployResult:
    """Outcome of a ``deploy_workspace`` call.

    ``items_failed`` is always 0 on a successful return (failures raise
    RuntimeError). It is exposed for forward-compatibility with a future
    ``strict=False`` flag.
    """

    workspace_id: str
    environment: str
    items_published: int
    items_failed: int
    orphans_unpublished: int
    dot_graph_path: str | None


def deploy_workspace(
    *,
    workspace_id: str,
    repository_directory: str,
    environment: str,
    item_type_in_scope: list[str],
    parameters_path: str | None = None,
    token_provider: TokenProvider | None = None,
    unpublish_orphans: bool = False,
    unpublish_force: bool = False,
    unpublish_runbook_id: str | None = None,
    item_name_exclude_regex: str | None = None,
    folder_path_exclude_regex: str | None = None,
    folder_path_to_include: list[str] | None = None,
    items_to_include: list[str] | None = None,
    shortcut_exclude_regex: str | None = None,
    bulk: bool = False,
    max_workers: int = 4,
) -> DeployResult:
    """Deploy a Fabric item tree via fabric-cicd 1.0.0.

    Args:
        workspace_id: Target Fabric workspace GUID.
        repository_directory: Git working tree containing ``.platform`` items.
        environment: Parameter-file environment key (e.g. ``"DEV"``).
        item_type_in_scope: fabric-cicd's selective-deploy scope list.
        parameters_path: Override path to ``parameters.yml`` (defaults to
            ``<repository_directory>/parameters.yml``).
        token_provider: Optional ``TokenProvider``; when ``None`` the
            default chain is constructed via ``TokenProvider.from_defaults()``.
        unpublish_orphans: If True, also delete workspace items absent from
            the tree. DESTRUCTIVE — requires ``unpublish_force=True``.
        unpublish_force: Explicit acknowledgement for ``unpublish_orphans``.
        unpublish_runbook_id: Optional incident reference recorded in the
            audit log (not required for the orphan-unpublish action).
        item_name_exclude_regex: fabric-cicd pass-through — items whose
            display name matches this regex are skipped during publish AND
            orphan-unpublish. Useful for excluding WIP / experimental items.
        folder_path_exclude_regex: fabric-cicd pass-through — items under
            folder paths matching this regex are skipped during publish.
            Applies only to publish, not orphan-unpublish (upstream does not
            support folder filters on ``unpublish_all_orphan_items``).
        folder_path_to_include: fabric-cicd pass-through — scope publish to
            these folder paths (allow-list). Applies only to publish.
        items_to_include: fabric-cicd pass-through — explicit allow-list of
            item names/types. Applies to both publish and orphan-unpublish.
        shortcut_exclude_regex: fabric-cicd pass-through — skip matching
            shortcuts during publish.
        bulk: If True, publish items concurrently using a worker pool.
        max_workers: Concurrency worker limit for bulk publish (default: 4).

    Raises:
        HardcodedGuidError: parameters.yml contains a raw GUID.
        DependencyCycleError: item tree contains a cycle.
        DestructiveOpError: ``unpublish_orphans=True`` without
            ``unpublish_force=True``.
        RuntimeError: any items failed to publish.

    Returns:
        ``DeployResult`` with item counts and the dep-graph DOT path.
    """
    tp = token_provider or TokenProvider.from_defaults()
    credential = tp.get_credential()

    # 1) Parameter validation — DEPLOY-03 teeth.
    params_path = parameters_path or f"{repository_directory.rstrip('/')}/parameters.yml"
    config = load_and_validate(params_path)

    # 2) Dependency cycle check + DOT artefact — DEPLOY-04.
    dot_path = validate_order(
        repository_directory=repository_directory,
        item_type_in_scope=item_type_in_scope,
    )

    # 3) $ENV: substitution. fabric-cicd v1.0.0 rejects $ENV:VAR refs in
    # replace_value slots ("Invalid replace_value variable format. Expected
    # format: $items.type.name.attribute or $workspace.id"), so the toolkit
    # expands them itself into a tempfile copy of parameters.yml. The
    # tempdir is left intact for the lifetime of the deploy and best-effort-
    # cleaned via TemporaryDirectory's context manager. The substitution is
    # a no-op when no $ENV: refs are present.
    _params_tmpdir = tempfile.TemporaryDirectory(prefix="sigantry-params-")
    try:
        substituted_path = write_substituted_parameters(
            config, Path(_params_tmpdir.name) / "parameters.yml"
        )

        # 4) Upstream instantiation — 1.0.0 REQUIRES token_credential.
        # parameter_file_path points at the substituted tempfile so
        # fabric-cicd never sees a $ENV:VAR token.
        target_workspace = FabricWorkspace(
            workspace_id=workspace_id,
            environment=environment,
            repository_directory=repository_directory,
            item_type_in_scope=item_type_in_scope,
            token_credential=credential,
            parameter_file_path=str(substituted_path),
        )
        return _publish_and_optionally_unpublish(
            target_workspace=target_workspace,
            workspace_id=workspace_id,
            environment=environment,
            repository_directory=repository_directory,
            item_type_in_scope=item_type_in_scope,
            parameters_path=str(substituted_path),
            dot_path=dot_path,
            tp=tp,
            unpublish_orphans=unpublish_orphans,
            unpublish_force=unpublish_force,
            unpublish_runbook_id=unpublish_runbook_id,
            item_name_exclude_regex=item_name_exclude_regex,
            folder_path_exclude_regex=folder_path_exclude_regex,
            folder_path_to_include=folder_path_to_include,
            items_to_include=items_to_include,
            shortcut_exclude_regex=shortcut_exclude_regex,
            bulk=bulk,
            max_workers=max_workers,
        )
    finally:
        _params_tmpdir.cleanup()


def _publish_and_optionally_unpublish(
    *,
    target_workspace: FabricWorkspace,
    workspace_id: str,
    environment: str,
    repository_directory: str,
    item_type_in_scope: list[str],
    parameters_path: str,
    dot_path: str,
    tp: TokenProvider,
    unpublish_orphans: bool,
    unpublish_force: bool,
    unpublish_runbook_id: str | None,
    item_name_exclude_regex: str | None,
    folder_path_exclude_regex: str | None,
    folder_path_to_include: list[str] | None,
    items_to_include: list[str] | None,
    shortcut_exclude_regex: str | None,
    bulk: bool = False,
    max_workers: int = 4,
) -> DeployResult:
    """Internal helper: publish + (optionally) orphan-unpublish + assemble result.

    Extracted so the substituted-parameters tempfile context (created in
    :func:`deploy_workspace`) wraps the entire fabric-cicd interaction --
    fabric-cicd reads ``parameter_file_path`` lazily during ``publish_all_items``,
    so the tempfile must outlive the publish call.
    """
    if bulk:
        candidate_items: list[str] = []
        if items_to_include:
            candidate_items = list(items_to_include)
        elif hasattr(target_workspace, "repository_items") and isinstance(
            target_workspace.repository_items, dict
        ):
            for it_type, bucket in target_workspace.repository_items.items():
                if isinstance(bucket, dict):
                    for it_name in bucket.keys():
                        candidate_items.append(f"{it_type}.{it_name}")
                elif isinstance(bucket, list):
                    for it in bucket:
                        name = getattr(it, "name", str(it))
                        candidate_items.append(f"{it_type}.{name}")
                else:
                    candidate_items.append(str(it_type))

        if len(candidate_items) > 1:
            import concurrent.futures

            def _publish_single_item(spec: str) -> bool:
                ws = FabricWorkspace(
                    workspace_id=workspace_id,
                    environment=environment,
                    repository_directory=repository_directory,
                    item_type_in_scope=item_type_in_scope,
                    token_credential=tp.get_credential(),
                    parameter_file_path=parameters_path,
                )
                try:
                    publish_all_items(ws, items_to_include=[spec])
                    return True
                except Exception:
                    return False

            workers = min(max_workers, len(candidate_items))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(_publish_single_item, candidate_items))
                published = results.count(True)
                failed = results.count(False)

            orphans = 0
            if unpublish_orphans:
                orphans = _unpublish_orphans_gated(
                    target_workspace,
                    force=unpublish_force,
                    runbook_id=unpublish_runbook_id,
                    resource_id=workspace_id,
                    token_provider=tp,
                    item_name_exclude_regex=item_name_exclude_regex,
                    items_to_include=items_to_include,
                )

            if failed > 0:
                raise RuntimeError(
                    f"{failed} item(s) failed to publish (succeeded={published}); "
                    f"see log for per-item status."
                )

            return DeployResult(
                workspace_id=workspace_id,
                environment=environment,
                items_published=published,
                items_failed=failed,
                orphans_unpublished=orphans,
                dot_graph_path=dot_path,
            )

    # 4) Publish — forward fabric-cicd's folder/item scope filters.
    publish_result = publish_all_items(
        target_workspace,
        item_name_exclude_regex=item_name_exclude_regex,
        folder_path_exclude_regex=folder_path_exclude_regex,
        folder_path_to_include=folder_path_to_include,
        items_to_include=items_to_include,
        shortcut_exclude_regex=shortcut_exclude_regex,
    )
    failed = _count_failed(publish_result)
    published = _count_succeeded(publish_result, target_workspace, failed)

    # 5) Conditional orphan cleanup — gated destructive. Only the two
    # filters upstream accepts on unpublish are forwarded; folder filters
    # are not supported by unpublish_all_orphan_items (probed: 1.0.0).
    orphans = 0
    if unpublish_orphans:
        orphans = _unpublish_orphans_gated(
            target_workspace,
            force=unpublish_force,
            runbook_id=unpublish_runbook_id,
            resource_id=workspace_id,
            token_provider=tp,
            item_name_exclude_regex=item_name_exclude_regex,
            items_to_include=items_to_include,
        )

    if failed > 0:
        raise RuntimeError(
            f"{failed} item(s) failed to publish (succeeded={published}); "
            f"see log for per-item status."
        )

    return DeployResult(
        workspace_id=workspace_id,
        environment=environment,
        items_published=published,
        items_failed=failed,
        orphans_unpublished=orphans,
        dot_graph_path=dot_path,
    )


@destructive_op("fabric_workspace", "unpublish_orphans")
def _unpublish_orphans_gated(
    target_workspace: FabricWorkspace,
    *,
    force: bool,
    runbook_id: str | None,
    resource_id: str | None = None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    item_name_exclude_regex: str | None = None,
    items_to_include: list[str] | None = None,
) -> int:
    """Destructive — requires ``force=True`` per Phase 3 decorator contract.

    ``item_name_exclude_regex`` and ``items_to_include`` are the two filters
    upstream's ``unpublish_all_orphan_items`` supports (probed on fabric-cicd
    1.0.0). ``None`` for ``item_name_exclude_regex`` is normalised to ``'^$'``
    (upstream default — matches nothing) so forwarding ``None`` is a no-op
    rather than an error.
    """
    before = _count_items(target_workspace)
    unpublish_all_orphan_items(
        target_workspace,
        item_name_exclude_regex=item_name_exclude_regex or "^$",
        items_to_include=items_to_include,
    )
    after = _count_items(target_workspace)
    return max(before - after, 0)


# ----------------------------------------------------------------------------
# Probe-adjusted return-shape helpers.
#
# fabric-cicd 1.0.0 `publish_all_items(...)` returns:
#   - None by default.
#   - dict of API responses when the `enable_response_collection` feature flag
#     is enabled (see upstream docstring).
#
# It raises on item failure (no `failed` counter in the default path). The
# toolkit's fallback policy (Rule 2 deviation): if the return value is None or lacks a
# failure indicator, treat "all items published" as succeeded via the workspace
# `repository_items` count. If the return is a dict carrying a failure summary
# (list-of-dicts with `status`, or `{summary: {Failed: N}}`), count explicitly.
# ----------------------------------------------------------------------------


def _count_failed(result: Any) -> int:
    """Return the number of failed items implied by ``result``.

    Supports three upstream shapes:
      * list/tuple of dicts each with ``status`` ("Succeeded" / "Failed")
      * dict containing ``{"summary": {"Failed": N}}``
      * object with ``.failed`` attribute
    Defaults to 0 (fabric-cicd raises on publish failure; a non-exception
    return implies success).
    """
    if isinstance(result, list | tuple):
        return sum(
            1
            for r in result
            if isinstance(r, dict) and str(r.get("status", "")).lower() == "failed"
        )
    if isinstance(result, dict):
        summary = result.get("summary")
        if isinstance(summary, dict) and "Failed" in summary:
            return int(summary["Failed"])
    failed = getattr(result, "failed", None)
    if isinstance(failed, int):
        return failed
    return 0


def _count_succeeded(result: Any, target_workspace: FabricWorkspace, failed: int) -> int:
    """Return the number of successfully-published items.

    Fallback order matches ``_count_failed``: explicit list/dict/attribute, then
    fall back to the workspace ``repository_items`` count minus failures.
    """
    if isinstance(result, list | tuple):
        return sum(
            1
            for r in result
            if isinstance(r, dict) and str(r.get("status", "")).lower() == "succeeded"
        )
    if isinstance(result, dict):
        summary = result.get("summary")
        if isinstance(summary, dict) and "Succeeded" in summary:
            return int(summary["Succeeded"])
    succeeded = getattr(result, "succeeded", None)
    if isinstance(succeeded, int):
        return succeeded
    # Fallback: fabric-cicd raises on failure; a non-exception return means
    # every item in scope was published.
    return max(_count_items(target_workspace) - failed, 0)


def _count_items(target_workspace: FabricWorkspace) -> int:
    """Best-effort item count.

    fabric-cicd 1.0.0 populates ``repository_items`` (probed); fallbacks cover
    older / patched upstream builds. Returns 0 when nothing useful is found.

    fabric-cicd 1.0.x exposes ``repository_items`` as a NESTED dict
    ``{item_type: {item_name: Item}}`` -- a bare ``len()`` on that returns the
    count of item-TYPE buckets, not the count of items, so a 14-item / 2-type
    publish would report 2. Walk one level deeper when a value is itself a dict
    (mirrors the sibling fix in ``sync_publish._count_succeeded``); the
    ``isinstance(v, dict)`` guard tolerates the older flat ``{name: Item}`` shape.
    """
    for attr in ("repository_items", "_items_to_publish", "_items", "items"):
        items = getattr(target_workspace, attr, None)
        if items is None:
            continue
        if isinstance(items, dict):
            try:
                return sum(
                    len(bucket) if isinstance(bucket, dict) else 1 for bucket in items.values()
                )
            except TypeError:
                continue
        try:
            return len(items)
        except TypeError:
            continue
    return 0
