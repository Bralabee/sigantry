"""Audit-2026-05-07 W2.4 -- falsifiability tests for the widened
:class:`sigantry_core.api.FabricDataOps` 11-seam surface.

The pre-W2.4 ``FabricDataOps`` had 6 seam kwargs (auth / telemetry /
dq_gate / deploy_profile / runbooks / capacity). The 5 added seams --
``notifications`` / ``secrets`` / ``approvals`` / ``work_items`` /
``pr_review_bot`` -- previously had Protocols + registry groups +
reference impls but no seat at the public composition root.

Tests below pin:

- All 11 kwargs are accepted at ``__init__``.
- All 11 attributes are populated by ``from_config`` when settings name
  the plugins.
- ``close()`` iterates all 11 seams (Closeable plugins all close, even
  if some raise).
- ``settings.work_items`` reuses the existing ``[release]`` namespace
  via ``settings.release.provider`` rather than introducing a parallel
  ``[work_items]`` slot operators would have to populate twice.
- The mapping between Protocols, registry groups, and TOML sections is
  consistent (cross-check against ``sigantry_core._dispatch``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sigantry_core._dispatch import GROUP_TO_TOML_KEY
from sigantry_core.api import FabricDataOps
from sigantry_core.registry import (
    GROUP_APPROVAL_GATES,
    GROUP_NOTIFICATION_SINKS,
    GROUP_PR_REVIEW_BOTS,
    GROUP_SECRET_STORES,
    GROUP_WORK_ITEM_PROVIDERS,
    Registry,
)

# --- Test doubles ------------------------------------------------------------


class _FakeNotifications:
    name = "fake-noti"

    def __init__(self) -> None:
        self.events: list[object] = []

    def notify(self, event: object) -> None:  # pragma: no cover
        self.events.append(event)


class _FakeSecrets:
    name = "fake-secrets"

    def __init__(self) -> None:
        self.set_calls: list[tuple[str, object]] = []

    def get(self, key: str) -> str:  # pragma: no cover
        return "secret"

    def set(self, key: str, value: object) -> None:  # pragma: no cover
        self.set_calls.append((key, value))


class _FakeApprovals:
    name = "fake-approvals"

    def request(self, ctx) -> object:  # pragma: no cover
        return None

    def wait(self, request, timeout_s: int = 3600):  # pragma: no cover
        return "approved"

    def ping(self) -> None:  # pragma: no cover
        return None


class _FakeWorkItems:
    name = "fake-work-items"

    def link_release(self, release_id, work_items, deploy_record):  # pragma: no cover
        return None

    def fetch_work_items(self, ids):  # pragma: no cover
        return []


class _FakePrBot:
    name = "fake-pr-bot"

    def ping(self) -> None:  # pragma: no cover
        return None

    def get_pr(self, pr_id):  # pragma: no cover
        return None

    def get_changed_files(self, pr_id):  # pragma: no cover
        return []

    def post_comment(self, pr_id, body):  # pragma: no cover
        return ""


class _ClosingFake:
    """Records close() calls so we can verify lifecycle reaches every seam."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


# --- __init__ contract -------------------------------------------------------


def test_fabric_dataops_accepts_all_11_seam_kwargs() -> None:
    """Every new seam kwarg lands as an attribute on the instance."""
    fdo = FabricDataOps(
        notifications=_FakeNotifications(),
        secrets=_FakeSecrets(),
        approvals=_FakeApprovals(),
        work_items=_FakeWorkItems(),
        pr_review_bot=_FakePrBot(),
    )
    assert isinstance(fdo.notifications, _FakeNotifications)
    assert isinstance(fdo.secrets, _FakeSecrets)
    assert isinstance(fdo.approvals, _FakeApprovals)
    assert isinstance(fdo.work_items, _FakeWorkItems)
    assert isinstance(fdo.pr_review_bot, _FakePrBot)


def test_fabric_dataops_partial_wiring_leaves_missing_seams_none() -> None:
    """Operators may wire only some of the 11 seams without crashing."""
    fdo = FabricDataOps(secrets=_FakeSecrets())
    # All other seams remain None -- the pre-W2.4 7-seam contract held the
    # same property; W2.4 widens the surface but preserves it.
    assert fdo.secrets is not None
    for attr in (
        "auth",
        "telemetry",
        "dq_gate",
        "deploy_profile",
        "runbooks",
        "capacity",
        "notifications",
        "approvals",
        "work_items",
        "pr_review_bot",
    ):
        assert getattr(fdo, attr) is None


