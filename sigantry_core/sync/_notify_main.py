"""argparse entrypoint for the scheduled drift-check templates (Plan 13-06 / DRIFT-03).

The ADO + GHA drift-check workflows
(``templates/schedules/drift-check.yml`` and
``.github/workflows/drift-check.yml``) invoke this module as
``python -m sigantry_core.sync._notify_main --drift-json drift.json``
after ``sigantry diff --output json --fail-on-drift`` has written its
SemVer-pinned JSON output.

Runtime contract:

1. Read ``--drift-json <path>`` (default: ``drift.json``) and
   ``--workspace-id <id>`` (default: pulled from
   ``$SIGANTRY_DRIFT_WORKSPACE_ID`` for back-compat with operators who
   set the env var on the notify job).
2. Resolve the sink via :func:`sigantry_core.notifications.sink_from_env`
   (reads ``$SIGANTRY_NOTIFICATION_SINK`` + the per-impl env vars), BEFORE
   reading the report, so a check that produced no report can still be
   announced (#34).
   Plan 16-01 migrated the import path from
   ``sigantry_core.sync.notifications`` to ``sigantry_core.notifications``;
   the legacy path remains as a deprecation shim through v3.0.
3. Read the JSON payload and check it is a D-23 drift report: an object
   carrying all five keys, a ``1.x`` ``schema_version``, and four lists of
   objects. Anything else -- a missing, empty or unparsable file, which is
   what ``sigantry diff`` leaves behind when it exits 2, or any other
   object -- is NOT a drift report, and is never summarised as "no drift".
   It is announced as one ``error``-level event saying the drift check
   produced no report (an operational failure, not drift), pointing at the
   CI run log, and the module exits 1. The file's content is not echoed
   into the channel.
4. Otherwise project the report onto a ``NotificationEvent`` via
   :func:`_drift_event_from_report` and call ``sink.send(event)``;
   return 0 on success.

Exit codes:

* ``0`` -- drift JSON parsed, sink resolved, ``post()`` returned without
  raising. Note: the sinks themselves swallow transport errors and log
  at WARN, so a failed Teams webhook still exits 0; that is the
  documented Phase 13 contract (the CI gate is owned by ``sigantry diff
  --fail-on-drift``, not by notification success).
* ``1`` -- env-var configuration invalid (nothing is sent); the drift
  JSON missing / malformed (the operational-failure event is sent first);
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


_REPORT_LISTS = ("added", "removed", "modified", "unchanged")


class NoDriftReportError(Exception):
    """The drift JSON is not a drift report; the message says why, not what it held."""


def _drift_report_from_json(payload: object) -> DriftReport:
    """Reconstruct a :class:`DriftReport` from the JSON wire format.

    The keyset is the D-23 lock: ``schema_version``, ``added``,
    ``removed``, ``modified``, ``unchanged``. Every key must be present:
    a payload missing one is not a report, and defaulting it to an empty
    list would turn, say, ``{"error": "..."}`` into a "no drift" summary
    (#34). Further top-level keys are accepted, since D-37 lets a minor
    schema version add them.
    """
    if not isinstance(payload, dict):
        raise NoDriftReportError(f"the file holds a JSON {type(payload).__name__}, not an object")
    missing = [key for key in ("schema_version", *_REPORT_LISTS) if key not in payload]
    if missing:
        raise NoDriftReportError(f"the object lacks the drift report keys {missing}")
    version = payload["schema_version"]
    if not isinstance(version, str) or not version.startswith("1."):
        raise NoDriftReportError(f"schema_version is not a 1.x version string: {version!r}")
    for key in _REPORT_LISTS:
        value = payload[key]
        if not isinstance(value, list) or not all(isinstance(e, dict) for e in value):
            raise NoDriftReportError(f"{key!r} is not a list of objects")
    return DriftReport(
        schema_version=version,
        added=list(payload["added"]),
        removed=list(payload["removed"]),
        modified=list(payload["modified"]),
        unchanged=list(payload["unchanged"]),
    )


def _read_report(path: Path) -> DriftReport:
    if not path.is_file():
        raise NoDriftReportError(f"{path} does not exist")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NoDriftReportError(f"{path} could not be read ({type(exc).__name__})") from exc
    if not text.strip():
        raise NoDriftReportError(f"{path} is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NoDriftReportError(f"{path} is not valid JSON ({exc.msg})") from exc
    return _drift_report_from_json(payload)


def _run_url() -> str:
    """The CI run's URL from the runner's own environment, or ``""``."""
    env = os.environ
    if env.get("GITHUB_SERVER_URL") and env.get("GITHUB_REPOSITORY") and env.get("GITHUB_RUN_ID"):
        return (
            f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
            f"/actions/runs/{env['GITHUB_RUN_ID']}"
        )
    collection = env.get("SYSTEM_COLLECTIONURI", "")
    project = env.get("SYSTEM_TEAMPROJECT", "")
    build = env.get("BUILD_BUILDID", "")
    if collection and project and build:
        return f"{collection.rstrip('/')}/{project}/_build/results?buildId={build}"
    return ""


def _no_report_event(reason: str, *, workspace_id: str) -> NotificationEvent:
    """The event for a drift check that produced no report: an operational failure."""
    url = _run_url()
    where = f"See the run log: {url}" if url else "See the CI run's log."
    return NotificationEvent(
        title=f"Sigantry drift check FAILED: {workspace_id}",
        body=(
            f"Sigantry drift check on workspace {workspace_id} produced no drift report "
            f"({reason}). This is an operational failure of the check, not drift: the "
            f"workspace was not compared. {where}"
        ),
        level="error",
        properties={"workspace_id": workspace_id, "outcome": "no_report", "run_url": url},
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
    workspace_id = args.workspace_id or "<unknown>"

    try:
        sink = sink_from_env()
    except ValueError as exc:
        logger.error("sink_resolution_failed err=%s", exc)
        print(
            f"sigantry _notify_main: sink configuration invalid: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        report = _read_report(drift_path)
    except NoDriftReportError as exc:
        logger.error("drift_json_not_a_report path=%s reason=%s", drift_path, exc)
        print(
            f"sigantry _notify_main: no drift report ({exc}); "
            "announcing an operational failure of the drift check",
            file=sys.stderr,
        )
        try:
            sink.send(_no_report_event(str(exc), workspace_id=workspace_id))
        except Exception as send_exc:  # defensive -- Protocol contract says no
            logger.error("sink_send_unexpected_failure err=%s", send_exc)
            print(
                f"sigantry _notify_main: unexpected sink failure: {send_exc}",
                file=sys.stderr,
            )
        return 1

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
