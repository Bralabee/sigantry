"""TeamsNotificationSink tests (Plan 16-01).

Adaptive Card 1.5 + MessageCard legacy fallback per RESEARCH §Pitfall 1
(Office 365 Connectors disabled May 18-22, 2026).

Verifies:
- Adaptive Card byte-shape pinned to RESEARCH §Pattern 3
- MessageCard legacy payload retained behind format='messagecard'
- POST round-trips against respx-mocked transport
- 429 + transient 5xx are retried via execute_with_retry
- Persistent transport / status failures are logged at WARN, NOT raised
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from sigantry_core.notifications.teams import TeamsNotificationSink
from sigantry_core.protocols import NotificationEvent

WEBHOOK_URL = "https://example.com/webhook"


def _event(level: str = "warning") -> NotificationEvent:
    return NotificationEvent(
        title="Sigantry release approved",
        body="release-2026-04-28-001 approved by alice@example.com",
        level=level,  # type: ignore[arg-type]
        properties={"release_id": "release-2026-04-28-001", "env": "prod"},
    )


def test_adaptive_card_snapshot() -> None:
    """Adaptive Card 1.5 byte-shape matches RESEARCH §Pattern 3 exactly."""
    sink = TeamsNotificationSink(webhook_url=WEBHOOK_URL)
    event = _event(level="warning")
    payload = sink._adaptive_card_payload(event)

    # Top-level envelope is 'message' + attachments[0] of contentType
    # 'application/vnd.microsoft.card.adaptive' (Workflows webhook).
    assert payload["type"] == "message"
    assert len(payload["attachments"]) == 1
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["contentUrl"] is None

    # Adaptive Card 1.5 schema header.
    content = attachment["content"]
    assert content["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.5"

    # Body: title TextBlock (severity colour) + body TextBlock + FactSet.
    body = content["body"]
    assert body[0]["type"] == "TextBlock"
    assert body[0]["text"] == event.title
    # warning maps to 'warning' colour token per RESEARCH §Pattern 3.
    assert body[0]["color"] == "warning"
    assert body[1]["type"] == "TextBlock"
    assert body[1]["text"] == event.body
    assert body[1]["wrap"] is True
    assert body[2]["type"] == "FactSet"
    facts = body[2]["facts"]
    assert {"title": "release_id", "value": "release-2026-04-28-001"} in facts
    assert {"title": "env", "value": "prod"} in facts


def test_messagecard_legacy() -> None:
    """``format='messagecard'`` returns the legacy MessageCard envelope."""
    sink = TeamsNotificationSink(webhook_url=WEBHOOK_URL, format="messagecard")
    event = _event(level="error")
    payload = sink._messagecard_payload(event)

    # Legacy MessageCard schema (NOT the Adaptive Card schema).
    assert payload["@type"] == "MessageCard"
    assert payload["@context"] == "https://schema.org/extensions"
    assert "themeColor" in payload  # Legacy field; absent in Adaptive Card.
    # error -> red theme colour per the _MESSAGECARD_THEME_COLOR map.
    assert payload["themeColor"] == "FF0000"
    # No Adaptive Card schema fields surface in the MessageCard payload.
    assert "$schema" not in payload
    # The constructor flag is also reflected in the .format property.
    assert sink.format == "messagecard"


def test_teams_post_uses_workflows_url_format() -> None:
    """``send()`` POSTs the Adaptive Card payload to the configured URL."""
    sink = TeamsNotificationSink(webhook_url=WEBHOOK_URL)
    event = _event(level="info")

    with respx.mock(assert_all_called=True) as router:
        route = router.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        sink.send(event)

    assert route.call_count == 1
    request = route.calls[0].request
    body = request.read().decode("utf-8")
    # Workflows webhook envelope keys surface in the wire body.
    assert "application/vnd.microsoft.card.adaptive" in body
    assert "AdaptiveCard" in body
    assert event.title in body
    # Severity colour token surfaces (info -> 'good'). Match the parsed
    # JSON to stay decoupled from httpx's serialisation whitespace.
    import json as _json

    parsed = _json.loads(body)
    first_text_block = parsed["attachments"][0]["content"]["body"][0]
    assert first_text_block["color"] == "good"


def test_teams_handles_429_via_tenacity_retry() -> None:
    """A 429 response triggers a retry; the second attempt's 200 succeeds.

    Verifies the sink wires through ``execute_with_retry`` from
    ``sigantry_core.client.retry``; the retry policy includes 429 in
    its retry-eligible status set.
    """
    sink = TeamsNotificationSink(webhook_url=WEBHOOK_URL)
    event = _event()

    with respx.mock(assert_all_called=True) as router:
        # respx side_effect cycles through the list of responses.
        route = router.post(WEBHOOK_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        sink.send(event)

    # Two POSTs: the 429 retry + the eventual 200.
    assert route.call_count == 2


def test_teams_warn_logs_on_post_failure_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``httpx.ConnectError`` is logged at WARN; ``send()`` does NOT raise."""
    sink = TeamsNotificationSink(webhook_url=WEBHOOK_URL)
    event = _event()

    caplog.set_level(logging.WARNING, logger="sigantry_core.notifications.teams")
    with respx.mock(assert_all_called=True) as router:
        router.post(WEBHOOK_URL).mock(side_effect=httpx.ConnectError("simulated transport failure"))
        # Must NOT raise -- the CI gate is owned by sigantry diff,
        # NOT by notification delivery success.
        sink.send(event)

    assert any("teams_webhook_post_failed" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


def test_teams_repr_redacts_webhook_url_path() -> None:
    """T-16-01-01: __repr__ surfaces only the host, not the credentialed path."""
    sink = TeamsNotificationSink(
        webhook_url="https://outlook.office.com/webhook/super-secret-token-do-not-leak"
    )
    rendered = repr(sink)
    # The URL path (which acts as the auth credential) MUST NOT leak.
    assert "super-secret-token-do-not-leak" not in rendered
    # The host is safe to surface for debugging.
    assert "outlook.office.com" in rendered


def test_teams_format_validation_rejects_unknown() -> None:
    """The constructor rejects an unknown ``format`` value."""
    with pytest.raises(ValueError, match="format must be"):
        TeamsNotificationSink(webhook_url=WEBHOOK_URL, format="unknown")  # type: ignore[arg-type]


def test_teams_ping_posts_to_webhook() -> None:
    """``ping()`` POSTs a minimal Adaptive Card to the webhook URL."""
    sink = TeamsNotificationSink(webhook_url=WEBHOOK_URL)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        sink.ping()
    assert route.call_count == 1
    body = route.calls[0].request.read().decode("utf-8")
    assert "Sigantry ping" in body
    assert "AdaptiveCard" in body
