"""Folder blueprint catalog for ``sigantry workspace bootstrap``.

A blueprint is a named folder layout an operator can reference by string
in ``workspace.yml`` instead of enumerating folders by hand.

Each blueprint is a list of top-level folder display-names. Sub-folders
are not first-class: bootstrap creates every folder, from a blueprint or
from an explicit ``folders.list``, at the workspace root.

Adding a blueprint:

1. Append a new entry to :data:`BLUEPRINTS` keyed by a snake_case name.
2. Add a one-line docstring summary to the entry.
3. Add an entry-existence test in
   ``tests/sigantry_core/workspace/test_blueprints.py``.

Blueprints are intentionally small + readable here rather than loaded from
YAML on disk -- the catalog is part of the toolkit's documented surface.
Per-workspace override is via an explicit ``folders.list`` in
``workspace.yml`` (see :mod:`sigantry_core.workspace.bootstrap`).
"""

from __future__ import annotations

from typing import Final

#: Numbered default layout. Order is preserved, and the two-digit prefixes
#: keep the Fabric workspace UI listing folders top-to-bottom in
#: pipeline-flow order (control -> intake -> ... -> reporting), with shared
#: code and retired items last.
#:
#: These names replace the ones sigantry 1.0.0 shipped (changed in 1.0.1).
#: Bootstrap never renames or deletes a folder, so it warns when it would lay
#: this layout out beside a workspace's existing folders; an operator keeps
#: an existing layout by listing it under ``folders.list`` (see
#: :mod:`sigantry_core.workspace.bootstrap`).
_MINIMAL_STARTER: Final[tuple[str, ...]] = (
    "00_control",
    "10_intake",
    "20_storage",
    "30_transform",
    "40_semantic",
    "50_reporting",
    "90_shared",
    "99_retired",
)

#: Light alias for operators who prefer the "medallion" framing literal.
_MEDALLION: Final[tuple[str, ...]] = _MINIMAL_STARTER


BLUEPRINTS: Final[dict[str, tuple[str, ...]]] = {
    "minimal_starter": _MINIMAL_STARTER,
    "medallion": _MEDALLION,
}


def get_blueprint(name: str) -> tuple[str, ...]:
    """Return the folder list for blueprint ``name`` or raise ``KeyError``.

    The error message lists every available blueprint so the operator
    sees the full catalog rather than a bare key name.
    """
    if name not in BLUEPRINTS:
        available = ", ".join(sorted(BLUEPRINTS))
        raise KeyError(
            f"unknown blueprint {name!r}; choose one of: {available}. "
            "Or omit `folders.blueprint` and pass an explicit `folders.list`."
        )
    return BLUEPRINTS[name]


__all__ = ["BLUEPRINTS", "get_blueprint"]
