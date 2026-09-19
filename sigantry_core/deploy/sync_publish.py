"""sigantry_core.deploy.sync_publish - first-time publish helper for sync apply --with-publish.

Phase 17 / SYNC-PUBLISH engine helper that closes ADR-0012 Option C. Hands
the staging tree built by :func:`sigantry_core.sync.apply.apply_sync` to
``fabric_cicd.publish_all_items`` with an ``items_to_include`` filter so
ONLY the manifest items that are absent from the workspace get published.
Existing items are reparented by the folder reconciler (unchanged from
v3.0); the absent set is what fabric-cicd creates.

Decision references (17-CONTEXT.md):

- **D-17-06** -- module placement: this file lives under ``sigantry_core/deploy/``
  so the fabric-cicd quarantine grep
  (``tests/sigantry_core/deploy/test_core.py::test_fabric_cicd_only_from_deploy_package``)
  stays GREEN. ``sigantry_core.sync.apply`` imports from here; it never
  imports ``fabric_cicd`` directly.
- **D-17-07** -- staging-tempdir lifetime: the caller (apply_sync) keeps the
  staging directory alive across the publish call. This helper validates
  that ``staging_dir.is_dir()`` BEFORE invoking fabric-cicd so a stale /
  cleaned tempdir surfaces as a clear :class:`SyncPublishError` rather
  than a confusing low-level ``FileNotFoundError`` from inside the
  fabric-cicd walker.
- **D-17-09** -- both ``enable_experimental_features`` AND
  ``enable_items_to_include`` MUST be appended to
  ``fabric_cicd.constants.FEATURE_FLAG`` BEFORE
  ``publish_all_items(workspace, items_to_include=[...])`` is called.
  Without these two flags fabric-cicd 1.0.x silently ignores the filter
  and publishes the WHOLE staging tree.

The ``parameters_path`` argument is the SUBSTITUTED tempfile path produced
by :func:`sigantry_core.deploy.parameters.write_substituted_parameters`
(PR #54). This helper does NOT re-substitute ``$ENV:`` references; the
caller (``apply_sync``) is responsible for the substitution dance and
the lifetime of the substituted tempfile.

T-17-05 mitigation: when fabric-cicd raises during publish, the wrapped
:class:`SyncPublishError` message is BOUNDED -- it never echoes the raw
exception text (which may embed parameters.yml content). The original
exception is chained via ``raise ... from exc`` so the traceback is
still inspectable in stderr.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fabric_cicd
from fabric_cicd import (
    FabricWorkspace,
    append_feature_flag,
    publish_all_items,
)

from sigantry_core.auth import TokenProvider
from sigantry_core.sync.errors import SyncPublishError
from sigantry_core.sync.manifest import SyncItem

logger = logging.getLogger("sigantry_core.deploy.sync_publish")

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of a :func:`publish_absent_items` call.

    Fields
    ------
    outcome : str
        One of ``"succeeded"`` / ``"partial-failure"`` / ``"failed"``.
        Maps to ``test_evidence['outcome']`` on the combined DeployRecord
        (D-17-05 encoding rule).
    published_items : list[str]
        ``"<display_name>.<type>"`` strings of items that successfully
        published. Empty list on full failure.
    failed_item : str | None
        Best-effort identification of the failing item on partial /
        full failure (``"<display_name>.<type>"``). ``None`` when the
        identification could not be performed or no failure occurred.
        Per D-17-05 / runbook §1.3, this is best-effort -- the field
        may be ``None`` even on a partial-failure outcome if the
        upstream exception text did not name a candidate item.
    """

    outcome: str
    published_items: list[str]
    failed_item: str | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
#
# ``_count_failed`` and ``_count_succeeded`` are functionally equivalent to
# the helpers in ``sigantry_core/deploy/core.py:279-326``. The plan
# (Decision D-17-06 / 17-01-02 step 7) chose to copy rather than import to
# keep the two deploy paths independently auditable -- changing one helper
# should not silently affect the other. ``deploy/core.py``'s versions stay
# unchanged for backward compatibility.


