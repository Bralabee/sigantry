"""Deprecation-shim tests for ``sigantry_core.sync.notifications`` (Plan 16-01).

Plan 16-01 (Phase 16 / SEAM-01) migrated the three Phase 13 reference
implementations from ``sigantry_core/sync/notifications.py`` to
``sigantry_core/notifications/{teams,slack,email}.py`` as first-class
:class:`sigantry_core.protocols.NotificationSink` implementations.

The old module path is retained as a deprecation shim through v3.0 per
ADR-0011 / 16-RESEARCH.md §Open-Q-1 (drops in v3.1). This file's tests
verify the shim's contract:

1. Importing the shim emits a :class:`DeprecationWarning`.
2. The Phase 13 names (``TeamsWebhookSink`` / ``SlackWebhookSink`` /
   ``EmailSmtpSink``) resolve to the new Phase 16 classes via aliases.
3. ``NotificationSinkProtocol`` aliases the new
   :class:`sigantry_core.protocols.NotificationSink` Protocol.
4. ``sink_from_env()`` re-exports from the new location.

The Phase 13 ``post(drift, workspace_id=...)`` API is REMOVED at the
class level -- the new Protocol surface is ``send(event)``. Callsites
(``sigantry_core.sync._notify_main``) were updated in lockstep.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest


def _force_reimport() -> None:
    """Drop the cached ``sigantry_core.sync.notifications`` module.

    Required because ``warnings.warn(..., DeprecationWarning)`` only
    fires the first time a module is imported in a given process; tests
    that need to capture the warning must clear the import cache first.
    """
    sys.modules.pop("sigantry_core.sync.notifications", None)


def test_sync_notifications_module_emits_deprecation_warning_on_import() -> None:
    """The shim emits a DeprecationWarning at module-import time.

    External consumers who continue importing from the legacy path
    receive a CHANGELOG-grade signal to migrate before v3.1 drops the
    shim.
    """
    _force_reimport()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import sigantry_core.sync.notifications  # noqa: F401

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecation_warnings, (
        "expected at least one DeprecationWarning when importing "
        "sigantry_core.sync.notifications; got: "
        f"{[(w.category.__name__, str(w.message)) for w in caught]}"
    )
    msg = str(deprecation_warnings[0].message)
    assert "sigantry_core.notifications" in msg, msg


def test_sync_notifications_shim_aliases_resolve_to_phase16_classes() -> None:
    """Phase 13 names alias to the new Phase 16 classes."""
    _force_reimport()
    # Suppress the deprecation warning during the import; we only care
    # about alias correctness here.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim = importlib.import_module("sigantry_core.sync.notifications")

    from sigantry_core.notifications import (
        EmailNotificationSink,
        SlackNotificationSink,
        TeamsNotificationSink,
    )
    from sigantry_core.protocols import NotificationSink

    assert shim.TeamsWebhookSink is TeamsNotificationSink
    assert shim.SlackWebhookSink is SlackNotificationSink
    assert shim.EmailSmtpSink is EmailNotificationSink
    assert shim.NotificationSinkProtocol is NotificationSink


def test_sync_notifications_shim_sink_from_env_is_phase16_factory() -> None:
    """``sink_from_env`` re-exports from ``sigantry_core.notifications``."""
    _force_reimport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim = importlib.import_module("sigantry_core.sync.notifications")

    from sigantry_core.notifications import sink_from_env as new_sink_from_env

    assert shim.sink_from_env is new_sink_from_env


def test_sync_notifications_shim_does_not_import_httpx() -> None:
    """The shim is a pure re-export; no transport / no httpx import.

    Confirms the legacy ``[tool.ruff.lint.per-file-ignores]`` entry
    (``sigantry_core/sync/notifications.py = ["TID251"]``) was correctly
    removed by Plan 16-01 -- the shim's module body has no httpx call.
    """
    _force_reimport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim = importlib.import_module("sigantry_core.sync.notifications")

    # The module's globals do NOT carry httpx as a name.
    assert "httpx" not in shim.__dict__, (
        "shim should be a pure re-export with no httpx import; got "
        f"{[k for k in shim.__dict__ if not k.startswith('_')]}"
    )


def test_sync_notifications_shim_factory_resolves_teams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shim's sink_from_env still dispatches by env var (Phase 13 contract)."""
    _force_reimport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim = importlib.import_module("sigantry_core.sync.notifications")

    monkeypatch.setenv("SIGANTRY_NOTIFICATION_SINK", "teams")
    monkeypatch.setenv("SIGANTRY_TEAMS_WEBHOOK", "https://teams.example/test")
    sink = shim.sink_from_env()
    # Aliases resolve identically -- isinstance against either name works.
    assert isinstance(sink, shim.TeamsWebhookSink)
    from sigantry_core.notifications import TeamsNotificationSink

    assert isinstance(sink, TeamsNotificationSink)
