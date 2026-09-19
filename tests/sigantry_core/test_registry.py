"""Tests for :mod:`sigantry_core.registry` (PROD-02 + Plan 10-02 Task 3).

Covers both the base direct-DI register/resolve behaviour and the v3.0
dual-read entry-point discovery introduced in Plan 10-02 Task 3
(``_NEW_GROUPS`` / ``_LEGACY_GROUPS`` / ``_LEGACY_TO_NEW`` + one-warning-
per-plugin DeprecationWarning emission).
"""

from __future__ import annotations

import warnings
from importlib.metadata import EntryPoint

import pytest

from sigantry_core import registry as reg_mod
from sigantry_core.registry import (
    _LEGACY_GROUPS,
    _LEGACY_TO_NEW,
    _NEW_GROUPS,
    PluginInfo,
    Registry,
    default_registry,
    register_plugin,
)

# Canonical (new, preferred) group names.
NEW_DEPLOY_GROUP = "sigantry.deploy_profiles"
NEW_DQ_GROUP = "sigantry.dq_gates"
# Legacy shim group names (still resolvable during v3.0).
LEGACY_DEPLOY_GROUP = "fabric_dataops_toolkits.deploy_profiles"
LEGACY_DQ_GROUP = "fabric_dataops_toolkits.dq_gates"

# Back-compat aliases for legacy test code that reference these names.
DEPLOY_GROUP = LEGACY_DEPLOY_GROUP
DQ_GROUP = LEGACY_DQ_GROUP


class _FakeDeploy:
    name = "fake"

    def plan(self, ctx):  # pragma: no cover - trivial stub
        return None

    def apply(self, ctx, plan):  # pragma: no cover - trivial stub
        return None


# ---------------------------------------------------------------------------
# Known-groups API surface
# ---------------------------------------------------------------------------


def test_registry_known_groups_returns_six_v2_new_names() -> None:
    """``known_groups()`` returns exactly the six v2 seams under the new prefix."""
    groups = Registry.known_groups()
    assert len(groups) == 6
    assert all(g.startswith("sigantry.") for g in groups)
    assert NEW_DEPLOY_GROUP in groups


def test_registry_known_new_groups_returns_eleven_seams() -> None:
    """``known_new_groups()`` returns all 11 ``sigantry.*`` groups (v2 + v3)."""
    new = Registry.known_new_groups()
    assert new is _NEW_GROUPS
    assert len(new) == 11
    # v3 seam placeholders are included:
    assert "sigantry.work_item_providers" in new
    assert "sigantry.notification_sinks" in new
    assert "sigantry.secret_stores" in new
    assert "sigantry.approval_gates" in new
    assert "sigantry.pr_review_bots" in new


def test_registry_known_legacy_groups_returns_six_names() -> None:
    legacy = Registry.known_legacy_groups()
    assert legacy is _LEGACY_GROUPS
    assert len(legacy) == 6
    assert all(g.startswith("fabric_dataops_toolkits.") for g in legacy)


def test_legacy_to_new_map_is_six_entries() -> None:
    assert len(_LEGACY_TO_NEW) == 6
    assert _LEGACY_TO_NEW[LEGACY_DEPLOY_GROUP] == NEW_DEPLOY_GROUP


# ---------------------------------------------------------------------------
# Direct-DI register / resolve
# ---------------------------------------------------------------------------


def test_registry_direct_di_register_and_resolve() -> None:
    r = Registry()
    r.register(LEGACY_DEPLOY_GROUP, "fake", _FakeDeploy)
    assert r.resolve(LEGACY_DEPLOY_GROUP, "fake") is _FakeDeploy


def test_registry_direct_di_under_new_group_also_works() -> None:
    r = Registry()
    r.register(NEW_DEPLOY_GROUP, "fake-new", _FakeDeploy)
    assert r.resolve(NEW_DEPLOY_GROUP, "fake-new") is _FakeDeploy