def _count_failed(result: Any) -> int:
    """Return the number of failed items implied by ``result``.

    Mirror of :func:`sigantry_core.deploy.core._count_failed`. Three
    return shapes supported (per fabric-cicd 1.0.x probe):

    * list / tuple of dicts each carrying ``status`` ("Succeeded" / "Failed")
    * dict containing ``{"summary": {"Failed": N}}``
    * object with ``.failed`` attribute

    Defaults to 0 -- fabric-cicd raises on publish failure, so a non-
    exception return implies success.
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


def _count_succeeded(result: Any, target_workspace: Any, failed: int) -> int:
    """Return the number of successfully-published items.

    Mirror of :func:`sigantry_core.deploy.core._count_succeeded`. Fallback
    order matches ``_count_failed``: explicit list / dict / attribute,
    then fall back to the workspace's ``repository_items`` count minus
    the failure count.
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
    # Fallback: assume every item in scope was published when fabric-cicd
    # returned a non-exception, non-introspectable shape.
    #
    # fabric-cicd 1.0.x exposes ``repository_items`` as a NESTED dict
    # ``{item_type: {item_name: Item}}`` -- ``len()`` on that returns
    # the count of item-type buckets, not the count of items. Walk one
    # level deeper when the value is a dict-of-dicts so a 2-Notebook
    # publish reports 2 (not 1). The ``isinstance(v, dict)`` guard
    # tolerates the older flat-dict shape (``{item_name: Item}``) for
    # forward-compat with fabric-cicd builds that may flatten the
    # structure.
    items = getattr(target_workspace, "repository_items", None)
    total = 0
    if isinstance(items, dict):
        try:
            total = sum(len(bucket) if isinstance(bucket, dict) else 1 for bucket in items.values())
        except TypeError:
            total = 0
    elif items is not None:
        try:
            total = len(items)
        except TypeError:
            total = 0
    return max(total - failed, 0)


