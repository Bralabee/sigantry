"""Phase 13 notification-sink module -- DEPRECATION SHIM (Plan 16-01).

.. deprecated:: 3.0.0

    Imports from ``sigantry_core.sync.notifications`` are deprecated.
    Use :mod:`sigantry_core.notifications` instead. The legacy path is
    retained for v3.0 backwards compatibility per ADR-0011 and the
    16-RESEARCH.md §Open-Q-1 resolution; this module drops in v3.1.

Plan 16-01 (Phase 16 / SEAM-01) migrated the three reference
implementations to first-class status under
:mod:`sigantry_core.notifications` as part of promoting the Phase 13
stand-ins to the new :class:`sigantry_core.protocols.NotificationSink`
Protocol seam:

* ``TeamsWebhookSink`` -> :class:`sigantry_core.notifications.teams.TeamsNotificationSink`
* ``SlackWebhookSink`` -> :class:`sigantry_core.notifications.slack.SlackNotificationSink`
* ``EmailSmtpSink`` -> :class:`sigantry_core.notifications.email.EmailNotificationSink`

Backwards-compat aliases are exposed at this module path:

* ``TeamsWebhookSink = TeamsNotificationSink``
* ``SlackWebhookSink = SlackNotificationSink``
* ``EmailSmtpSink = EmailNotificationSink``
* ``NotificationSinkProtocol = NotificationSink`` (the new Protocol)

External consumers who imported from this path receive a
:class:`DeprecationWarning` at module-import time so they have a
CHANGELOG-grade signal to migrate before v3.1 drops the shim.

This module does NOT import :mod:`httpx` -- it is a pure re-export.
The legacy ``[tool.ruff.lint.per-file-ignores]`` entry covering this
file (Plan 13-07) is REMOVED by Plan 16-01.
"""

from __future__ import annotations

import warnings

from sigantry_core.notifications import (
    EmailNotificationSink,
    SlackNotificationSink,
    TeamsNotificationSink,
    sink_from_env,
)
from sigantry_core.protocols import NotificationSink

# Backwards-compat aliases -- Phase 13 names map to Phase 16 names.
TeamsWebhookSink = TeamsNotificationSink
SlackWebhookSink = SlackNotificationSink
EmailSmtpSink = EmailNotificationSink
NotificationSinkProtocol = NotificationSink

warnings.warn(
    "sigantry_core.sync.notifications is deprecated; "
    "use sigantry_core.notifications instead. The legacy import path is "
    "retained for v3.0 backwards compatibility per ADR-0011 and drops in v3.1.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "EmailNotificationSink",
    "EmailSmtpSink",
    "NotificationSinkProtocol",
    "SlackNotificationSink",
    "SlackWebhookSink",
    "TeamsNotificationSink",
    "TeamsWebhookSink",
    "sink_from_env",
]
