"""``sync pull`` -- workspace -> local IaC-fication (Phase 13 / SYNC-05).

Execution flow (D-20):

1. Validate ``--into <dir>``: refuse non-empty target without
   ``force=True`` (D-21). Create the directory (and any missing
   parents) if absent.
2. Call :func:`sigantry_core.sync.snapshot.snapshot_workspace` to
   obtain the topology map -- folders + items + the
   ``folder_path_index`` and ``item_to_folder`` lookups.
3. For each item in scope (filtered by ``item_types`` + the per-type
   :data:`_DEFINITION_ENDPOINTS` mapping), POST to
   ``/v1/workspaces/{ws}/{url_segment}/{item_id}/getDefinition``
   passing ``format=ipynb`` for Notebooks (Council A) and the default
   ``fabricGitSource`` for the other supported types (Council B).
4. Walk ``body["definition"]["parts"]``; base64-decode each
   ``InlineBase64`` payload and write it to disk under
   ``<into>/<target_folder>/<display_name>/<part_path>``. Apply LF
   normalisation on text suffixes (``.platform`` / ``.py`` / ``.json``
   / ``.ipynb`` / ``.tmdl`` / ``.bim``) so a subsequent ``sync apply``
   round-trip stays byte-deterministic.
5. Emit a ``sync.yml`` adjacent to the pulled tree with the workspace's
   actual topology mirrored 1:1 -- ``logical_id`` is set to the
   workspace's GUID for each item so a follow-up ``sync apply`` is a
   no-op (D-22 round-trip preservation invariant).

D-15 (load-bearing): ``pull_workspace`` does NOT emit a
:class:`DeployRecord`. Pull is read-only; nothing changed in the
workspace. The unit test in ``tests/sync/test_pull.py`` monkeypatches
the audit emitter to raise on call as the falsifiability gate.

REST endpoints follow the Microsoft Learn ``Item Definition`` family
(Council A verified). The HTTP method is ``POST`` -- this matches the
canonical
``learn.microsoft.com/rest/api/fabric/articles/item-management/definitions``
shape ("POST .../getDefinition action with optional `format` query
parameter"). The 202 Accepted long-running-operation flow is handled
transparently by :meth:`sigantry_core.client.base.BaseRestClient.send_lro`.

D-31: every REST call routes through
:class:`sigantry_core.client.FabricRestClient` using
``TokenProvider.from_defaults()``. There is no direct ``httpx`` usage
in this module.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from sigantry_core.client import FabricRestClient
from sigantry_core.sync.errors import (
    PullDefinitionDecodeError,
    PullDefinitionFetchError,
    PullDefinitionPathTraversalError,
    PullTargetNotEmptyError,
)
from sigantry_core.sync.manifest import SyncItem, SyncManifest
from sigantry_core.sync.snapshot import WorkspaceSnapshot, snapshot_workspace
from sigantry_core.workspace.items import Item

logger = logging.getLogger("sigantry_core.sync.pull")


#: Default in-scope item types (D-20). Notebook uses ``format=ipynb``;
#: the other four use the default ``fabricGitSource`` shape. Lakehouse,
#: Warehouse, SQLDatabase, and MLExperiment are out of scope at v3.0
#: (their definitions are shell-only -- the data lives in OneLake / SQL
#: rather than the ``.platform`` payload). Items whose ``type`` falls
#: outside the registry are SKIPPED with an INFO log so a single mixed
#: workspace does not error mid-pull.
_DEFAULT_PULL_TYPES: Final[tuple[str, ...]] = (
    "Notebook",
    "DataPipeline",
    "SemanticModel",
    "Report",
    "SparkJobDefinition",
)

#: Per-type REST endpoint mapping. Each value is
#: ``(url_segment, query_format)``. ``query_format`` is the value of the
#: ``format`` query parameter (``"ipynb"`` for Notebook, ``None`` for
#: the others -> default ``fabricGitSource``).
_DEFINITION_ENDPOINTS: Final[dict[str, tuple[str, str | None]]] = {
    "Notebook": ("notebooks", "ipynb"),
    "DataPipeline": ("dataPipelines", None),
    "SemanticModel": ("semanticModels", None),
    "Report": ("reports", None),
    "SparkJobDefinition": ("sparkJobDefinitions", None),
}

#: File suffixes that are text-mode (we LF-normalise so pull -> apply
#: round-trips stay byte-deterministic on Windows operators).
_LF_TEXT_SUFFIXES: Final[tuple[str, ...]] = (
    ".platform",
    ".py",
    ".json",
    ".ipynb",
    ".tmdl",
    ".bim",
)


@dataclass(frozen=True, slots=True)
class SyncPullReport:
    """Operator-facing summary of one ``pull_workspace`` invocation.

    The ``snapshot`` field is the live :class:`WorkspaceSnapshot` taken
    at the start of the pull -- callers (e.g. the live round-trip
    integration test) can compare against it without re-fetching.
    """

    workspace_id: str
    into: Path
    items_pulled: int
    sync_yml_path: Path
    snapshot: WorkspaceSnapshot


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


def _normalise_lf(data: bytes) -> bytes:
    """Project-standard LF normalisation (mirrors the packagers).

    ``CRLF`` -> ``LF``; bare ``CR`` -> ``LF``. Idempotent on
    already-LF content.
    """
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _validate_target(into: Path, *, force: bool) -> Path:
    """Refuse non-empty ``into`` without ``force`` (D-21).

    A target that does not exist is fine -- we'll ``mkdir(parents=True)``
    further down. A target that exists and is empty is also fine. Only
    a non-empty existing directory triggers
    :class:`PullTargetNotEmptyError`; the caller chose ``--force`` to
    accept the clobber semantics.

    WR-04 (REVIEW.md): resolve ``into`` through symlinks BEFORE the
    emptiness check so the function reports on the actual target, not
    the symlink itself. Combined with the CR-01 fix this prevents a
    confused-deputy write through a symlink (e.g.
    ``--into ~/symlink-to-home`` would previously silently
    ``mkdir(parents=True, exist_ok=True)`` against the symlink and
    every subsequent ``write_bytes`` would punch through to the symlink
    target). Returns the resolved path so the caller can use it for
    every downstream filesystem operation.
    """
    # Resolve through symlinks (strict=False -- the path may not exist
    # yet, which is fine). Behavioural note: a symlink pointing to a
    # non-existent target resolves to that non-existent target and the
    # ``not into_resolved.exists()`` branch below handles it the same
    # as any absent ``--into``.
    into_resolved = into.resolve(strict=False)
    if not into_resolved.exists():
        return into_resolved
    if not into_resolved.is_dir():
        # An existing file at the path (or a symlink resolving to one)
        # is a harder error than a non-empty dir; treat it the same way
        # (refuse without force). When ``into`` differs from
        # ``into_resolved`` (i.e. ``into`` was a symlink), the message
        # surfaces both so the operator can see what was actually being
        # written through.
        if not force:
            via_symlink = f" (via symlink {into})" if str(into) != str(into_resolved) else ""
            raise PullTargetNotEmptyError(
                f"sync pull target exists and is not a directory: {into_resolved}{via_symlink}",
                target=str(into),
            )
        return into_resolved
    has_entries = any(into_resolved.iterdir())
    if has_entries and not force:
        raise PullTargetNotEmptyError(
            f"sync pull target {into_resolved} is not empty; pass --force to overwrite",
            target=str(into),
        )
    return into_resolved


def _resolve_folder_path_from_id(snapshot: WorkspaceSnapshot, folder_id: str | None) -> str:
    """Reverse-lookup a workspace folder path from its GUID.

    Audit-2026-05-07 W4.1: reverses through :attr:`WorkspaceSnapshot.id_to_path`
    (a ``functools.cached_property`` on the snapshot model) instead of an
    O(N) scan over ``folder_path_index``. The reverse map is computed
    once per snapshot instance; subsequent calls are O(1).

    Returns ``"/"`` for ``None`` (item lives at workspace root) or for
    an unknown folder_id (logs a WARNING -- INTROSPECT-02 says routing
    joins on ``folder_id``; an item carrying a folder_id missing from
    the snapshot is a Fabric race condition and we surface it).
    """
    if folder_id is None:
        return "/"
    path = snapshot.id_to_path.get(folder_id)
    if path is not None:
        return path
    logger.warning(
        "pull_unknown_folder_id folder_id=%s; placing item at workspace root",
        folder_id,
    )
    return "/"


def _local_source_dir(into: Path, target_folder: str, display_name: str) -> Path:
    """Compute the per-item local source directory.

    ``<into>/<target_folder.lstrip('/')>/<display_name>/`` -- the
    ``target_folder`` is mirrored verbatim so a follow-up ``sync apply``
    re-discovers the same topology.
    """
    rel = target_folder.lstrip("/") if target_folder != "/" else ""
    return into / rel / display_name


def _fetch_item_definition(
    client: FabricRestClient,
    *,
    workspace_id: str,
    item: Item,
    url_segment: str,
    query_format: str | None,
) -> dict[str, Any]:
    """POST ``getDefinition``; unwrap 200 / 202 transparently.

    ``send_lro`` (Phase 2 Pattern 2) handles both the synchronous 200
    and the asynchronous 202 + Location-poll flow uniformly -- we get
    back the eventual 200 body either way. Failures bubble up as
    :class:`PullDefinitionFetchError` with the original cause preserved.
    """
    path = f"/v1/workspaces/{workspace_id}/{url_segment}/{item.id}/getDefinition"
    params: dict[str, Any] | None = {"format": query_format} if query_format else None
    try:
        body = client.send_lro("POST", path, params=params)
    except Exception as exc:
        raise PullDefinitionFetchError(
            f"Get{item.type}Definition failed for "
            f"workspace={workspace_id} item_id={item.id} "
            f"display_name={item.display_name!r}: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise PullDefinitionFetchError(
            f"Get{item.type}Definition returned non-dict body for "
            f"item_id={item.id}: type={type(body).__name__}"
        )
    return body


def _write_definition_parts(
    body: dict[str, Any],
    *,
    source_dir: Path,
    item: Item,
) -> None:
    """Walk ``definition.parts`` and write each base64 payload to disk.

    Each part has the shape
    ``{"path": "<rel>", "payload": "<b64>", "payloadType": "InlineBase64"}``.
    We tolerate parts whose ``payloadType`` is missing (treat as
    ``InlineBase64`` -- Fabric's documented default per Council A); a
    ``payloadType`` of anything else is logged at WARNING and skipped.
    """
    definition = body.get("definition")
    if not isinstance(definition, dict):
        raise PullDefinitionFetchError(
            f"Get{item.type}Definition response missing 'definition' object for item_id={item.id}"
        )
    parts = definition.get("parts")
    if not isinstance(parts, list):
        raise PullDefinitionFetchError(
            f"Get{item.type}Definition response missing 'definition.parts' "
            f"list for item_id={item.id}"
        )

    source_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        if not isinstance(part, dict):
            logger.warning(
                "pull_skipping_non_dict_part item_id=%s part=%r",
                item.id,
                part,
            )
            continue
        rel_path_raw = part.get("path")
        payload_b64 = part.get("payload")
        payload_type = part.get("payloadType")
        if not isinstance(rel_path_raw, str) or not isinstance(payload_b64, str):
            logger.warning(
                "pull_skipping_malformed_part item_id=%s path=%r",
                item.id,
                rel_path_raw,
            )
            continue
        if payload_type not in (None, "InlineBase64"):
            logger.warning(
                "pull_unsupported_payload_type item_id=%s path=%s payload_type=%s",
                item.id,
                rel_path_raw,
                payload_type,
            )
            continue

        # WR-02: surface malformed base64 with a typed error carrying
        # the offending item identity + part path; otherwise a single
        # bad payload aborts the whole pull with a bare
        # ``binascii.Error`` and ``PullTargetNotEmptyError`` then refuses
        # every retry until ``--force``.
        try:
            decoded = base64.b64decode(payload_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.warning(
                "pull_definition_decode_failed item_id=%s display_name=%s path=%s err=%s",
                item.id,
                item.display_name,
                rel_path_raw,
                exc,
            )
            raise PullDefinitionDecodeError(
                f"Get{item.type}Definition response carried malformed base64 "
                f"for workspace_id={item.workspace_id} item_id={item.id} "
                f"display_name={item.display_name!r} part_path={rel_path_raw!r}: "
                f"{exc}"
            ) from exc
        # LF-normalise text-suffix files so Windows operators can
        # round-trip pull -> commit -> apply without spurious diffs.
        rel_path = Path(rel_path_raw)
        if rel_path.suffix in _LF_TEXT_SUFFIXES:
            decoded = _normalise_lf(decoded)

        # CR-01: enforce real path-traversal containment. ``rel_path``
        # comes from a Fabric REST response; a malicious / compromised
        # response carrying ``"path": "../../etc/passwd"`` (or an
        # absolute path / Windows drive prefix) would otherwise resolve
        # OUTSIDE ``source_dir`` and write a confused-deputy file at the
        # operator's expense. We resolve both ends and assert the
        # destination stays inside ``source_dir`` BEFORE creating any
        # parent dirs or writing bytes; failure raises a typed error so
        # the operator can investigate.
        source_dir_resolved = source_dir.resolve()
        try:
            dest_resolved = (source_dir / rel_path).resolve()
        except (OSError, RuntimeError) as exc:
            # ``resolve()`` may raise on symlink loops or pathological
            # inputs; treat that as a refusal too.
            logger.warning(
                "pull_definition_path_resolve_failed item_id=%s path=%r err=%s",
                item.id,
                rel_path_raw,
                exc,
            )
            raise PullDefinitionPathTraversalError(
                f"Get{item.type}Definition response part path could not be "
                f"safely resolved for workspace_id={item.workspace_id} "
                f"item_id={item.id} display_name={item.display_name!r} "
                f"part_path={rel_path_raw!r}: {exc}"
            ) from exc
        try:
            dest_resolved.relative_to(source_dir_resolved)
        except ValueError:
            logger.warning(
                "pull_rejecting_path_traversal item_id=%s display_name=%s "
                "path=%r resolved=%s source_dir=%s",
                item.id,
                item.display_name,
                rel_path_raw,
                dest_resolved,
                source_dir_resolved,
            )
            raise PullDefinitionPathTraversalError(
                f"Get{item.type}Definition response carried out-of-tree "
                f"part path for workspace_id={item.workspace_id} "
                f"item_id={item.id} display_name={item.display_name!r} "
                f"part_path={rel_path_raw!r}; resolved to "
                f"{dest_resolved} which is outside {source_dir_resolved}"
            ) from None
        # Containment confirmed -- safe to create parent dirs and write.
        dest_resolved.parent.mkdir(parents=True, exist_ok=True)
        dest_resolved.write_bytes(decoded)


def _build_emitted_manifest(
    *,
    snapshot: WorkspaceSnapshot,
    items_in_scope: list[Item],
    into: Path,
) -> SyncManifest:
    """Build the ``sync.yml`` payload mirroring workspace topology.

    Per-item fields:

    * ``local_path`` -- relative path from ``into`` to the item's
      source dir, e.g. ``Path("MyFolder/MyNb")``.
    * ``type`` -- canonical item type (matches the workspace).
    * ``target_folder`` -- the path string from
      ``snapshot.folder_path_index`` (e.g. ``"/MyFolder"`` or ``"/"``).
    * ``display_name`` -- the workspace's display name verbatim.
    * ``logical_id`` -- THE WORKSPACE GUID (D-22 round-trip
      preservation invariant).

    The optional ``folders`` preservation set carries every workspace
    folder path so a subsequent ``sync apply`` does not auto-cleanup
    operator-created subfolders.
    """
    new_items: list[SyncItem] = []
    for item in items_in_scope:
        target_folder = _resolve_folder_path_from_id(snapshot, item.folder_id)
        # Per ``_local_source_dir`` -- the source dir is
        # ``<target_folder>/<display_name>``, where target_folder is
        # the leading ``/`` -stripped form (or "" at the root).
        rel_target = target_folder.lstrip("/") if target_folder != "/" else ""
        source_dir_rel = (
            Path(rel_target) / item.display_name if rel_target else Path(item.display_name)
        )
        # NotebookPackager.pack requires source to be an ``.ipynb`` file
        # (notebook.py:111); for the round-trip apply (D-22) to succeed,
        # the manifest's ``local_path`` must therefore point at the
        # ``notebook-content.ipynb`` payload that pull wrote into the
        # source dir (see ``_write_definition_parts`` Notebook branch).
        # Other v3.0 types (DataPipeline / SemanticModel / Report /
        # SparkJobDefinition) use the GenericPackager which accepts a
        # directory source, so for those types the source dir IS the
        # local_path.
        if item.type == "Notebook":
            local_rel = source_dir_rel / "notebook-content.ipynb"
        else:
            local_rel = source_dir_rel
        new_items.append(
            SyncItem(
                local_path=local_rel,
                type=item.type,
                target_folder=target_folder,
                display_name=item.display_name,
                logical_id=item.id,
            )
        )

    # Sorted snapshot folder paths -- preservation set so apply does not
    # delete folders that exist in the workspace today.
    folders_preservation = sorted(snapshot.folder_path_index.keys())
    return SyncManifest(
        schema_version="1.0.0",
        items=new_items,
        folders=folders_preservation,
    )


def _serialise_manifest(manifest: SyncManifest) -> str:
    """Render a manifest as YAML preserving field order.

    pydantic's ``model_dump(mode="json", exclude_none=True)`` produces
    JSON-friendly primitives (``Path`` -> ``str``); ``yaml.safe_dump``
    with ``sort_keys=False`` then preserves the manifest's natural
    ordering (``schema_version`` -> ``items`` -> ``folders``).
    """
    payload = manifest.model_dump(mode="json", exclude_none=True)
    for item in payload.get("items", []):
        if "local_path" in item and isinstance(item["local_path"], str):
            item["local_path"] = item["local_path"].replace("\\", "/")
    return yaml.safe_dump(payload, sort_keys=False)


def pull_workspace(
    workspace_id: str,
    *,
    into: str | Path,
    item_types: list[str] | None = None,
    force: bool = False,
    client: FabricRestClient | None = None,
) -> SyncPullReport:
    """Pull a workspace's items into a local IaC tree (SYNC-05 / D-20).

    Parameters
    ----------
    workspace_id:
        Fabric workspace GUID.
    into:
        Target directory. Must be empty or absent unless ``force=True``
        (D-21).
    item_types:
        Optional list of canonical item types to pull (e.g.
        ``["Notebook"]``). When ``None`` the default scope
        :data:`_DEFAULT_PULL_TYPES` applies. Items whose type falls
        outside the per-type endpoint registry are SKIPPED with an
        INFO log.
    force:
        Allow ``into`` to be a non-empty directory; existing files
        MAY be overwritten. Operators must opt in explicitly.
    client:
        Optional :class:`FabricRestClient`. When ``None`` the function
        constructs one via :meth:`FabricRestClient.from_defaults` and
        uses it as a context manager so the connection pool closes
        on exit.

    Returns
    -------
    SyncPullReport
        Summary of the pull -- ``items_pulled``, the emitted
        ``sync.yml`` path, and the live :class:`WorkspaceSnapshot`.

    Notes
    -----
    No :class:`DeployRecord` is emitted (D-15 -- pull is read-only).
    The unit test in ``tests/sync/test_pull.py`` monkeypatches the
    audit emitter to raise on call so a regression that introduces an
    audit emission flips the test red.
    """
    # WR-04: resolve ``--into`` through symlinks BEFORE every downstream
    # filesystem operation so the pull writes to the symlink TARGET
    # (resolved real path) rather than punching through a symlink at
    # write time. ``_validate_target`` returns the resolved path; we
    # pass that through to ``_pull_with_client`` so every nested
    # ``mkdir`` / ``write_bytes`` operates on the real path.
    into_path = _validate_target(Path(into), force=force)
    into_path.mkdir(parents=True, exist_ok=True)

    if client is None:
        with FabricRestClient.from_defaults() as owned_client:
            return _pull_with_client(
                workspace_id,
                into_path=into_path,
                item_types=item_types,
                client=owned_client,
            )
    return _pull_with_client(
        workspace_id,
        into_path=into_path,
        item_types=item_types,
        client=client,
    )


def _pull_with_client(
    workspace_id: str,
    *,
    into_path: Path,
    item_types: list[str] | None,
    client: FabricRestClient,
) -> SyncPullReport:
    """Inner pull driver -- assumes ``client`` is already constructed.

    Splitting the own/borrow concern from the algorithm keeps the
    own-or-borrow context manager pattern symmetric with
    ``snapshot_workspace`` / ``apply_sync``.
    """
    snapshot = snapshot_workspace(workspace_id, client=client)

    type_scope: tuple[str, ...] = tuple(item_types) if item_types else _DEFAULT_PULL_TYPES
    type_scope_set = frozenset(type_scope)

    items_in_scope: list[Item] = []
    for item in snapshot.items_by_id.values():
        if item.type not in type_scope_set:
            continue
        if item.type not in _DEFINITION_ENDPOINTS:
            logger.info(
                "pull_skipping_unsupported_type "
                "item_id=%s type=%s display_name=%s reason="
                "no_definition_endpoint_in_v3",
                item.id,
                item.type,
                item.display_name,
            )
            continue
        url_segment, query_format = _DEFINITION_ENDPOINTS[item.type]
        target_folder = _resolve_folder_path_from_id(snapshot, item.folder_id)
        source_dir = _local_source_dir(into_path, target_folder, item.display_name)
        body = _fetch_item_definition(
            client,
            workspace_id=workspace_id,
            item=item,
            url_segment=url_segment,
            query_format=query_format,
        )
        _write_definition_parts(body, source_dir=source_dir, item=item)
        items_in_scope.append(item)

    manifest = _build_emitted_manifest(
        snapshot=snapshot,
        items_in_scope=items_in_scope,
        into=into_path,
    )
    sync_yml_path = into_path / "sync.yml"
    sync_yml_path.write_text(_serialise_manifest(manifest), encoding="utf-8")

    return SyncPullReport(
        workspace_id=workspace_id,
        into=into_path,
        items_pulled=len(items_in_scope),
        sync_yml_path=sync_yml_path,
        snapshot=snapshot,
    )


__all__ = (
    "SyncPullReport",
    "pull_workspace",
)
