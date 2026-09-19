"""Plugin registry (PROD-02) with dual-read of v2 legacy + v3 new entry-point groups.

Supports two registration paths:

1. **Entry-point discovery** via ``importlib.metadata.entry_points``. Plugins
   declare themselves in their own ``pyproject.toml``
   ``[project.entry-points."sigantry.*"]`` (new, preferred) or
   ``[project.entry-points."fabric_dataops_toolkits.*"]`` (legacy shim; emits
   ``DeprecationWarning`` once per plugin and drops in v3.1 per ADR-0011).

2. **Direct-DI registration** via ``Registry.register(group, name, impl)`` --
   useful for tests and advanced embedding where entry points are not available.

Entry-point load failures are **recorded, not raised**: a plugin that fails to
import leaves a ``PluginInfo`` entry with ``import_error`` populated so the
``doctor`` CLI (Plan 08-06) can surface it without bringing down the whole app.

``default_registry()`` returns a lazily-discovered module-level singleton.

Dual-read canonicalisation rules (Plan 10-02 Task 3):

- ``sigantry.<seam>`` is the canonical (preferred) entry-point group name for
  v3.0 onward. ``fabric_dataops_toolkits.<seam>`` is the legacy shim name.
- The registry eagerly reads the new groups first. It then walks the legacy
  groups; for each legacy hit, the plugin is registered under the CANONICAL
  new group name (never under the legacy group's key).
- If the same plugin ``name`` is registered under BOTH the new group and the
  legacy group, the NEW entry wins; a ``DeprecationWarning`` is emitted once
  naming the plugin and the preferred entry point.
- If a plugin is registered ONLY under a legacy group, a single
  ``DeprecationWarning`` fires once per process per plugin pointing to
  ``docs/migration/2.x-to-3.0.md`` (guide authored in Plan 10-07).
"""

from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points

# -- entry-point group sets ------------------------------------------------
#
# Audit-2026-05-07 W2.1: every reference to an entry-point group across
# the codebase MUST come from one of the named constants below (or the
# Registry.GROUP_* class attrs that mirror them). A grep meta-gate at
# tests/prereqs/test_no_group_literals.py forbids new string-literal
# references to either ``sigantry.<seam>`` or
# ``fabric_dataops_toolkits.<seam>`` outside this module. Dispatchers
# previously hardcoded legacy names (``fabric_dataops_toolkits.*``)
# while api.py used canonical (``sigantry.*``); both worked through
# runtime aliasing but the inconsistency was a v3.1-shim-drop hazard.

GROUP_DEPLOY_PROFILES: str = "sigantry.deploy_profiles"
GROUP_DQ_GATES: str = "sigantry.dq_gates"
GROUP_TELEMETRY_SINKS: str = "sigantry.telemetry_sinks"
GROUP_AUTH_PROVIDERS: str = "sigantry.auth_providers"
GROUP_RUNBOOK_REGISTRIES: str = "sigantry.runbook_registries"
GROUP_CAPACITY_POLICIES: str = "sigantry.capacity_policies"
GROUP_WORK_ITEM_PROVIDERS: str = "sigantry.work_item_providers"
GROUP_NOTIFICATION_SINKS: str = "sigantry.notification_sinks"
GROUP_SECRET_STORES: str = "sigantry.secret_stores"
GROUP_APPROVAL_GATES: str = "sigantry.approval_gates"
GROUP_PR_REVIEW_BOTS: str = "sigantry.pr_review_bots"

_NEW_GROUPS: tuple[str, ...] = (
    # v2 seams under the new "sigantry." prefix (preferred during v3.0).
    GROUP_DEPLOY_PROFILES,
    GROUP_DQ_GATES,
    GROUP_TELEMETRY_SINKS,
    GROUP_AUTH_PROVIDERS,
    GROUP_RUNBOOK_REGISTRIES,
    GROUP_CAPACITY_POLICIES,
    # v3 seams -- reserved namespace declarations. First implementations land
    # in Phase 11 (WorkItemProvider), Phase 14 (PrReviewBot), and Phase 16
    # (NotificationSink + SecretStore + ApprovalGate). See
    # docs/reference/seam-map.md.
    GROUP_WORK_ITEM_PROVIDERS,
    GROUP_NOTIFICATION_SINKS,
    GROUP_SECRET_STORES,
    GROUP_APPROVAL_GATES,
    GROUP_PR_REVIEW_BOTS,
)

