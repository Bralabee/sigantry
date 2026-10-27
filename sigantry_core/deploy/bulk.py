"""Dependency-ordered waves for ``--bulk`` publishing.

``deploy run --bulk`` and ``sync apply --with-publish --bulk`` publish items
concurrently, one fabric-cicd ``publish_all_items`` call per unit. fabric-cicd
resolves a reference to another item only once that item is deployed, so the
units are grouped into waves, one per item type, in the order fabric-cicd
itself publishes types: a pipeline is never published before its notebook.
"""

from __future__ import annotations

# Item types that can reference items of their OWN type (a pipeline that
# invokes another pipeline). fabric-cicd orders such references only within
# one publish call, so bulk publishes each of these types as a single unit.
SELF_REFERENCING_TYPES: frozenset[str] = frozenset({"DataPipeline", "Dataflow"})


def fabric_cicd_type_rank() -> dict[str, int]:
    """Item type -> position in the installed fabric-cicd's serial publish order."""
    try:
        from fabric_cicd import constants as fabric_cicd_constants

        order = fabric_cicd_constants.SERIAL_ITEM_PUBLISH_ORDER
    except (ImportError, AttributeError):  # pragma: no cover - guarded by a contract test
        return {}
    return {getattr(t, "value", str(t)): rank for rank, t in order.items()}


def bulk_publish_waves(candidate_items: list[str]) -> list[list[list[str]]]:
    """Group ``name.Type`` specs into ordered waves of publish units.

    One wave per item type, ordered as fabric-cicd publishes types (types it
    does not list go last, by name). A unit is one spec, or every spec of a
    self-referencing type, which fabric-cicd must order in one call.
    """
    rank = fabric_cicd_type_rank()
    by_type: dict[str, list[str]] = {}
    for spec in candidate_items:
        item_type = spec.rsplit(".", 1)[-1]
        by_type.setdefault(item_type, []).append(spec)
    ordered = sorted(by_type, key=lambda t: (rank.get(t, len(rank) + 1), t))
    waves: list[list[list[str]]] = []
    for item_type in ordered:
        specs = by_type[item_type]
        if item_type in SELF_REFERENCING_TYPES:
            waves.append([specs])
        else:
            waves.append([[spec] for spec in specs])
    return waves
