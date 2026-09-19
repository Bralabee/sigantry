"""SlackNotificationSink tests (Plan 16-01).

Modern Block Kit ``blocks`` payload + severity emoji prefix per
RESEARCH §State of the Art. Legacy ``attachments[].color`` is NOT used.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from sigantry_core.notifications.slack import SlackNotificationSink
from sigantry_core.protocols import NotificationEvent

WEBHOOK_URL = "https://hooks.slack.com/services/T000/B000/contract"


def _event(level: str = "warning") -> NotificationEvent:
    return NotificationEvent(
        title="Sigantry release approved",
        body="release-2026-04-28-001 approved by alice@example.com",
        level=level,  # type: ignore[arg-type]
        properties={"release_id": "release-2026-04-28-001", "env": "prod"},
    )


def _post_and_capture(sink: SlackNotificationSink, event: NotificationEvent) -> dict:
    """Send via ``sink.send`` against respx-mocked transport; return the JSON body."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, text="ok"))
        sink.send(event)
    raw = route.calls[0].request.read().decode("utf-8")
    return json.loads(raw)


def test_blocks_snapshot() -> None:
    """``send()`` POSTs the modern Block Kit ``blocks`` payload."""
    sink = SlackNotificationSink(webhook_url=WEBHOOK_URL)
    event = _event(level="warning")
    body = _post_and_capture(sink, event)

    # Top-level shape: only 'blocks' (NOT 'attachments').
    assert "blocks" in body
    blocks = body["blocks"]

    # Layout: heading section + body section + divider + context block.
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["type"] == "mrkdwn"
    # Severity emoji prefix + bold title.
    assert ":warning:" in blocks[0]["text"]["text"]
    assert "*Sigantry release approved*" in blocks[0]["text"]["text"]

    assert blocks[1]["type"] == "section"
    assert blocks[1]["text"]["text"] == event.body

    assert blocks[2]["type"] == "divider"

    # Context block carries the properties dict as italic key/value pairs.
    assert blocks[3]["type"] == "context"
    ctx_text = blocks[3]["elements"][0]["text"]
    assert "release_id" in ctx_text
    assert "release-2026-04-28-001" in ctx_text


def test_slack_uses_blocks_not_legacy_attachments() -> None:
    """Modern Block Kit only -- legacy ``attachments[].color`` is NOT used."""
    sink = SlackNotificationSink(webhook_url=WEBHOOK_URL)
    body = _post_and_capture(sink, _event())

    assert "blocks" in body
    assert "attachments" not in body


def test_slack_severity_emoji_prefix() -> None:
    """Each NotificationLevel maps to a distinct leading emoji."""
    sink = SlackNotificationSink(webhook_url=WEBHOOK_URL)

    for level, emoji in (
        ("info", ":information_source:"),
        ("warning", ":warning:"),
        ("error", ":exclamation:"),
        ("critical", ":rotating_light:"),
    ):
        body = _post_and_capture(sink, _event(level=level))
        leading = body["blocks"][0]["text"]["text"]
        assert leading.startswith(emoji), (
            f"level={level}: expected leading emoji {emoji!r}; got {leading!r}"
        )


def test_slack_warn_logs_on_post_failure_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed Slack POST is logged at WARN, NOT propagated."""
    sink = SlackNotificationSink(webhook_url=WEBHOOK_URL)
    event = _event()

    caplog.set_level(logging.WARNING, logger="sigantry_core.notifications.slack")
    with respx.mock(assert_all_called=True) as router:
        router.post(WEBHOOK_URL).mock(side_effect=httpx.ConnectError("simulated transport failure"))
        # Must NOT raise.
        sink.send(event)

    assert any("slack_webhook_post_failed" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


def test_slack_repr_redacts_webhook_url_path() -> None:
    """T-16-01-01: __repr__ surfaces only the host, not the credentialed path."""
    sink = SlackNotificationSink(
        webhook_url="https://hooks.slack.com/services/T000/B000/super-secret-token-do-not-leak"
    )
    rendered = repr(sink)
    assert "super-secret-token-do-not-leak" not in rendered
    assert "hooks.slack.com" in rendered


def test_slack_ping_posts_minimal_payload() -> None:
    """``ping()`` POSTs a minimal blocks payload to the webhook URL."""
    sink = SlackNotificationSink(webhook_url=WEBHOOK_URL)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        sink.ping()
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.read().decode("utf-8"))
    assert "blocks" in body
    # The robot face emoji is the canonical ping marker.
    leading = body["blocks"][0]["text"]["text"]
    assert "Sigantry ping" in leading