_LEGACY_GROUPS: tuple[str, ...] = (
    "fabric_dataops_toolkits.deploy_profiles",
    "fabric_dataops_toolkits.dq_gates",
    "fabric_dataops_toolkits.telemetry_sinks",
    "fabric_dataops_toolkits.auth_providers",
    "fabric_dataops_toolkits.runbook_registries",
    "fabric_dataops_toolkits.capacity_policies",
)

# Map each legacy group to its canonical new name so dual-registered plugins
# can be deduplicated and legacy-only plugins are stored under their canonical
# new group.
_LEGACY_TO_NEW: dict[str, str] = {
    "fabric_dataops_toolkits.deploy_profiles": "sigantry.deploy_profiles",
    "fabric_dataops_toolkits.dq_gates": "sigantry.dq_gates",
    "fabric_dataops_toolkits.telemetry_sinks": "sigantry.telemetry_sinks",
    "fabric_dataops_toolkits.auth_providers": "sigantry.auth_providers",
    "fabric_dataops_toolkits.runbook_registries": "sigantry.runbook_registries",
    "fabric_dataops_toolkits.capacity_policies": "sigantry.capacity_policies",
}

# Full group-name tuple exposed for back-compat. Covers both new + legacy
# groups so callers that accept BOTH names (register under legacy name,
# resolve under legacy name) continue to work during v3.0.
_GROUPS: tuple[str, ...] = _NEW_GROUPS + _LEGACY_GROUPS

