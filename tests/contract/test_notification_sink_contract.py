"""NotificationSink Protocol contract tests (Phase 16 / SEAM-01 + SEAM-05).

Eighth contract test in tests/contract/. Wave 0 (Plan 16-00) shipped
xfail stubs; Plan 16-01 lands real assertions parametrised over Fake +
Teams + Slack + Email factories. Plan 16-04 will extend the parametrise
list to include the JToye reference impl.

Per RESEARCH §3 Open-Q-3 + Phase 11 ADR-0004: NotificationSink Protocol
instances do NOT carry an ``api_version`` class var. Cross-seam widening
deferred to v3.1 -- adding ``api_version`` to ANY single seam without
updating all ten is a contract break.
"""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from sigantry_core.notifications import (
    EmailNotificationSink,
    SlackNotificationSink,
    TeamsNotificationSink,
)
from sigantry_core.protocols import NotificationEvent, NotificationSink
from sigantry_core.testing.doubles import FakeNotificationSink

pytestmark = [pytest.mark.contract, pytest.mark.sigantry_seam]


# ---------------------------------------------------------------------------
# Sink factories -- one per impl. Each constructs the sink WITHOUT exercising
# transport (the contract battery is a pure-Python check of name / Protocol
# membership / signature shape; respx + smtplib mocks live in the per-impl
# tests at tests/sigantry_core/notifications/).
# ---------------------------------------------------------------------------


def _fake_sink() -> NotificationSink:
    return FakeNotificationSink()


def _teams_sink() -> NotificationSink:
    return TeamsNotificationSink(webhook_url="https://example.com/webhook")


def _slack_sink() -> NotificationSink:
    return SlackNotificationSink(webhook_url="https://hooks.slack.com/x")


def _email_sink() -> NotificationSink:
    return EmailNotificationSink(
        smtp_host="smtp.example.com",
        from_addr="contract@example.com",
        default_recipients=["target@example.com"],
        use_tls=False,
    )


def _jtoye_sink() -> NotificationSink:
    """Construct the sigantry-jtoye stub sink for contract battery (Plan 16-04).

    Skipped when sigantry-jtoye is not editable-installed in the active
    env. The stub satisfies the same NotificationSink contract as the
    three reference impls; SEAM-05 closure proves multi-org plugin
    authorship works.
    """
    pytest.importorskip("sigantry_jtoye.notifications")
    from sigantry_jtoye.notifications import JtoyeNotificationSink

    return JtoyeNotificationSink()


_FACTORIES = [
    pytest.param(_fake_sink, id="fake"),
    pytest.param(_teams_sink, id="teams"),
    pytest.param(_slack_sink, id="slack"),
    pytest.param(_email_sink, id="email"),
    pytest.param(
        _jtoye_sink,
        id="jtoye",
        marks=pytest.mark.skipif(
            importlib.util.find_spec("sigantry_jtoye") is None,
            reason="sigantry-jtoye not editable-installed in this env",
        ),
    ),
]


# ---------------------------------------------------------------------------
# Contract battery -- the lock on SEAM-01 + SEAM-05.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", _FACTORIES)
def test_notification_sink_protocol_membership(factory) -> None:
    """Every concrete impl + Fake satisfies ``NotificationSink`` runtime_checkable."""
    sink = factory()
    assert isinstance(sink, NotificationSink), (
        f"{type(sink).__name__} does not satisfy the NotificationSink Protocol"
    )
    # ``name`` MUST exist and be a non-empty string.
    assert isinstance(sink.name, str) and sink.name, (
        f"{type(sink).__name__}.name must be a non-empty string"
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_notification_sink_no_api_version_attr(factory) -> None:
    """Phase 11 precedent + RESEARCH §3 Open-Q-3: no api_version field at v3.0.

    Adding ``api_version`` to ANY single seam without coordinating
    across all ten seams is a contract break (would unbalance the
    symmetry recorded in protocols.py module docstring + ADR-0004).
    Cross-seam widening is a v3.1 candidate.
    """
    sink = factory()
    cls = type(sink)
    attr = getattr(cls, "api_version", None)
    assert not isinstance(attr, str), (
        f"{cls.__name__}.api_version must NOT be a class-level string at "
        "v3.0 -- cross-seam widening is deferred to v3.1 per planner-mapper "
        "resolution. See sigantry_core/protocols.py module docstring."
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_notification_sink_send_signature_compatible(factory) -> None:
    """``send()`` exists and accepts ``event`` + keyword-only ``channel``."""
    sink = factory()
    assert callable(getattr(sink, "send", None)), f"{type(sink).__name__}.send must be callable"
    sig = inspect.signature(sink.send)
    params = sig.parameters
    # The Protocol surface is `send(event, *, channel=None)`; the impl MUST
    # carry a parameter named ``event`` and a keyword-only ``channel`` param.
    assert "event" in params, (
        f"{type(sink).__name__}.send must accept an 'event' parameter; got {sorted(params)}"
    )
    assert "channel" in params, (
        f"{type(sink).__name__}.send must accept a 'channel' parameter; got {sorted(params)}"
    )
    assert params["channel"].kind == inspect.Parameter.KEYWORD_ONLY, (
        f"{type(sink).__name__}.send 'channel' must be keyword-only"
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_notification_sink_ping_returns_none(factory) -> None:
    """``ping()`` exists and returns None when the underlying transport is mocked."""
    sink = factory()
    assert callable(getattr(sink, "ping", None)), f"{type(sink).__name__}.ping must be callable"
    sig = inspect.signature(sink.ping)
    # Zero parameters (besides ``self``) -- ``ping`` is a no-arg health check.
    assert len(sig.parameters) == 0, (
        f"{type(sink).__name__}.ping must take no parameters; got {sorted(sig.parameters)}"
    )


# ---------------------------------------------------------------------------
# FakeNotificationSink standalone behaviour -- the recorded-invocation
# contract is the value of the double, so it gets a sanity test alongside
# the parametrised contract battery.
# ---------------------------------------------------------------------------


def test_fake_notification_sink_records_send_invocations() -> None:
    """The double records every ``(event, channel)`` pair into ``.calls``."""
    sink = FakeNotificationSink()
    event = NotificationEvent(title="t", body="b", level="info", properties={})
    sink.send(event)
    sink.send(event, channel="incidents")

    assert len(sink.calls) == 2
    assert sink.calls[0] == {"event": event, "channel": None}
    assert sink.calls[1] == {"event": event, "channel": "incidents"}


def test_fake_notification_sink_counts_ping_invocations() -> None:
    """The double counts ``ping`` calls in ``.pinged``."""
    sink = FakeNotificationSink()
    sink.ping()
    sink.ping()
    sink.ping()
    assert sink.pinged == 3