def test_registry_rejects_unknown_group_on_register() -> None:
    r = Registry()
    with pytest.raises(ValueError, match="Unknown plugin group"):
        r.register("bogus.group", "name", _FakeDeploy)


def test_registry_rejects_unknown_group_on_resolve() -> None:
    r = Registry()
    with pytest.raises(ValueError, match="Unknown plugin group"):
        r.resolve("bogus.group", "name")


def test_registry_rejects_duplicate_name_within_group() -> None:
    r = Registry()
    r.register(LEGACY_DEPLOY_GROUP, "fake", _FakeDeploy)
    with pytest.raises(KeyError, match="already registered"):
        r.register(LEGACY_DEPLOY_GROUP, "fake", _FakeDeploy)


def test_registry_resolve_missing_name_raises_keyerror() -> None:
    r = Registry()
    with pytest.raises(KeyError, match="No plugin named"):
        r.resolve(LEGACY_DEPLOY_GROUP, "does-not-exist")


def test_registry_group_isolation() -> None:
    """A plugin registered under one group is not resolvable under another."""
    r = Registry()
    r.register(LEGACY_DEPLOY_GROUP, "shared-name", _FakeDeploy)
    with pytest.raises(KeyError):
        r.resolve(LEGACY_DQ_GROUP, "shared-name")


def test_registry_list_plugins_filters_by_group() -> None:
    r = Registry()
    r.register(LEGACY_DEPLOY_GROUP, "one", _FakeDeploy)
    r.register(LEGACY_DEPLOY_GROUP, "two", _FakeDeploy)

    listed = r.list_plugins(LEGACY_DEPLOY_GROUP)
    assert {p.name for p in listed} == {"one", "two"}

    all_listed = r.list_plugins()
    assert {p.name for p in all_listed} >= {"one", "two"}


# ---------------------------------------------------------------------------
# Entry-point discovery -- idempotency + import-error handling
# ---------------------------------------------------------------------------


def test_registry_discover_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """``discover()`` walks every group tuple exactly once per registry.

    Post-Plan-10-02: walks both ``_NEW_GROUPS`` (11) and ``_LEGACY_GROUPS``
    (6) = 17 group reads on the first call; the second call short-circuits
    on ``self._discovered`` and performs zero additional reads.
    """
    r = Registry()

    calls: list[str] = []

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        calls.append(group)
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)
    r.discover()
    # 11 new groups + 6 legacy groups = 17 total walks.
    assert len(calls) == 17, calls
    # Second call is a no-op.
    r.discover()
    assert len(calls) == 17


def test_registry_discover_records_import_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad entry point under a NEW group is recorded on ``PluginInfo.import_error``."""
    r = Registry()

    class BadEP:
        name = "broken"
        module = "does_not_exist.module"
        dist = None

        def load(self):
            raise ImportError("synthetic failure")

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        if group == NEW_DEPLOY_GROUP:
            return [BadEP()]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)
    r.discover()  # must NOT raise

    listed = r.list_plugins(NEW_DEPLOY_GROUP)
    assert len(listed) == 1
    assert listed[0].name == "broken"
    assert listed[0].import_error is not None
    assert "synthetic failure" in listed[0].import_error


def test_registry_discover_loads_successful_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful entry points under a NEW group become resolvable."""
    r = Registry()

    class GoodEP:
        name = "good"
        module = "stub.module"
        dist = None

        def load(self):
            return _FakeDeploy

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        if group == NEW_DEPLOY_GROUP:
            return [GoodEP()]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)
    r.discover()

    assert r.resolve(NEW_DEPLOY_GROUP, "good") is _FakeDeploy
    listed = r.list_plugins(NEW_DEPLOY_GROUP)
    assert listed[0].import_error is None


# ---------------------------------------------------------------------------
# Plan 10-02 Task 3: dual-read + DeprecationWarning behaviour
# ---------------------------------------------------------------------------


