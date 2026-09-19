"""Phase 16 NotificationSink reference implementations + sink_from_env factory.

Migrated from ``sigantry_core/sync/notifications.py`` per
16-RESEARCH.md §3 correction (Plan 16-01); the Phase 13 stand-ins are
now first-class reference implementations of the new
:class:`sigantry_core.protocols.NotificationSink` Protocol seam.

``sink_from_env()`` preserves the Phase 13 environment-variable
contract; the new ``SIGANTRY_TEAMS_WEBHOOK_FORMAT`` env var (default
``"adaptive_card"``) is the only addition (RESEARCH §Pitfall 1: Office
365 Connectors disabled May 18-22, 2026 -- adopters who need the legacy
MessageCard payload set ``SIGANTRY_TEAMS_WEBHOOK_FORMAT=messagecard``).

Recognised environment variables:

* ``SIGANTRY_NOTIFICATION_SINK`` -> ``"teams" | "slack" | "email"``
* ``SIGANTRY_TEAMS_WEBHOOK`` -> str (Workflows webhook URL or legacy Connector URL)
* ``SIGANTRY_TEAMS_WEBHOOK_FORMAT`` -> ``"adaptive_card"`` (default) | ``"messagecard"``
* ``SIGANTRY_SLACK_WEBHOOK`` -> str
* ``SIGANTRY_SMTP_HOST`` -> str
* ``SIGANTRY_SMTP_PORT`` -> int (default ``587``)
* ``SIGANTRY_SMTP_USER`` -> str (optional)
* ``SIGANTRY_SMTP_PASSWORD`` -> str (optional; wrapped in ``Secret`` on entry)
* ``SIGANTRY_SMTP_FROM`` -> str
* ``SIGANTRY_SMTP_TO`` -> comma-separated str
* ``SIGANTRY_SMTP_USE_TLS`` -> ``"true"`` (default) | ``"false"``
"""

from __future__ import annotations

import os
from typing import cast

from sigantry_core.notifications.email import EmailNotificationSink
from sigantry_core.notifications.slack import SlackNotificationSink
from sigantry_core.notifications.teams import TeamsNotificationSink
from sigantry_core.protocols import NotificationSink


def sink_from_env() -> NotificationSink:
    """Return a configured :class:`NotificationSink` from process env vars.

    Plan 16-01 preserves the Phase 13 contract; the new
    ``SIGANTRY_TEAMS_WEBHOOK_FORMAT`` env var (default
    ``"adaptive_card"``) is the only addition.

    Raises
    ------
    ValueError
        If ``SIGANTRY_NOTIFICATION_SINK`` is unset, holds an unknown
        value, or any required per-impl env var is missing.
    """
    kind = os.environ.get("SIGANTRY_NOTIFICATION_SINK", "").strip().lower()
    if kind == "teams":
        webhook = os.environ.get("SIGANTRY_TEAMS_WEBHOOK", "").strip()
        if not webhook:
            raise ValueError(
                "SIGANTRY_NOTIFICATION_SINK=teams requires SIGANTRY_TEAMS_WEBHOOK to be set."
            )
        fmt = os.environ.get("SIGANTRY_TEAMS_WEBHOOK_FORMAT", "adaptive_card").strip().lower()
        if fmt not in ("adaptive_card", "messagecard"):
            raise ValueError(
                "SIGANTRY_TEAMS_WEBHOOK_FORMAT must be 'adaptive_card' (default) or "
                f"'messagecard'; got {fmt!r}."
            )
        return cast(
            NotificationSink,
            TeamsNotificationSink(
                webhook_url=webhook,
                format=fmt,  # type: ignore[arg-type]
            ),
        )
    if kind == "slack":
        webhook = os.environ.get("SIGANTRY_SLACK_WEBHOOK", "").strip()
        if not webhook:
            raise ValueError(
                "SIGANTRY_NOTIFICATION_SINK=slack requires SIGANTRY_SLACK_WEBHOOK to be set."
            )
        return cast(NotificationSink, SlackNotificationSink(webhook_url=webhook))
    if kind == "email":
        host = os.environ.get("SIGANTRY_SMTP_HOST", "").strip()
        from_addr = os.environ.get("SIGANTRY_SMTP_FROM", "").strip()
        to_str = os.environ.get("SIGANTRY_SMTP_TO", "").strip()
        if not (host and from_addr and to_str):
            raise ValueError(
                "SIGANTRY_NOTIFICATION_SINK=email requires SIGANTRY_SMTP_HOST, "
                "SIGANTRY_SMTP_FROM, and SIGANTRY_SMTP_TO to be set."
            )
        port_str = os.environ.get("SIGANTRY_SMTP_PORT", "587").strip() or "587"
        try:
            port = int(port_str)
        except ValueError as exc:
            raise ValueError(f"SIGANTRY_SMTP_PORT must be an integer; got {port_str!r}.") from exc
        recipients = [s.strip() for s in to_str.split(",") if s.strip()]
        if not recipients:
            raise ValueError("SIGANTRY_SMTP_TO must list at least one recipient (comma-separated).")
        use_tls = os.environ.get("SIGANTRY_SMTP_USE_TLS", "true").strip().lower() != "false"
        return cast(
            NotificationSink,
            EmailNotificationSink(
                smtp_host=host,
                smtp_port=port,
                smtp_user=os.environ.get("SIGANTRY_SMTP_USER") or None,
                smtp_password=os.environ.get("SIGANTRY_SMTP_PASSWORD") or None,
                from_addr=from_addr,
                default_recipients=recipients,
                use_tls=use_tls,
            ),
        )
    raise ValueError(
        f"SIGANTRY_NOTIFICATION_SINK must be one of {{'teams', 'slack', 'email'}}; got {kind!r}."
    )


__all__ = [
    "EmailNotificationSink",
    "SlackNotificationSink",
    "TeamsNotificationSink",
    "sink_from_env",
]
