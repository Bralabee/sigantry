"""Dependency-ordered waves for ``--bulk`` publishing.

``deploy run --bulk`` and ``sync apply --with-publish --bulk`` publish items
concurrently, one fabric-cicd ``publish_all_items`` call per unit. fabric-cicd
resolves a reference to another item only once that item is deployed, so the
units are grouped into waves, one per item type, in the order fabric-cicd
itself publishes types: a pipeline is never published before its notebook.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger("sigantry_core.deploy.bulk")

# Item types that can reference items of their OWN type: a pipeline that
# invokes another pipeline, a dataflow that reads another, a lakehouse shortcut
# to another lakehouse. fabric-cicd orders such references only within one
# publish call (lakehouse shortcuts are published after every lakehouse in the
# call), so bulk publishes each of these types as a single unit.
SELF_REFERENCING_TYPES: frozenset[str] = frozenset({"DataPipeline", "Dataflow", "Lakehouse"})

# fabric-cicd's PublishError text names the items that failed, without type:
# "Failed to publish 2 item(s): ['PL_A', 'PL_B']".
_FAILED_NAMES_RE = re.compile(r"Failed to publish \d+ item\(s\): \[(?P<names>[^\]]*)\]")


@dataclass
class BulkOutcome:
    """Specs of a bulk publish, by what happened to each."""

    published: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_attempted: list[str] = field(default_factory=list)


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
    self-referencing type, which fabric-cicd must order in one call. Duplicate
    specs are dropped. When the installed fabric-cicd does not expose its
    publish order, every spec goes in one unit: a serial publish that
    fabric-cicd orders itself, rather than a guessed order.
    """
    specs = list(dict.fromkeys(candidate_items))
    rank = fabric_cicd_type_rank()
    if not rank:
        logger.warning(
            "bulk_publish_order_unavailable: fabric-cicd exposes no "
            "SERIAL_ITEM_PUBLISH_ORDER; publishing %d item(s) serially",
            len(specs),
        )
        return [[specs]] if specs else []
    by_type: dict[str, list[str]] = {}
    for spec in specs:
        item_type = spec.rsplit(".", 1)[-1]
        by_type.setdefault(item_type, []).append(spec)
    ordered = sorted(by_type, key=lambda t: (rank.get(t, len(rank) + 1), t))
    waves: list[list[list[str]]] = []
    for item_type in ordered:
        type_specs = by_type[item_type]
        if item_type in SELF_REFERENCING_TYPES:
            waves.append([type_specs])
        else:
            waves.append([[spec] for spec in type_specs])
    return waves


def _failed_specs(exc: Exception, unit: list[str]) -> list[str]:
    """The specs of ``unit`` that ``exc`` reports as failed (all of them if unclear).

    fabric-cicd publishes every item of a call before raising, so in a
    multi-item unit the items it does not name were published.
    """
    if len(unit) == 1:
        return list(unit)
    match = _FAILED_NAMES_RE.search(str(exc))
    if match is None:
        return list(unit)
    names = {n.strip().strip("'\"") for n in match.group("names").split(",") if n.strip()}
    failed = [spec for spec in unit if spec.rsplit(".", 1)[0] in names]
    return failed or list(unit)


def run_bulk_waves(
    waves: list[list[list[str]]],
    publish_unit: Callable[[list[str]], None],
    max_workers: int,
) -> BulkOutcome:
    """Publish ``waves`` in order, the units of one wave concurrently.

    ``publish_unit`` publishes one unit and raises on failure. The very first
    unit runs alone: every fabric-cicd publish call also creates the
    workspace's missing folders, so concurrent first calls would race to
    create the same folder. A failed wave stops the later ones; their specs
    are reported as not attempted.
    """
    outcome = BulkOutcome()

    def _run(unit: list[str]) -> tuple[list[str], list[str]]:
        try:
            publish_unit(unit)
        except Exception as exc:
            failed = _failed_specs(exc, unit)
            logger.error("bulk_publish_unit_failed specs=%s failed=%s", unit, failed, exc_info=True)
            return [s for s in unit if s not in failed], failed
        return list(unit), []

    first = True
    for wave in waves:
        if outcome.failed:
            outcome.not_attempted.extend(spec for unit in wave for spec in unit)
            continue
        units = list(wave)
        results: list[tuple[list[str], list[str]]] = []
        if first:
            results.append(_run(units.pop(0)))
            first = False
        if units:
            workers = max(1, min(max_workers, len(units)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                results.extend(executor.map(_run, units))
        for published, failed in results:
            outcome.published.extend(published)
            outcome.failed.extend(failed)
    if outcome.not_attempted:
        logger.warning(
            "bulk_publish_stopped: %d item(s) not attempted after a failed wave: %s",
            len(outcome.not_attempted),
            outcome.not_attempted,
        )
    return outcome