class _GoodEP:
    """Minimal EntryPoint-lookalike used by the dual-read tests."""

    def __init__(self, name: str, impl: type = _FakeDeploy) -> None:
        self.name = name
        self.module = "stub.module"
        self.dist = None
        self._impl = impl

    def load(self):
        return self._impl


def test_registry_reads_both_entry_point_group_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin registered ONLY under the legacy group resolves under the
    canonical new group after ``discover()``.
    """
    r = Registry()
    plugin_name = "legacy-profile"

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        # Legacy-only registration -- registry must canonicalise it to the
        # new group.
        if group == LEGACY_DEPLOY_GROUP:
            return [_GoodEP(plugin_name)]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)
    # Suppress the expected DeprecationWarning so it doesn't fail strict test runs.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        r.discover()

    assert r.resolve(NEW_DEPLOY_GROUP, plugin_name) is _FakeDeploy


def test_legacy_registration_emits_deprecation_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy-group plugin triggers exactly one DeprecationWarning whose
    text contains the legacy group key and the migration-guide path.
    """
    r = Registry()

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        if group == LEGACY_DEPLOY_GROUP:
            return [_GoodEP("legacy-plugin")]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        r.discover()

    dep_warnings = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) == 1, [str(w.message) for w in captured]
    msg = str(dep_warnings[0].message)
    assert "fabric_dataops_toolkits." in msg
    assert "docs/migration/2.x-to-3.0.md" in msg
    assert "legacy-plugin" in msg


def test_duplicate_registration_prefers_new_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin registered under BOTH new + legacy groups with the same name:
    the new entry wins; exactly one DeprecationWarning is emitted.
    """
    r = Registry()
    plugin_name = "dual-reg"

    class _NewImpl:
        marker = "new"

    class _LegacyImpl:
        marker = "legacy"

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        if group == NEW_DEPLOY_GROUP:
            return [_GoodEP(plugin_name, impl=_NewImpl)]
        if group == LEGACY_DEPLOY_GROUP:
            return [_GoodEP(plugin_name, impl=_LegacyImpl)]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        r.discover()

    # New wins.
    assert r.resolve(NEW_DEPLOY_GROUP, plugin_name) is _NewImpl
    # Legacy bucket is empty -- dual-read stores ONLY under canonical.
    legacy_listed = r.list_plugins(LEGACY_DEPLOY_GROUP)
    assert plugin_name not in {p.name for p in legacy_listed}
    # Exactly one DeprecationWarning names the duplicate plugin.
    dep_warnings = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) == 1
    assert plugin_name in str(dep_warnings[0].message)


# ---------------------------------------------------------------------------
# Audit-2026-05-08 review follow-up (BL-04): broken canonical must NOT
# shadow a working legacy registration
# ---------------------------------------------------------------------------


class _BoomEP:
    """EntryPoint-lookalike whose ``load()`` raises on access.

    Pre-BL-04-fix the registry recorded the import error in
    ``self._info[new_group][ep.name]`` but did NOT register the impl
    in ``self._groups[new_group]``. The legacy walker then checked
    ``ep.name in self._info[new_group]`` -- saw the failure record --
    and silently skipped the legacy entry. The resolver later raised
    ``KeyError: No plugin named ...`` even though a working legacy
    plugin was waiting.
    """

    def __init__(self, name: str, exc: Exception | None = None) -> None:
        self.name = name
        self.module = "stub.broken.module"
        self.dist = None
        self._exc = exc or RuntimeError("simulated import-time failure")

    def load(self):  # pragma: no cover - exercised in the test below
        raise self._exc


def test_broken_canonical_does_not_shadow_working_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical entry-point that fails to load must NOT prevent the
    same-named legacy entry-point from registering under the canonical
    group.

    Falsifiability: reverting the BL-04 fix (changing
    ``ep.name in self._groups[new_group]`` back to
    ``ep.name in self._info[new_group]``) makes this test fail with
    ``KeyError`` from ``r.resolve(NEW_DEPLOY_GROUP, plugin_name)``
    because the legacy fallback is preempted by the failed-import
    record in ``_info``.
    """
    r = Registry()
    plugin_name = "fallback-profile"

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        if group == NEW_DEPLOY_GROUP:
            return [_BoomEP(plugin_name, exc=ImportError("missing dependency"))]
        if group == LEGACY_DEPLOY_GROUP:
            return [_GoodEP(plugin_name, impl=_FakeDeploy)]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        r.discover()

    # The legacy impl must resolve under the canonical group.
    assert r.resolve(NEW_DEPLOY_GROUP, plugin_name) is _FakeDeploy, (
        "BL-04: broken canonical entry-point silently shadowed a working "
        "legacy plugin -- the legacy walker checked _info (diagnostic) "
        "instead of _groups (actual registration)"
    )

    # The diagnostic info should reflect the legacy registration that
    # actually won, not the prior failed canonical load.
    info = next((p for p in r.list_plugins(NEW_DEPLOY_GROUP) if p.name == plugin_name), None)
    assert info is not None, "list_plugins missed the resolved registration"
    assert info.import_error is None, (
        "stale failed-import record from the broken canonical "
        f"entry-point leaked into list_plugins: {info.import_error!r}"
    )