def _extract_failed_item(exc: Exception, candidates: list[str]) -> str | None:
    """Best-effort identification of a candidate item named in ``str(exc)``.

    Walks the candidate list (``"<display_name>.<type>"`` strings) and
    returns the first one that appears verbatim in the exception text.
    Returns ``None`` if no candidate matches -- the caller documents this
    as the documented best-effort path (runbook §1.3).

    The match is a literal substring check rather than a regex so the
    function is robust to fabric-cicd 1.x -> 2.x exception-shape drift
    (Pitfall 3 / D-17-05 deferred note).
    """
    text = str(exc)
    for candidate in candidates:
        if candidate in text:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def publish_absent_items(
    *,
    workspace_id: str,
    environment: str | None,
    staging_dir: Path,
    absent_items: list[SyncItem],
    item_type_in_scope: list[str],
    parameters_path: Path,
    token_provider: TokenProvider,
    bulk: bool = False,
    max_workers: int = 4,
) -> PublishResult:
    """Publish the absent subset of a sync manifest via fabric-cicd.

    Parameters
    ----------
    workspace_id:
        Target Fabric workspace GUID.
    environment:
        ``parameters.yml`` environment label (e.g. ``"DEV"``). May be
        ``None`` when ``parameters.yml`` only carries the ``_ALL_``
        wildcard (D-17-02 -- the CLI layer enforces the multi-env
        requirement BEFORE calling this helper).
    staging_dir:
        The staging tree built by :func:`sigantry_core.sync.apply.apply_sync`.
        Lifetime is the caller's responsibility (D-17-07): the staging
        directory MUST exist for the duration of this call. We validate
        ``is_dir()`` up-front so a stale tempdir surfaces as a typed
        :class:`SyncPublishError` rather than a confusing
        ``FileNotFoundError`` deep inside fabric-cicd.
    absent_items:
        The set of manifest items to publish via the fabric-cicd
        ``items_to_include`` filter. Normally the subset NOT yet present
        in the workspace (first-time publish). When the caller opts into
        ``republish_existing`` (D-17-10) this is the FULL manifest --
        fabric-cicd matches each item to the workspace by
        ``(display_name, type)`` and ``updateDefinition``s the ones
        already present, so the name is historical, not a precondition.
        Empty list short-circuits without invoking fabric-cicd.
    item_type_in_scope:
        fabric-cicd selective-deploy scope list (e.g. ``["Notebook",
        "DataPipeline"]``). Computed by the caller from
        ``absent_items`` types.
    parameters_path:
        SUBSTITUTED ``parameters.yml`` tempfile path (PR #54). This
        helper does NOT re-substitute ``$ENV:`` refs.
    token_provider:
        :class:`TokenProvider` whose ``get_credential()`` is forwarded
        to ``FabricWorkspace(token_credential=...)``.

    Returns
    -------
    PublishResult
        Structured outcome -- see the dataclass docstring.

    Raises
    ------
    SyncPublishError
        If the staging directory is missing (D-17-07) or, in some
        implementations, on full-failure paths where the helper would
        otherwise return ``outcome="failed"``. Callers should catch
        :class:`SyncPublishError` AND inspect ``result.outcome`` --
        both surfaces are valid failure indicators per the contract.
    """
    # 1. Empty short-circuit: skip fabric-cicd entirely.
    if not absent_items:
        return PublishResult(outcome="succeeded", published_items=[], failed_item=None)

    # 2. Staging-directory existence guard (D-17-07).
    if not staging_dir.is_dir():
        raise SyncPublishError(
            f"Staging directory missing: {staging_dir}. "
            f"Sync engine staging tempdir was cleaned prematurely "
            f"(see ADR-0012 / runbook §1.3)."
        )

    # 3. Append both fabric-cicd feature flags (D-17-09).
    # Without BOTH flags, fabric-cicd 1.x (verified through 1.3.0)
    # silently ignores the ``items_to_include`` kwarg and publishes the
    # entire staging tree. Flag-name canary:
    # tests/sigantry_core/deploy/test_fabric_cicd_contract.py.
    append_feature_flag("enable_experimental_features")
    append_feature_flag("enable_items_to_include")

    # 4. Build items_to_include filter.
    items_to_include = [f"{it.display_name}.{it.type}" for it in absent_items]

    # 5. Construct FabricWorkspace handing the staging tree + substituted
    # parameters.yml to fabric-cicd. ``parameter_file_path`` is str() of
    # the substituted tempfile path; fabric-cicd reads it lazily during
    # ``publish_all_items``, which is why D-17-07 mandates the caller
    # keep both directories alive through the publish call.
    #
    # fabric-cicd 1.0.x rejects ``environment=None`` via
    # ``validate_data_type("string", "environment", input_value)`` --
    # its constructor defaults to the literal string ``"N/A"``. When
    # the caller (``apply_sync``) passes ``environment=None`` (the
    # ``_ALL_``-only parameters.yml path per D-17-02), translate to the
    # upstream-native sentinel here so the surface stays operator-
    # facing (``--environment`` optional) without leaking the upstream
    # convention into the engine helper signature.
    fabric_cicd_environment = environment if environment is not None else "N/A"
    target_workspace = FabricWorkspace(
        workspace_id=workspace_id,
        environment=fabric_cicd_environment,
        repository_directory=str(staging_dir),
        item_type_in_scope=item_type_in_scope,
        token_credential=token_provider.get_credential(),
        parameter_file_path=str(parameters_path),
    )

    # 6. Publish items. If bulk=True, run parallel worker pool for multi-item acceleration.
    if bulk and len(items_to_include) > 1:
        import concurrent.futures

        succeeded_items: list[str] = []
        failed_item: str | None = None

        def _publish_single(spec: str) -> tuple[str, bool]:
            ws = FabricWorkspace(
                workspace_id=workspace_id,
                environment=fabric_cicd_environment,
                repository_directory=str(staging_dir),
                item_type_in_scope=item_type_in_scope,
                token_credential=token_provider.get_credential(),
                parameter_file_path=str(parameters_path),
            )
            try:
                publish_all_items(ws, items_to_include=[spec])
                return (spec, True)
            except Exception:
                return (spec, False)

        workers = min(max_workers, len(items_to_include))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_publish_single, item) for item in items_to_include]
            for fut in concurrent.futures.as_completed(futures):
                spec, ok = fut.result()
                if ok:
                    succeeded_items.append(spec)
                else:
                    if failed_item is None:
                        failed_item = spec

        if failed_item is not None:
            outcome = "partial-failure" if succeeded_items else "failed"
            return PublishResult(
                outcome=outcome,
                published_items=succeeded_items,
                failed_item=failed_item,
            )
        return PublishResult(
            outcome="succeeded",
            published_items=succeeded_items,
            failed_item=None,
        )

    try:
        publish_result: Any = publish_all_items(
            target_workspace,
            items_to_include=items_to_include,
        )
    except Exception as exc:
        # Best-effort failed-item extraction (Pitfall 3 / runbook §1.3).
        failed_item = _extract_failed_item(exc, items_to_include)
        # Determine partial vs. full failure. fabric-cicd 1.0.x doesn't
        # expose a stable "items succeeded so far" attribute on the
        # exception, so unless we extracted a failed_item that is NOT
        # the first item in the list, treat as full failure. This is
        # defensive -- the live integration test in 17-04 will refine
        # this if fabric-cicd 1.x stabilises a richer exception shape.
        if failed_item is not None and items_to_include[0] != failed_item:
            outcome = "partial-failure"
            # Items prior to the failed one are assumed succeeded.
            failed_index = items_to_include.index(failed_item)
            published_items = items_to_include[:failed_index]
        else:
            outcome = "failed"
            published_items = []

        # Note: we deliberately DO NOT re-raise here on partial-failure
        # / failed -- the caller (apply_sync) needs the structured
        # PublishResult to build the combined DeployRecord with the
        # appropriate outcome. Audit-2026-05-07 W1.5: prior to remediation
        # the bounded SyncPublishError was constructed and immediately
        # discarded, leaving operators with NO log record explaining why
        # publish failed. We now emit a structured WARNING with the same
        # bounded fields (outcome / failed_item / exc_type) so triage off
        # the audit ledger has somewhere to land.
        # (T-17-05) bounded message; no str(exc) leakage by design.
        bounded_message = (
            f"fabric-cicd publish failed: outcome={outcome}, "
            f"failed_item={failed_item or '<unknown>'}. "
            f"See stderr trace and runbook §1.3."
        )
        logger.warning(
            "sync_publish_failed outcome=%s failed_item=%s exc_type=%s message=%s",
            outcome,
            failed_item or "<unknown>",
            type(exc).__name__,
            bounded_message,
        )
        # Audit-2026-05-08 review follow-up (WR-09): the dead
        # ``_ = SyncPublishError(bounded_message).with_traceback(None)``
        # construct-and-discard line previously lived here. It was
        # documented as "for type-check / future re-raise wiring" but
        # it raised no exception, was never re-raised, and the
        # bounded_message is already on the warning logger above.
        # Removed to keep the failure path single-purposed.
        return PublishResult(
            outcome=outcome,
            published_items=published_items,
            failed_item=failed_item,
        )

    # 7. Clean publish path: derive outcome from the result shape.
    failed_count = _count_failed(publish_result)
    succeeded_count = _count_succeeded(publish_result, target_workspace, failed_count)
    n_absent = len(absent_items)

    if failed_count == 0 and succeeded_count >= n_absent:
        return PublishResult(
            outcome="succeeded",
            published_items=list(items_to_include),
            failed_item=None,
        )
    if succeeded_count == 0:
        return PublishResult(
            outcome="failed",
            published_items=[],
            failed_item=None,
        )
    # 0 < succeeded < N -> partial-failure.
    return PublishResult(
        outcome="partial-failure",
        published_items=list(items_to_include[:succeeded_count]),
        failed_item=(items_to_include[succeeded_count] if succeeded_count < n_absent else None),
    )


__all__ = (
    "PublishResult",
    "SyncPublishError",
    "publish_absent_items",
)


# Defensive import gate: ``fabric_cicd`` is imported at module top so the
# fabric-cicd quarantine grep registers this file under
# ``sigantry_core/deploy/``. Without an explicit reference somewhere in
# the module body, lint tools may rewrite the import as removed.
_: tuple = (fabric_cicd,)
