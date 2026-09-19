"""Folder blueprint catalog for ``sigantry workspace bootstrap``.

A blueprint is a named folder layout an operator can reference by string
in ``workspace.yml`` instead of enumerating folders by hand. Patterns are
lifted from ``usf_fabric_cli_cicd``'s blueprint catalog (see
``docs/RELATED-WORK.md`` §4 item 1) and the numbered medallion convention
they validated against live multi-customer workspaces (§4 item 2).

Each blueprint is a list of top-level folder display-names. Sub-folders
are not yet first-class -- the medallion convention is one-level-deep by
design (operator-friendly default; deeper nesting is opt-out via explicit
``folders:`` lists).

Adding a blueprint:

1. Append a new entry to :data:`BLUEPRINTS` keyed by a snake_case name.
2. Add a one-line docstring summary to the entry.
3. Add an entry-existence test in
   ``tests/sigantry_core/workspace/test_blueprints.py``.

Blueprints are intentionally small + readable here rather than loaded from
YAML on disk -- the catalog is part of the toolkit's documented surface.
Per-customer override is via the explicit ``folders:`` list in
``workspace.yml`` (see :mod:`sigantry_core.workspace.bootstrap`).
"""

from __future__ import annotations

from typing import Final

#: Numbered-medallion default. The exact convention validated by
#: ``usf_fabric_cli_cicd`` against Ricoh + JToye customer workspaces.
#: Order is preserved so the Fabric workspace UI lists folders top-to-bottom
#: in pipeline-flow order (orchestrate -> ingest -> ... -> visualise).
_MINIMAL_STARTER: Final[tuple[str, ...]] = (
    "000 Orchestrate",
    "100 Ingest",
    "200 Store",
    "300 Prepare",
    "400 Model",
    "500 Visualize",
    "999 Libraries",
    "Archive",
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