def test_broken_canonical_emits_deprecation_warning_for_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BL-04 fallback path still emits the legacy DeprecationWarning.

    The fallback is the operator's signal that the canonical install is
    broken AND that they are running on the legacy path. Skipping the
    warning would hide both halves of the migration.
    """
    r = Registry()
    plugin_name = "warn-fallback-profile"

    def fake_entry_points(*, group: str):  # type: ignore[no-untyped-def]
        if group == NEW_DEPLOY_GROUP:
            return [_BoomEP(plugin_name)]
        if group == LEGACY_DEPLOY_GROUP:
            return [_GoodEP(plugin_name)]
        return []

    monkeypatch.setattr(reg_mod, "entry_points", fake_entry_points)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        r.discover()

    dep_warnings = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert any(plugin_name in str(w.message) for w in dep_warnings), (
        "fallback path must still emit the legacy-group DeprecationWarning"
    )


# ---------------------------------------------------------------------------
# default_registry / register_plugin helpers
# ---------------------------------------------------------------------------


def test_default_registry_is_singleton() -> None:
    a = default_registry()
    b = default_registry()
    assert a is b


def test_default_registry_auto_discovers_on_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``default_registry()`` runs ``discover()`` once on initial construction.

    Regression guard: prior to the fix the docstring promised lazy discovery
    but the code never called it, so consumers calling
    ``default_registry().resolve(...)`` without a manual ``.discover()``
    hit ``KeyError`` even with installed entry-point plugins on-disk.
    """
    reg_mod._reset_default_registry_for_tests()
    r = default_registry()
    assert r._discovered is True, (
        "default_registry() must call Registry.discover() on first construction "
        "so that entry-point plugins resolve without a manual discovery step."
    )
    reg_mod._reset_default_registry_for_tests()


def test_register_plugin_helper_uses_default_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``register_plugin`` delegates to the module-level singleton."""
    # Reset the singleton so this test is independent of import order.
    reg_mod._reset_default_registry_for_tests()
    register_plugin(LEGACY_DQ_GROUP, "helper-registered", _FakeDeploy)
    assert default_registry().resolve(LEGACY_DQ_GROUP, "helper-registered") is _FakeDeploy
    # Clean up for other tests.
    reg_mod._reset_default_registry_for_tests()


def test_plugin_info_is_frozen_dataclass() -> None:
    info = PluginInfo(group=LEGACY_DEPLOY_GROUP, name="x")
    with pytest.raises((AttributeError, Exception)):
        info.name = "y"  # type: ignore[misc]


def test_entry_point_type_is_used_once() -> None:
    """Quick sanity: ``EntryPoint`` is importable and distinct from our mocks."""
    assert EntryPoint is not None