# --- from_config wiring ------------------------------------------------------


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".fabric-dataops.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_from_config_resolves_all_5_new_seams(tmp_path: Path) -> None:
    """Settings-named plugins flow through ``resolve_seam`` for every new seam."""
    body = """\
[core]
tenant_id = "t-1"

[notifications]
sink = "fake-noti"

[secrets]
store = "fake-secrets"

[approvals]
gate = "fake-approvals"

[release]
provider = "fake-work-items"

[pr_review_bots]
bot = "fake-pr-bot"
"""
    path = _write_toml(tmp_path, body)
    reg = Registry()
    reg.register(GROUP_NOTIFICATION_SINKS, "fake-noti", _FakeNotifications)
    reg.register(GROUP_SECRET_STORES, "fake-secrets", _FakeSecrets)
    reg.register(GROUP_APPROVAL_GATES, "fake-approvals", _FakeApprovals)
    reg.register(GROUP_WORK_ITEM_PROVIDERS, "fake-work-items", _FakeWorkItems)
    reg.register(GROUP_PR_REVIEW_BOTS, "fake-pr-bot", _FakePrBot)

    fdo = FabricDataOps.from_config(path, registry=reg)

    assert isinstance(fdo.notifications, _FakeNotifications)
    assert isinstance(fdo.secrets, _FakeSecrets)
    assert isinstance(fdo.approvals, _FakeApprovals)
    assert isinstance(fdo.work_items, _FakeWorkItems)
    assert isinstance(fdo.pr_review_bot, _FakePrBot)


def test_from_config_work_items_reuses_release_namespace(tmp_path: Path) -> None:
    """``[release]`` (Phase-11 namespace) is the source-of-truth for work_items.

    Falsifiability: the W2.4 design choice intentionally avoids
    introducing a parallel ``[work_items]`` slot. This test pins that
    choice -- ``settings.release.provider`` is the only place
    ``WorkItemProvider`` plugins are named.
    """
    body = """\
[core]
tenant_id = "t-1"

[release]
provider = "fake-work-items"
"""
    path = _write_toml(tmp_path, body)
    reg = Registry()
    reg.register(GROUP_WORK_ITEM_PROVIDERS, "fake-work-items", _FakeWorkItems)

    fdo = FabricDataOps.from_config(path, registry=reg)
    assert isinstance(fdo.work_items, _FakeWorkItems)


def test_group_to_toml_key_routes_work_items_to_release() -> None:
    """``GROUP_WORK_ITEM_PROVIDERS`` maps to ``release`` in the TOML map.

    Pins the W2.4 design decision so a future refactor cannot silently
    introduce a parallel ``[work_items]`` namespace. The map is the
    single source of truth for plugin-config lookups.
    """
    assert GROUP_TO_TOML_KEY[GROUP_WORK_ITEM_PROVIDERS] == "release"


# --- close() lifecycle -------------------------------------------------------


def test_close_iterates_all_11_seams() -> None:
    """``close()`` reaches every seam with a Closeable shape."""
    seams: dict[str, _ClosingFake] = {
        name: _ClosingFake(name)
        for name in (
            "auth",
            "telemetry",
            "dq_gate",
            "deploy_profile",
            "runbooks",
            "capacity",
            "notifications",
            "secrets",
            "approvals",
            "work_items",
            "pr_review_bot",
        )
    }
    fdo = FabricDataOps(**seams)
    fdo.close()
    for name, double in seams.items():
        assert double.closed == 1, f"{name} was not closed"


def test_close_continues_after_seam_raises() -> None:
    """One bad ``close()`` does not prevent the other 10 seams from closing."""

    class _Boom(_ClosingFake):
        def close(self) -> None:
            self.closed += 1
            raise RuntimeError("boom")

    seams: dict[str, _ClosingFake] = {
        name: _ClosingFake(name)
        for name in (
            "auth",
            "telemetry",
            "dq_gate",
            "deploy_profile",
            "runbooks",
            "capacity",
            "notifications",
            "approvals",
            "work_items",
            "pr_review_bot",
        )
    }
    seams["secrets"] = _Boom("secrets")
    fdo = FabricDataOps(**seams)

    fdo.close()  # must not propagate the RuntimeError
    for name, double in seams.items():
        assert double.closed == 1, f"{name} was not closed even after sibling failure"


# --- Cross-check: every Protocol seam has a registry slot --------------------


@pytest.mark.parametrize(
    "group, attr, settings_path",
    [
        (GROUP_NOTIFICATION_SINKS, "notifications", "notifications.sink"),
        (GROUP_SECRET_STORES, "secrets", "secrets.store"),
        (GROUP_APPROVAL_GATES, "approvals", "approvals.gate"),
        (GROUP_WORK_ITEM_PROVIDERS, "work_items", "release.provider"),
        (GROUP_PR_REVIEW_BOTS, "pr_review_bot", "pr_review_bots.bot"),
    ],
)
def test_each_new_seam_attribute_exists(group: str, attr: str, settings_path: str) -> None:
    """Each new seam has an attribute and the documented settings path resolves."""
    fdo = FabricDataOps()
    assert hasattr(fdo, attr), f"FabricDataOps missing attribute `{attr}`"
    # The mapping in _dispatch.GROUP_TO_TOML_KEY must include this group.
    assert group in GROUP_TO_TOML_KEY, f"_dispatch.GROUP_TO_TOML_KEY missing entry for {group!r}"
    # And the settings_path documented above is a valid two-level dotted
    # accessor on a fresh ToolkitSettings (where every slot defaults to None).
    from sigantry_core.config import ToolkitSettings

    settings = ToolkitSettings(core={"tenant_id": "t-1"})
    obj = settings
    for piece in settings_path.split("."):
        assert hasattr(obj, piece), (
            f"settings has no attribute path `{settings_path}` (stopped at `{piece}`)"
        )
        obj = getattr(obj, piece)
    # Defaulted to None on a fresh settings object.
    assert obj is None
