"""SlackNotificationSink -- first-class NotificationSink reference impl (Plan 16-01).

Migrated from ``sigantry_core/sync/notifications.py::SlackWebhookSink``
(Phase 13 stand-in) and modernised to the **Block Kit** ``blocks``
payload per 16-RESEARCH.md §State of the Art -- legacy
``attachments[].color`` was soft-deprecated by Slack in 2019 and is no
longer the recommended payload shape for incoming webhooks.

Carve-out from the project-wide "one HTTP client" rule (CLAUDE.md)
------------------------------------------------------------------

This module imports :mod:`httpx` directly. Routing the webhook through
:class:`sigantry_core.client.FabricRestClient` would force a
:class:`TokenProvider` on a credential-less URL. Allow-listed in
``pyproject.toml`` (``[tool.ruff.lint.per-file-ignores]``).

Failure handling
----------------

``send()`` catches transport / status errors and LOGS at WARN without
re-raising. The CI gate is owned by ``sigantry diff --fail-on-drift``;
a failed Slack post does not break the pipeline.
"""

from __future__ import annotations

import contextlib
import logging

import httpx

from sigantry_core.client.retry import execute_with_retry
from sigantry_core.protocols import NotificationEvent

logger = logging.getLogger("sigantry_core.notifications.slack")

#: Connect / read timeout for outbound webhook POSTs (seconds).
_DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0

#: Severity-emoji map for the leading mrkdwn TextBlock heading.
_LEVEL_EMOJI: dict[str, str] = {
    "info": ":information_source:",
    "warning": ":warning:",
    "error": ":exclamation:",
    "critical": ":rotating_light:",
}


class SlackNotificationSink:
    """POST a NotificationEvent to a Slack incoming-webhook URL.

    Body shape uses Slack's modern Block Kit ``blocks`` payload (NOT
    the legacy ``attachments[].color`` shape). Layout:

    1. Section block -- mrkdwn heading with severity emoji + bold title.
    2. Section block -- mrkdwn body text.
    3. Divider.
    4. Context block -- italic key/value pairs from ``event.properties``.
    """

    name: str = "slack"

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout
        self._http = http_client

    @property
    def webhook_url(self) -> str:
        """Operator-supplied Slack incoming-webhook URL."""
        return self._webhook_url

    def __repr__(self) -> str:
        # T-16-01-01: redact webhook URL path -- the URL itself IS the
        # auth credential. Surface only the host for debug visibility.
        host = "<unparsable>"
        with contextlib.suppress(Exception):
            host = httpx.URL(self._webhook_url).host or "<empty>"
        return f"SlackNotificationSink(webhook_host={host!r}, timeout={self._timeout!r})"

    # ---------------------------------------------------------------
    # Payload builders
    # ---------------------------------------------------------------

    def _blocks_payload(self, event: NotificationEvent) -> dict:
        """Build a Slack Block Kit ``blocks`` payload.

        Byte-shape pinned by ``tests/sigantry_core/notifications/
        test_slack.py::test_blocks_snapshot``.
        """
        emoji = _LEVEL_EMOJI.get(event.level, ":information_source:")
        blocks: list[dict] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{event.title}*",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": event.body},
            },
            {"type": "divider"},
        ]
        if event.properties:
            ctx_text = " | ".join(f"_{k}_: {v}" for k, v in event.properties.items())
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": ctx_text}],
                }
            )
        return {"blocks": blocks}

    # ---------------------------------------------------------------
    # NotificationSink Protocol surface
    # ---------------------------------------------------------------

    def send(self, event: NotificationEvent, *, channel: str | None = None) -> None:
        """POST the event to the configured Slack webhook URL.

        ``channel`` is unused for incoming webhooks (the URL targets a
        specific channel). Accepted for Protocol conformance.
        Transport / status errors are logged at WARN and not re-raised.
        """
        payload = self._blocks_payload(event)
        self._post_json(payload)

    def ping(self) -> None:
        """POST a minimal blocks payload to verify reachability."""
        ping_event = NotificationEvent(
            title="Sigantry ping",
            body=":robot_face: Sigantry ping",
            level="info",
            properties={},
        )
        self._post_json(self._blocks_payload(ping_event))

    # ---------------------------------------------------------------
    # Transport helpers
    # ---------------------------------------------------------------

    def _post_json(self, payload: dict) -> None:
        client = self._http or httpx

        def _do_post() -> httpx.Response:
            return client.post(
                self._webhook_url,
                json=payload,
                timeout=self._timeout,
            )

        try:
            resp = execute_with_retry(_do_post)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "slack_webhook_post_failed status=%s err=%s",
                getattr(getattr(exc, "response", None), "status_code", "n/a"),
                exc,
            )


__all__ = ["SlackNotificationSink"]
