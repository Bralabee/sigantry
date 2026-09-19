"""argparse entrypoint for the scheduled drift-check templates (Plan 13-06 / DRIFT-03).

The ADO + GHA drift-check workflows
(``templates/schedules/drift-check.yml`` and
``.github/workflows/drift-check.yml``) invoke this module as
``python -m sigantry_core.sync._notify_main --drift-json drift.json``
after ``sigantry diff --output json --fail-on-drift`` has written its
SemVer-pinned JSON output.

Runtime contract:

1. Read ``--drift-json <path>`` (default: ``drift.json``).
2. Read ``--workspace-id <id>`` (default: pulled from
   ``$SIGANTRY_DRIFT_WORKSPACE_ID`` for back-compat with operators who
   set the env var on the notify job).
3. Resolve the sink via :func:`sigantry_core.notifications.sink_from_env`
   (reads ``$SIGANTRY_NOTIFICATION_SINK`` + the per-impl env vars).
   Plan 16-01 migrated the import path from
   ``sigantry_core.sync.notifications`` to ``sigantry_core.notifications``;
   the legacy path remains as a deprecation shim through v3.0.
4. Reconstruct a :class:`DriftReport` from the JSON payload (the wire
   format is a closed contract -- D-23).
5. Project the report onto a ``NotificationEvent`` via
   :func:`_drift_event_from_report` and call ``sink.send(event)``;
   return 0 on success.

Exit codes:

* ``0`` -- drift JSON parsed, sink resolved, ``post()`` returned without
  raising. Note: the sinks themselves swallow transport errors and log
  at WARN, so a failed Teams webhook still exits 0; that is the
  documented Phase 13 contract (the CI gate is owned by ``sigantry diff
  --fail-on-drift``, not by notification success).
* ``1`` -- drift JSON missing / malformed, env-var configuration invalid,
  or the sink raised an unexpected exception (which the sink Protocol
  contract says it should not, but we catch defensively).

Threat model (Plan 13-06 §threat_model T-13-06-NOTIFICATION-SECRET-LEAK):
operator-supplied secrets (webhook URLs, SMTP passwords) flow via the
``env:`` block on the notify job (Pitfall 8 / D-28) -- never as
template-time string interpolation. This module reads them from the
process env, so the operator's CI secrets store is the trust boundary.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from sigantry_core.notifications import sink_from_env
from sigantry_core.protocols import NotificationEvent
from sigantry_core.sync.diff import DriftReport

logger = logging.getLogger("sigantry_core.sync._notify_main")


def _drift_event_from_report(report: DriftReport, *, workspace_id: str) -> NotificationEvent:
    """Project a :class:`DriftReport` onto a :class:`NotificationEvent`.

    The Plan 16-01 NotificationSink Protocol seam takes a generic
    NotificationEvent (not a DriftReport-specific shape); this helper
    centralises the projection so the wire format stays consistent
    across Teams / Slack / email.
    """
    has_drift = report.has_drift()
    if has_drift:
        title = f"Sigantry drift: {workspace_id}"
        body = (
            f"Sigantry drift on workspace {workspace_id}: "
            f"+{len(report.added)} added, "
            f"-{len(report.removed)} removed, "
            f"~{len(report.modified)} modified."
        )
        level = "warning"
    else:
        title = f"Sigantry drift OK: {workspace_id}"
        body = f"Sigantry drift OK on workspace {workspace_id}: no drift."
        level = "info"
    properties = {
        "workspace_id": workspace_id,
        "added": str(len(report.added)),
        "removed": str(len(report.removed)),
        "modified": str(len(report.modified)),
        "unchanged": str(len(report.unchanged)),
    }
    return NotificationEvent(
        title=title,
        body=body,
        level=level,  # type: ignore[arg-type]
        properties=properties,
    )


def _drift_report_from_json(payload: dict) -> DriftReport:
    """Reconstruct a :class:`DriftReport` from the JSON wire format.

    The keyset is the D-23 lock: ``schema_version``, ``added``,
    ``removed``, ``modified``, ``unchanged``. Missing keys default to
    empty lists / ``"1.0.0"`` so a partially-malformed payload still
    surfaces a coherent (if empty) drift summary to the sink.
    """
    return DriftReport(
        schema_version=payload.get("schema_version", "1.0.0"),
        added=list(payload.get("added", [])),
        removed=list(payload.get("removed", [])),
        modified=list(payload.get("modified", [])),
        unchanged=list(payload.get("unchanged", [])),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the argparse surface and parse ``argv`` (or ``sys.argv[1:]``)."""
    parser = argparse.ArgumentParser(
        prog="sigantry_core.sync._notify_main",
        description=(
            "Read a sigantry diff JSON output, resolve the notification "
            "sink from env vars, and post the drift summary."
        ),
    )
    parser.add_argument(
        "--drift-json",
        type=Path,
        default=Path("drift.json"),
        help=(
            "Path to the sigantry diff JSON output (default: drift.json). "
            "Wire format is the SemVer-pinned D-23 contract."
        ),
    )
    parser.add_argument(
        "--workspace-id",
        type=str,
        default=os.environ.get("SIGANTRY_DRIFT_WORKSPACE_ID", ""),
        help=(
            "Workspace id to surface in the notification body. "
            "Default: pulled from $SIGANTRY_DRIFT_WORKSPACE_ID."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint -- returns the exit code per the module docstring."""
    args = _parse_args(argv)
    drift_path: Path = args.drift_json

    if not drift_path.is_file():
        logger.error("drift_json_not_found path=%s", drift_path)
        print(
            f"sigantry _notify_main: drift JSON not found at {drift_path}",
            file=sys.stderr,
        )
        return 1

    try:
        payload = json.loads(drift_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("drift_json_parse_failed path=%s err=%s", drift_path, exc)
        print(
            f"sigantry _notify_main: failed to parse {drift_path}: {exc}",
            file=sys.stderr,
        )
        return 1

    if not isinstance(payload, dict):
        logger.error(
            "drift_json_unexpected_shape path=%s type=%s", drift_path, type(payload).__name__
        )
        print(
            f"sigantry _notify_main: {drift_path} did not parse to a JSON object",
            file=sys.stderr,
        )
        return 1

    try:
        sink = sink_from_env()
    except ValueError as exc:
        logger.error("sink_resolution_failed err=%s", exc)
        print(
            f"sigantry _notify_main: sink configuration invalid: {exc}",
            file=sys.stderr,
        )
        return 1

    report = _drift_report_from_json(payload)
    workspace_id = args.workspace_id or "<unknown>"

    event = _drift_event_from_report(report, workspace_id=workspace_id)
    try:
        sink.send(event)
    except Exception as exc:  # defensive -- Protocol contract says no
        logger.error("sink_send_unexpected_failure err=%s", exc)
        print(
            f"sigantry _notify_main: unexpected sink failure: {exc}",
            file=sys.stderr,
        )
        return 1

    summary = (
        f"sigantry _notify_main: posted drift summary for workspace="
        f"{workspace_id} drift={report.has_drift()} "
        f"(+{len(report.added)} -{len(report.removed)} ~{len(report.modified)} ={len(report.unchanged)})"
    )
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised by integration only
    sys.exit(main())
