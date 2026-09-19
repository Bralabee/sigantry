"""Read-side ledger over the Phase 11 audit jsonl.

The deploy ledger IS ``~/.sigantry/audit/deploys.jsonl`` from Plan 11-03.
Phase 12 (PIPELINE-04) ships only the read-side: ``iter_records``,
``find_by_release_id``, ``diff_records``. NO new write paths;
``emit_deploy_record`` (Plan 11-03) is the only writer.

Defensive parsing: malformed and tampered lines are SKIPPED with WARN
logs, never raised. Detective control posture -- the full ledger
remains traversable even if one line was hand-edited.

Public surface (Plan 12-03 / PIPELINE-04):

- :func:`iter_records` -- yield every verified DeployRecord oldest-first.
- :func:`find_by_release_id` -- return the unique match or ``None``;
  raise ``ValueError`` on duplicate (Pattern 4 invariant).
- :func:`diff_records` -- bucketed ``{added, removed, unchanged}``
  shape that Phase 13's drift JSON will reuse (Pattern 5 / D-06,
  SemVer-committed via tests/release/test_release_diff_cli.py).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from sigantry_core.governance.audit_io import _DEFAULT_AUDIT_DIR
from sigantry_core.release.record import DeployRecord

logger = logging.getLogger("sigantry_core.release.ledger")


def iter_records(*, audit_dir: Path | None = None) -> Iterator[DeployRecord]:
    """Yield every DeployRecord in the ledger, oldest-first.

    Each line is parsed via ``DeployRecord(**json.loads(line))``. Lines
    that fail :meth:`DeployRecord.verify_hash` OR raise on JSON / pydantic
    validation are LOGGED and SKIPPED -- defensive parsing posture. The
    full ledger remains traversable even if one entry was hand-edited.

    Forward-compat (WR-03 review fix): :class:`DeployRecord` is
    ``extra="forbid"``, so a v3.x writer that adds a new additive field
    would otherwise be silently rejected by a v3.0 reader as an
    ``ledger_line_unparseable`` line. To preserve the read surface
    across additive minor schema bumps, on a strict-parse
    :class:`pydantic.ValidationError` we attempt a fallback parse with
    unknown keys filtered out before the model construction. The fallback
    is logged at INFO with a distinct ``ledger_line_forward_compat``
    event so triage can distinguish "newer schema fields present"
    (recoverable) from "tampered / malformed" (skipped).

    Note: a forward-compat record's ``audit_hash`` was computed against
    the FULL payload (including the unknown fields). Stripping unknown
    keys to satisfy the v3.0 model means ``verify_hash()`` will fail
    against the reduced payload, so the record is yielded ONLY if its
    hash still verifies after the strip. Records whose hash does not
    survive the strip are surfaced at WARN as
    ``ledger_line_forward_compat_hash_mismatch`` -- the operator gets the
    detective signal AND the record is skipped (we refuse to yield a
    record we cannot verify). When the v3.x reader catches up the
    record will round-trip cleanly.

    Parameters
    ----------
    audit_dir : Path | None
        Override ``~/.sigantry/audit/`` (used in tests + hermetic CI).
        Default reads from :data:`_DEFAULT_AUDIT_DIR` (Plan 11-03).
    """
    known_fields = set(DeployRecord.model_fields.keys())
    target = audit_dir if audit_dir is not None else _DEFAULT_AUDIT_DIR
    path = target / "deploys.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("ledger_line_unparseable lineno=%d err=%s", lineno, exc)
                continue
            try:
                rec = DeployRecord(**payload)
            except ValidationError as exc:
                # WR-03 fallback: try once with unknown keys stripped
                # before giving up. ``DeployRecord`` is ``extra="forbid"``
                # so any additive field added in a v3.x writer would
                # otherwise be silently dropped by a v3.0 reader as
                # ``ledger_line_unparseable``.
                if not isinstance(payload, dict):
                    logger.warning(
                        "ledger_line_unparseable lineno=%d err=%s",
                        lineno,
                        exc,
                    )
                    continue
                unknown_keys = sorted(set(payload.keys()) - known_fields)
                if not unknown_keys:
                    # ValidationError was NOT due to unknown keys --
                    # genuine schema breakage (missing required field,
                    # wrong type). Fall through to WARN-skip.
                    logger.warning(
                        "ledger_line_unparseable lineno=%d err=%s",
                        lineno,
                        exc,
                    )
                    continue
                trimmed = {k: v for k, v in payload.items() if k in known_fields}
                try:
                    rec = DeployRecord(**trimmed)
                except ValidationError as inner_exc:
                    logger.warning(
                        "ledger_line_unparseable lineno=%d err=%s",
                        lineno,
                        inner_exc,
                    )
                    continue
                # Forward-compat record successfully parsed against the
                # v3.0 schema. The audit_hash was computed against the
                # FULL payload including unknown_keys, so verify_hash()
                # will fail here -- surface the mismatch at WARN with a
                # distinct event so operators can distinguish "newer
                # writer" from "tampered ledger entry".
                if not rec.verify_hash():
                    logger.warning(
                        "ledger_line_forward_compat_hash_mismatch "
                        "lineno=%d release_id=%s unknown_keys=%s",
                        lineno,
                        rec.release_id,
                        unknown_keys,
                    )
                    continue
                logger.info(
                    "ledger_line_forward_compat lineno=%d release_id=%s unknown_keys=%s",
                    lineno,
                    rec.release_id,
                    unknown_keys,
                )
                yield rec
                continue
            if not rec.verify_hash():
                logger.warning(
                    "ledger_line_tampered lineno=%d release_id=%s",
                    lineno,
                    rec.release_id,
                )
                continue
            yield rec


def find_by_release_id(release_id: str, *, audit_dir: Path | None = None) -> DeployRecord | None:
    """Return the first record matching ``release_id``, or ``None``.

    Raises :class:`ValueError` if more than one record matches -- duplicates
    are a defect (release_id should be unique per ledger; the HUMAN-UAT
    runbook documents the composite-id convention to avoid Pitfall 5).
    """
    matches = [r for r in iter_records(audit_dir=audit_dir) if r.release_id == release_id]
    if len(matches) > 1:
        raise ValueError(
            f"Ledger has {len(matches)} records for release_id {release_id!r}; expected at most 1."
        )
    return matches[0] if matches else None


def diff_records(a: DeployRecord, b: DeployRecord) -> dict[str, list[dict[str, str]]]:
    """Return ``{added, removed, unchanged}`` buckets keyed by ``fabric_item_id``.

    The Pattern 5 / D-06 schema. Each entry carries
    ``{logical_name, item_type, fabric_item_id}``. SemVer-committed via
    ``tests/release/test_release_diff_cli.py::test_diff_json_schema``.
    """
    a_set = set(a.fabric_items_changed)
    b_set = set(b.fabric_items_changed)
    return {
        "added": [_to_diff_entry(x) for x in sorted(b_set - a_set)],
        "removed": [_to_diff_entry(x) for x in sorted(a_set - b_set)],
        "unchanged": [_to_diff_entry(x) for x in sorted(a_set & b_set)],
    }


def _to_diff_entry(fabric_item_id: str) -> dict[str, str]:
    """Split ``'<name>.<type>'`` into ``{logical_name, item_type, fabric_item_id}``.

    The ``fabric_item_id`` is preserved verbatim so consumers chaining
    ``jq`` don't have to re-concatenate.
    """
    name, _, item_type = fabric_item_id.rpartition(".")
    return {
        "logical_name": name,
        "item_type": item_type,
        "fabric_item_id": fabric_item_id,
    }


__all__ = (
    "_to_diff_entry",
    "diff_records",
    "find_by_release_id",
    "iter_records",
)
