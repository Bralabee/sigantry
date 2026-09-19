"""Per-type packager seam (D-03 -- runtime-checkable Protocol).

The :class:`Packager` Protocol is the structural contract every packager
implementation in :mod:`sigantry_core.sync.packagers` satisfies. The
Phase 13 packager registry
(:data:`sigantry_core.sync.packagers.PACKAGER_REGISTRY`) is keyed by
canonical Fabric item-type name (PascalCase, matching
``fabric_cicd.constants.ItemType``); values are objects that satisfy
this Protocol.

Same pattern Phase 11's :class:`TokenProviderProtocol` uses -- the
registry-load gate (``isinstance(p, Packager)``) catches signature
divergence at CI time rather than at first-use, so any future packager
that drifts from the locked ``pack(...)`` signature fails the
``test_packager_registry_isinstance_runtime_checkable`` assertion in
``tests/sync/test_packagers_generic.py``.

Locked decision references:

- **D-03** (Council A): Packager is a runtime-checkable Protocol with
  the uniform ``pack(source, *, display_name, target_folder,
  logical_id, staging_dir) -> Path`` signature. Selection by item type
  via ``PACKAGER_REGISTRY``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Packager(Protocol):
    """Per-type packager seam (D-03).

    Implementations consume operator-supplied source artefacts (a raw
    ``.ipynb`` file for :class:`NotebookPackager`, or an arbitrary
    file/directory for :class:`GenericPackager`) and emit a staged
    ``<displayName>.<Type>/`` folder under ``staging_dir`` carrying the
    fabric-cicd schema 2.0 ``.platform`` payload plus the item content.

    The signature is intentionally narrow:

    - ``source``: path to the operator's authoring location (never
      mutated; read-only).
    - ``display_name``: validated upstream by :class:`SyncManifest`
      (Plan 13-01 enforces banned-character / depth / control-character
      rules before any packager sees the value).
    - ``target_folder``: optional Fabric-side folder path (e.g.
      ``"/raw"`` -- leading slash optional; SYNC-06 folder-less types
      are forced to ``"/"`` upstream by ``SyncManifest.normalised()``).
    - ``logical_id``: caller-supplied UUID4 (manifest-pinned) or
      ``None`` to delegate to the packager's sidecar persistence (D-11
      / D-12).
    - ``staging_dir``: scratch directory the caller owns (one fresh
      tempdir per ``sync apply`` invocation in Plan 13-04). Each
      ``pack()`` call creates a fresh ``<displayName>.<Type>/`` subtree
      and refuses to overwrite an existing one.

    The return value is the absolute path to the newly-created staged
    item folder.
    """

    def pack(
        self,
        source: Path,
        *,
        display_name: str,
        target_folder: str,
        logical_id: str | None,
        staging_dir: Path,
    ) -> Path:
        """Pack ``source`` into a staged item-folder under ``staging_dir``.

        Returns the path to the staged ``<displayName>.<Type>/``
        directory.
        """
        ...


__all__ = ("Packager",)
