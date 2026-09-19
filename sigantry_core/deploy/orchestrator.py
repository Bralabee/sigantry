"""DeployProfile orchestrator (PROD-07).

Thin registry-driven dispatcher: resolve a registered
:class:`~sigantry_core.protocols.DeployProfile`, call
``profile.plan(ctx)`` then ``profile.apply(ctx, plan)``, and propagate the
:class:`DeployResult`.

Profiles ship in plugin packages and register under the
``sigantry.deploy_profiles`` entry-point group. The base package
deliberately ships no concrete profile.

Audit-2026-05-07 W2.2: registry probe goes through the canonical
:func:`sigantry_core._dispatch.resolve_seam` helper -- one resolution
algorithm for every protocol seam.
"""

from __future__ import annotations

from typing import cast

from sigantry_core._dispatch import resolve_seam
from sigantry_core.protocols import (
    DeployContext,
    DeployProfile,
    DeployResult,
)
from sigantry_core.registry import GROUP_DEPLOY_PROFILES, Registry


def deploy(
    ctx: DeployContext,
    *,
    profile: DeployProfile | None = None,
    profile_name: str | None = None,
    registry: Registry | None = None,
) -> DeployResult:
    """Resolve a :class:`DeployProfile` and execute plan + apply against ``ctx``.

    Parameters
    ----------
    ctx
        Runtime deployment context (workspace, environment, items).
    profile
        Explicit profile instance (direct DI). Wins over ``profile_name``.
    profile_name
        Plugin name registered under ``sigantry.deploy_profiles``.
    registry
        Optional :class:`Registry` to resolve from. Defaults to
        :func:`default_registry`.

    Returns
    -------
    DeployResult
        Whatever ``profile.apply`` returns, propagated verbatim.

    Raises
    ------
    ValueError
        If neither ``profile`` nor ``profile_name`` is provided.
    KeyError
        If ``profile_name`` is not registered under the
        ``deploy_profiles`` group.
    """
    if profile is None and profile_name is None:
        raise ValueError(
            "deploy requires either a pre-built profile or a "
            "profile_name to resolve from the registry; set "
            "settings.deploy.profile or pass profile= / profile_name=."
        )
    resolved = resolve_seam(
        GROUP_DEPLOY_PROFILES,
        profile_name,
        impl=profile,
        registry=registry,
    )
    if resolved is None:
        raise KeyError(f"Deploy profile {profile_name!r} could not be resolved from registry")
    plan = resolved.plan(ctx)
    return cast(DeployResult, resolved.apply(ctx, plan))


__all__ = ["deploy"]
