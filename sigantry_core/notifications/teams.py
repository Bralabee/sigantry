"""TeamsNotificationSink -- first-class NotificationSink reference impl (Plan 16-01).

Migrated from ``sigantry_core/sync/notifications.py::TeamsWebhookSink``
(Phase 13 stand-in) and modernised to follow the
``NotificationSink`` Protocol seam declared in
:mod:`sigantry_core.protocols`.

Default payload format
----------------------

Adaptive Card 1.5 + Workflows webhook envelope is the **default** per
16-RESEARCH.md §Pitfall 1 (Office 365 Connectors disabled May 18-22,
2026). The legacy MessageCard payload is retained behind
``format='messagecard'`` for grandfathered Connector URLs that have not
yet been migrated to the Workflows webhook surface. CHANGELOG +
16-HUMAN-UAT.md Test 1 surface the choice for adopters.

Carve-out from the project-wide "one HTTP client" rule (CLAUDE.md)
------------------------------------------------------------------

This module imports :mod:`httpx` directly. Routing notification webhooks
through :class:`sigantry_core.client.FabricRestClient` would force a
:class:`TokenProvider` on a credential-less webhook URL (the URL itself
is the auth credential -- no Authorization header is sent). The
exception is allow-listed in ``pyproject.toml``
(``[tool.ruff.lint.per-file-ignores]`` -> ``TID251``) and documented in
the banned-api message.

Failure handling
----------------

``send()`` catches transport / status errors and LOGS at WARN without
re-raising. The scheduled drift-check pipeline is gated by
``sigantry diff --fail-on-drift`` exit code, NOT by notification
success. A failed Teams webhook does not break the CI gate.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Literal

# Module-local exception to the project's "one HTTP client" rule
# (CLAUDE.md). Webhook URLs are NOT Fabric API endpoints; routing them
# through ``FabricRestClient`` would force a TokenProvider on a
# credential-less URL. Allow-listed in ``pyproject.toml``.
import httpx

from sigantry_core.client.retry import execute_with_retry
from sigantry_core.protocols import NotificationEvent

logger = logging.getLogger("sigantry_core.notifications.teams")

#: Connect / read timeout for outbound webhook POSTs (seconds).
_DEFAULT_HTTP_TIMEOUT_SECONDS: float = 30.0

#: Adaptive Card 1.5 colour token map (per RESEARCH §Pattern 3).
_ADAPTIVE_CARD_COLOR: dict[str, str] = {
    "info": "good",
    "warning": "warning",
    "error": "attention",
    "critical": "attention",
}

#: Legacy MessageCard ``themeColor`` (HTML hex) for grandfathered URLs.
_MESSAGECARD_THEME_COLOR: dict[str, str] = {
    "info": "00BFFF",
    "warning": "FFA500",
    "error": "FF0000",
    "critical": "8B0000",
}


class TeamsNotificationSink:
    """POST a NotificationEvent to a Microsoft Teams webhook.

    Default body shape is the Adaptive Card 1.5 + Workflows-webhook
    envelope per 16-RESEARCH.md §Pattern 3. Adopters with grandfathered
    Office 365 Connector URLs can opt into the legacy MessageCard
    payload via ``format='messagecard'``.
    """

    name: str = "teams"

    def __init__(
        self,
        *,
        webhook_url: str,
        format: Literal["adaptive_card", "messagecard"] = "adaptive_card",
        timeout: float = _DEFAULT_HTTP_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        if format not in ("adaptive_card", "messagecard"):
            raise ValueError(f"format must be 'adaptive_card' or 'messagecard'; got {format!r}.")
        self._webhook_url = webhook_url
        self._format = format
        self._timeout = timeout
        self._http = http_client

    @property
    def webhook_url(self) -> str:
        """Operator-supplied webhook URL (Workflows or legacy Connector)."""
        return self._webhook_url

    @property
    def format(self) -> str:
        """Active payload format -- ``adaptive_card`` (default) or ``messagecard``."""
        return self._format

    def __repr__(self) -> str:
        # T-16-01-01: redact webhook URL path -- the URL itself IS the
        # auth credential. Surface only the host for debug visibility.
        host = "<unparsable>"
        with contextlib.suppress(Exception):
            host = httpx.URL(self._webhook_url).host or "<empty>"
        return (
            f"TeamsNotificationSink(webhook_host={host!r}, "
            f"format={self._format!r}, timeout={self._timeout!r})"
        )

    # ---------------------------------------------------------------
    # Payload builders
    # ---------------------------------------------------------------

    def _adaptive_card_payload(self, event: NotificationEvent) -> dict:
        """Build an Adaptive Card 1.5 Workflows-webhook payload.

        Byte-shape pinned by 16-RESEARCH.md §Pattern 3 + the verifying
        snapshot test ``tests/sigantry_core/notifications/test_teams.py
        ::test_adaptive_card_snapshot``.
        """
        color = _ADAPTIVE_CARD_COLOR.get(event.level, "default")
        body: list[dict] = [
            {
                "type": "TextBlock",
                "text": event.title,
                "weight": "Bolder",
                "size": "Medium",
                "color": color,
            },
            {"type": "TextBlock", "text": event.body, "wrap": True},
        ]
        if event.properties:
            body.append(
                {
                    "type": "FactSet",
                    "facts": [{"title": k, "value": str(v)} for k, v in event.properties.items()],
                }
            )
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.5",
                        "body": body,
                    },
                }
            ],
        }

    def _messagecard_payload(self, event: NotificationEvent) -> dict:
        """Build a legacy Microsoft Teams MessageCard payload.

        Retained behind ``format='messagecard'`` for grandfathered
        Office 365 Connector URLs. Microsoft is disabling the Connector
        webhook surface May 18-22, 2026 -- adopters opting in accept the
        deprecation risk explicitly (T-16-01-03).
        """
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": event.title,
            "themeColor": _MESSAGECARD_THEME_COLOR.get(event.level, "808080"),
            "title": event.title,
            "sections": [
                {
                    "activityTitle": event.title,
                    "text": event.body,
                    "facts": [
                        {"name": k, "value": str(v)} for k, v in (event.properties or {}).items()
                    ],
                }
            ],
        }

    # ---------------------------------------------------------------
    # NotificationSink Protocol surface
    # ---------------------------------------------------------------

    def send(self, event: NotificationEvent, *, channel: str | None = None) -> None:
        """POST the event to the configured webhook URL.

        ``channel`` is unused for Teams (the webhook URL targets a
        specific channel). It is accepted for Protocol conformance.
        Transport / status errors are logged at WARN and not re-raised.
        """
        if self._format == "adaptive_card":
            payload = self._adaptive_card_payload(event)
        else:
            payload = self._messagecard_payload(event)
        self._post_json(payload)

    def ping(self) -> None:
        """POST a minimal ping payload to verify the webhook is reachable.

        Uses the active payload format (``adaptive_card`` or
        ``messagecard``). Transport failures are logged at WARN and not
        re-raised -- ``ping()`` is best-effort for the operator gate.
        """
        ping_event = NotificationEvent(
            title="Sigantry ping",
            body="ping",
            level="info",
            properties={},
        )
        if self._format == "adaptive_card":
            payload = self._adaptive_card_payload(ping_event)
        else:
            payload = self._messagecard_payload(ping_event)
        self._post_json(payload)

    # ---------------------------------------------------------------
    # Transport helpers
    # ---------------------------------------------------------------

    def _post_json(self, payload: dict) -> None:
        """Send ``payload`` as JSON to the webhook URL with retry + log-on-fail."""
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
                "teams_webhook_post_failed status=%s err=%s",
                getattr(getattr(exc, "response", None), "status_code", "n/a"),
                exc,
            )


__all__ = ["TeamsNotificationSink"]