# Deprecation warning template (Plan 10-02 Task 3 acceptance criterion
# requires this exact surface shape so the test can match).
_LEGACY_WARNING_TEMPLATE = (
    "Plugin {name!r} is registered under the legacy entry-point group "
    "{legacy_group!r}. This shim support drops in Sigantry v3.1. "
    "Migrate to {new_group!r}. See docs/migration/2.x-to-3.0.md."
)


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Diagnostic record for one (attempted) plugin registration."""

    group: str
    name: str
    module: str | None = None
    version: str | None = None
    import_error: str | None = None


class Registry:
    """Plugin registry supporting entry-point discovery and direct DI."""

    def __init__(self) -> None:
        # Per-group: name -> implementation (class, instance, or factory).
        # Buckets exist for BOTH the new + legacy group names so callers can
        # ``register(legacy_group, ...)`` or ``resolve(legacy_group, ...)``
        # during the v3.0 shim window.
        self._groups: dict[str, dict[str, object]] = {g: {} for g in _GROUPS}
        # Parallel diagnostics mirror used by ``list_plugins``.
        self._info: dict[str, dict[str, PluginInfo]] = {g: {} for g in _GROUPS}
        # De-dupe set of plugin names that have already received a
        # DeprecationWarning this process (one warning per name per process).
        self._legacy_warned: set[str] = set()
        self._discovered = False
        self._lock = threading.Lock()

    # --- known groups ------------------------------------------------------

    @staticmethod
    def known_groups() -> tuple[str, ...]:
        """Return the six ``sigantry.<v2-seam>`` entry-point group names.

        Back-compat-friendly: returns only the six v2 seams under the NEW
        ``sigantry.`` prefix. Callers that need the full v2+v3 new-group set
        should call ``known_new_groups()``; callers that need the legacy
        names should introspect ``_LEGACY_GROUPS`` directly.
        """
        return _NEW_GROUPS[:6]

    @staticmethod
    def known_new_groups() -> tuple[str, ...]:
        """Return the full v2+v3 ``sigantry.*`` entry-point group tuple (11 groups)."""
        return _NEW_GROUPS

    @staticmethod
    def known_legacy_groups() -> tuple[str, ...]:
        """Return the six legacy ``fabric_dataops_toolkits.*`` entry-point groups."""
        return _LEGACY_GROUPS

    def _require_known_group(self, group: str) -> None:
        if group not in self._groups:
            raise ValueError(f"Unknown plugin group {group!r}. Known groups: {_GROUPS!r}")

    # --- registration ------------------------------------------------------

    def register(self, group: str, name: str, impl: object) -> None:
        """Register a plugin directly (no entry point required).

        Raises ``ValueError`` if ``group`` is not one of the known groups
        (either new or legacy). Raises ``KeyError`` if ``name`` is already
        registered in that group.
        """
        self._require_known_group(group)
        bucket = self._groups[group]
        if name in bucket:
            raise KeyError(f"Plugin {name!r} is already registered under group {group!r}")
        bucket[name] = impl
        self._info[group][name] = PluginInfo(
            group=group,
            name=name,
            module=getattr(impl, "__module__", None),
        )

    # --- resolution --------------------------------------------------------

    def resolve(self, group: str, name: str) -> object:
        """Return the implementation previously registered under (group, name).

        Raises ``ValueError`` if ``group`` is not a known group.
        Raises ``KeyError`` if ``name`` is not registered in that group.
        """
        self._require_known_group(group)
        bucket = self._groups[group]
        if name not in bucket:
            raise KeyError(
                f"No plugin named {name!r} registered under group {group!r}. "
                f"Known names: {sorted(bucket.keys())!r}"
            )
        return bucket[name]

    # --- entry-point discovery --------------------------------------------

    def discover(self) -> None:
        """Populate this registry from ``importlib.metadata.entry_points``.

        Dual-reads both ``sigantry.*`` (new, preferred) and
        ``fabric_dataops_toolkits.*`` (legacy shim) entry-point group sets.
        New entries win ties; legacy-only plugins emit exactly one
        ``DeprecationWarning`` per plugin pointing at
        ``docs/migration/2.x-to-3.0.md``.

        Idempotent: safe to call multiple times (second call is a no-op).
        Per-plugin ``ImportError``\\s are recorded on ``PluginInfo`` rather
        than raised, so one bad plugin cannot brick the whole process.
        """
        with self._lock:
            if self._discovered:
                return

            # 1. Walk NEW groups first so their entries are registered as
            #    canonical.
            for group in _NEW_GROUPS:
                for ep in self._entry_points_for(group):
                    self._load_entry_point(group, ep)

            # 2. Walk LEGACY groups. Each hit is stored under the CANONICAL
            #    new group (via _LEGACY_TO_NEW). Plugins already present
            #    under the new group lose; the duplicate + warning path
            #    handles that case.
            for legacy_group in _LEGACY_GROUPS:
                new_group = _LEGACY_TO_NEW[legacy_group]
                for ep in self._entry_points_for(legacy_group):
                    self._load_legacy_entry_point(legacy_group, new_group, ep)

            self._discovered = True

    @staticmethod
    def _entry_points_for(group: str):
        """Return entry_points for a given group, tolerating old Python versions."""
        try:
            return list(entry_points(group=group))
        except TypeError:
            # Python <3.10 shim: entry_points()[group] returned a tuple.
            # We pin Python >=3.11 (pyproject.toml), so this branch is
            # defensive only.
            return list(entry_points().get(group, []))  # type: ignore[attr-defined]

    def _load_entry_point(self, group: str, ep: EntryPoint) -> None:
        """Register a plugin under the CANONICAL (new) group name.

        Called directly for ``sigantry.*`` groups; called via
        ``_load_legacy_entry_point`` for ``fabric_dataops_toolkits.*`` groups
        after remapping the target group.
        """
        if ep.name in self._info[group]:
            # Duplicate name across multiple distributions -- first wins; log
            # the collision as an import error on the second.
            self._info[group][ep.name] = PluginInfo(
                group=group,
                name=ep.name,
                module=ep.module,
                import_error=(
                    f"duplicate entry-point name {ep.name!r} under group {group!r} (first-wins)"
                ),
            )
            return
        try:
            impl = ep.load()
        except Exception as exc:  # pragma: no cover - defensive catch-all
            self._info[group][ep.name] = PluginInfo(
                group=group,
                name=ep.name,
                module=ep.module,
                import_error=f"{type(exc).__name__}: {exc}",
            )
            return
        self._groups[group][ep.name] = impl
        self._info[group][ep.name] = PluginInfo(
            group=group,
            name=ep.name,
            module=ep.module,
            version=getattr(ep.dist, "version", None) if ep.dist else None,
        )

    def _load_legacy_entry_point(self, legacy_group: str, new_group: str, ep: EntryPoint) -> None:
        """Handle a legacy-group entry point.

        If the plugin is ACTUALLY REGISTERED under the canonical new group
        with the same name (i.e. the canonical ``ep.load()`` succeeded and
        produced an impl), prefer the new entry and emit a
        ``DeprecationWarning`` naming the duplicate. Otherwise, register
        the plugin under the canonical new group and emit the legacy-only
        ``DeprecationWarning`` (once per plugin).

        Audit-2026-05-08 review follow-up (BL-04): pre-fix this method
        tested ``ep.name in self._info[new_group]`` -- the *diagnostic*
        info map. ``_load_entry_point`` populates ``_info`` even when
        ``ep.load()`` raises (recording the import error), but does NOT
        populate ``_groups``. The pre-fix check therefore treated a
        broken canonical install as if it had registered, returning
        before the legacy fallback could register under the canonical
        group. A working legacy plugin was silently shadowed by a
        broken canonical one -- the resolver would later raise
        ``KeyError: No plugin named 'X' registered under group 'Y'``
        even though a perfectly good legacy registration was waiting.
        Test against ``_groups`` (the actual registration map) instead.
        """
        if ep.name in self._groups[new_group]:
            # Dual-registered AND canonical loaded: prefer new, warn once.
            self._emit_legacy_warning(ep.name, legacy_group, new_group)
            return
        # Legacy-only OR canonical-failed-to-load -- register the legacy
        # impl under the canonical new group + warn. ``_load_entry_point``
        # may collide with a stale ``_info`` entry written by a failed
        # canonical load; that branch returns early via the duplicate-
        # name check, so we clear the stale info first to make the
        # legacy registration win.
        self._info[new_group].pop(ep.name, None)
        self._emit_legacy_warning(ep.name, legacy_group, new_group)
        self._load_entry_point(new_group, ep)

    def _emit_legacy_warning(self, name: str, legacy_group: str, new_group: str) -> None:
        """Emit a ``DeprecationWarning`` once per plugin name per process."""
        if name in self._legacy_warned:
            return
        self._legacy_warned.add(name)
        warnings.warn(
            _LEGACY_WARNING_TEMPLATE.format(
                name=name, legacy_group=legacy_group, new_group=new_group
            ),
            DeprecationWarning,
            stacklevel=2,
        )

    # --- introspection -----------------------------------------------------

    def list_plugins(self, group: str | None = None) -> list[PluginInfo]:
        """List diagnostic records for registered plugins.

        Passing ``group=None`` returns every record across every group.
        """
        if group is not None:
            self._require_known_group(group)
            return list(self._info[group].values())
        out: list[PluginInfo] = []
        for g in _GROUPS:
            out.extend(self._info[g].values())
        return out


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_lock = threading.Lock()
_default_instance: Registry | None = None


def default_registry() -> Registry:
    """Return the module-level singleton ``Registry``.

    First caller triggers lazy ``Registry.discover()`` so entry-point plugins
    come online on first use. ``discover()`` is itself idempotent; subsequent
    calls short-circuit on ``self._discovered``.
    """
    global _default_instance
    with _default_lock:
        if _default_instance is None:
            _default_instance = Registry()
            _default_instance.discover()
    return _default_instance


def register_plugin(group: str, name: str, impl: object) -> None:
    """Convenience helper: register ``impl`` on the module-level singleton.

    Equivalent to ``default_registry().register(group, name, impl)``.
    """
    default_registry().register(group, name, impl)


def _reset_default_registry_for_tests() -> None:
    """Test-only: drop the module-level singleton so the next call rebuilds it.

    Use via ``monkeypatch`` rather than importing directly in production code.
    """
    global _default_instance
    with _default_lock:
        _default_instance = None


__all__ = [
    "PluginInfo",
    "Registry",
    "default_registry",
    "register_plugin",
]
