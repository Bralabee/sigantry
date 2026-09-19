"""Per-type packager registry (D-03 -- runtime-checkable seam).

The Phase 13 sync engine resolves a packager per manifest item by
keying :data:`PACKAGER_REGISTRY` on the canonical Fabric item-type
name (PascalCase, matching ``fabric_cicd.constants.ItemType``). Plan
13-04 (apply) imports ``PACKAGER_REGISTRY`` and calls
``PACKAGER_REGISTRY[item.type].pack(...)`` for each item.

Notebook has a dedicated :class:`NotebookPackager` because a raw
``.ipynb`` requires a fixed content filename
(``notebook-content.ipynb``); every other type covered in v3.0 routes
through :class:`GenericPackager` keyed by item type so the
operator-facing registry stays uniform (one packager per ItemType
key).

Lakehouse / Warehouse / SQLDatabase / MLExperiment are out-of-scope
per SPEC; those types are not registered here and a manifest item
referencing them raises ``KeyError`` at registry-resolution time in
Plan 13-04 (translated to a typed ``PackagerError``).

The :class:`Packager` Protocol is ``@runtime_checkable``; the
``test_packager_registry_isinstance_runtime_checkable`` assertion in
``tests/sync/test_packagers_generic.py`` is the CI-time gate that
catches signature divergence before any consumer hits a packager at
runtime.
"""

from __future__ import annotations

from sigantry_core.sync.packagers.generic import GenericPackager
from sigantry_core.sync.packagers.notebook import NotebookPackager
from sigantry_core.sync.protocols import Packager

PACKAGER_REGISTRY: dict[str, Packager] = {
    "Notebook": NotebookPackager(),
    "DataPipeline": GenericPackager(item_type="DataPipeline"),
    "SemanticModel": GenericPackager(item_type="SemanticModel"),
    "Report": GenericPackager(item_type="Report"),
    "SparkJobDefinition": GenericPackager(item_type="SparkJobDefinition"),
}

__all__ = (
    "PACKAGER_REGISTRY",
    "GenericPackager",
    "NotebookPackager",
    "Packager",
)
