"""Canonical seam resolver — Audit-2026-05-07 W2.2.

Single source of truth for the "named-plugin -> instance" resolution
chain that every protocol seam in the toolkit goes through. Replaces
the four near-identical inline probes that previously sat in:

- ``sigantry_core.deploy.orchestrator.deploy``
- ``sigantry_core.dq.dispatcher.run_gate``
- ``sigantry_core.monitor.dispatcher.resolve_sink``
- ``sigantry_core.api._resolve_optional``

There is exactly one resolution algorithm:

1. ``impl`` kwarg pre-empts everything (direct DI -- the test path).
2. Empty ``name`` -> ``None``. Unconfigured seams are no-ops; callers
   that require a name should gate ahead of this call (the orchestrator
   and dq dispatcher do, since absence is a programming error there).
3. Otherwise, the registry (``registry`` kwarg, else
   :func:`sigantry_core.registry.default_registry`) is discovered
   (idempotent) and asked for ``(group, name)``:

   - Non-class result (instance / factory): returned verbatim.
   - Class result: instantiated via the first applicable rule:

     a) ``cls.from_settings(plugin_config_dict)`` if that classmethod
        exists AND ``settings`` was provided. Plugins that need rich
        wiring opt into this convention. Skipped when ``settings`` is
        ``None`` so direct callers (orchestrator, dq dispatcher) keep
        the zero-arg behaviour they had before.
     b) ``cls(**plugin_config_dict)`` if a ``[<seam>.<name>]`` TOML
        subsection is present. ``TypeError`` raised by the constructor
        is wrapped with an actionable message pointing at the two
        recovery paths.
     c) ``cls()`` -- zero-arg path. Used by ``NoopGate`` and friends.

The plugin config dict comes from the ``[<seam>.<name>]`` subsection of
the TOML settings via the ``extra="allow"`` pydantic v2 ``model_extra``
field. ``settings is None`` -> ``{}`` (so direct callers get the
zero-arg path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sigantry_core.registry import (
    GROUP_APPROVAL_GATES,
    GROUP_AUTH_PROVIDERS,
    GROUP_CAPACITY_POLICIES,
    GROUP_DEPLOY_PROFILES,
    GROUP_DQ_GATES,
    GROUP_NOTIFICATION_SINKS,
    GROUP_PR_REVIEW_BOTS,
    GROUP_RUNBOOK_REGISTRIES,
    GROUP_SECRET_STORES,
    GROUP_TELEMETRY_SINKS,
    GROUP_WORK_ITEM_PROVIDERS,
    Registry,
    default_registry,
)

if TYPE_CHECKING:
    from sigantry_core.config import ToolkitSettings


# Single-source map: registry group -> TOML section key. The keys here
# are the only place in the toolkit that connects a registry group to
# the short ``[<seam>]`` name that operators write in their TOML.
GROUP_TO_TOML_KEY: dict[str, str] = {
    GROUP_DEPLOY_PROFILES: "deploy",
    GROUP_DQ_GATES: "dq",
    GROUP_TELEMETRY_SINKS: "telemetry",
    GROUP_AUTH_PROVIDERS: "auth",
    GROUP_RUNBOOK_REGISTRIES: "runbooks",
    GROUP_CAPACITY_POLICIES: "capacity",
    # ``WorkItemProvider`` keeps the Phase-11 ``[release]`` namespace --
    # ``release.provider`` is the plugin name, ``release.ado`` and
    # ``release.github`` carry per-provider kwargs. W2.4 widening of
    # FabricDataOps reuses this slot rather than introducing a parallel
    # ``[work_items]`` namespace operators would have to populate twice.
    GROUP_WORK_ITEM_PROVIDERS: "release",
    GROUP_NOTIFICATION_SINKS: "notifications",
    GROUP_SECRET_STORES: "secrets",
    GROUP_APPROVAL_GATES: "approvals",
    GROUP_PR_REVIEW_BOTS: "pr_review_bots",
}


def _plugin_config(settings: ToolkitSettings | None, group: str, name: str) -> dict[str, Any]:
    """Return the ``[<seam>.<name>]`` TOML subsection as a dict of kwargs.

    Returns ``{}`` when no subsection is present (so zero-arg plugins
    keep working). Raises ``TypeError`` when the subsection exists but
    is not a TOML table -- an operator-side type-mismatch the toolkit
    should fail loudly on rather than silently dropping.
    """
    if settings is None:
        return {}
    section_key = GROUP_TO_TOML_KEY.get(group)
    if section_key is None:
        return {}
    section = getattr(settings, section_key, None)
    if section is None:
        return {}
    extras = getattr(section, "model_extra", None) or {}
    raw = extras.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(
            f"Plugin config [{section_key}.{name}] must be a TOML table; got {type(raw).__name__}."
        )
    return dict(raw)


def resolve_seam(
    group: str,
    name: str | None,
    *,
    impl: Any | None = None,
    registry: Registry | None = None,
    settings: ToolkitSettings | None = None,
) -> Any | None:
    """Resolve a named protocol-seam implementation via the registry.

    See module docstring for the full algorithm. This helper is the
    only path through which dispatchers should reach the registry --
    inlined ``reg.resolve(...)`` + ``isinstance(impl, type)`` dances
    are gated by ``tests/prereqs/test_no_inline_seam_resolution.py``.
    """
    if impl is not None:
        return impl

    if name is None or name == "":
        return None

    reg = registry if registry is not None else default_registry()
    reg.discover()
    resolved = reg.resolve(group, name)

    if not isinstance(resolved, type):
        return resolved

    cfg = _plugin_config(settings, group, name)

    if settings is not None:
        from_settings = getattr(resolved, "from_settings", None)
        if callable(from_settings):
            return from_settings(cfg)

    toml_section = GROUP_TO_TOML_KEY.get(group, "<?>")
    if cfg:
        try:
            return resolved(**cfg)
        except TypeError as exc:
            raise TypeError(
                f"Cannot instantiate plugin {name!r} in group {group!r} "
                f"with the config at [{toml_section}.{name}]: {exc}. "
                f"Either accept those kwargs in the plugin's __init__ "
                f"or define a `from_settings(cls, settings: dict)` "
                f"classmethod."
            ) from exc

    try:
        return resolved()
    except TypeError as exc:
        raise TypeError(
            f"Plugin {name!r} in group {group!r} requires configuration "
            f"but none was found. Add a [{toml_section}.{name}] section "
            f"to your TOML, or define `from_settings(cls, settings: "
            f"dict)` on the plugin class. Original error: {exc}"
        ) from exc


__all__ = ["GROUP_TO_TOML_KEY", "resolve_seam"]
