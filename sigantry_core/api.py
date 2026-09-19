"""Public front door (PROD-01..08 glue).

``FabricDataOps`` is the single supported public surface. Consumers pick ONE
of two wiring paths:

1. **Direct dependency injection** (recommended for tests, advanced embedders):

       fdo = FabricDataOps(
           auth=my_auth_provider,
           telemetry=InMemoryTelemetrySink(),
           ...
       )

2. **Config-driven discovery** (recommended for production):

       fdo = FabricDataOps.from_config(".fabric-dataops.toml")

   ``from_config`` composes ``load_settings`` (:mod:`sigantry_core.config`)
   with ``default_registry()`` (:mod:`sigantry_core.registry`) to
   resolve each named seam into its registered implementation.

Plan 08-02 wires the behaviour methods (``deploy``, ``run_dq_gate``,
``emit``) onto the three dispatchers. Each method prefers an injected seam
over a settings-driven registry lookup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sigantry_core import registry as _registry
from sigantry_core._dispatch import resolve_seam
from sigantry_core.config import ToolkitSettings, load_settings
from sigantry_core.deploy.orchestrator import deploy as deploy_orchestrator
from sigantry_core.dq.dispatcher import run_gate as run_gate_dispatcher
from sigantry_core.monitor.emit import emit_telemetry
from sigantry_core.protocols import (
    ApprovalGate,
    AuthProvider,
    CapacityPolicy,
    Closeable,
    DataQualityGate,
    DataRef,
    DeployContext,
    DeployProfile,
    DeployResult,
    GateResult,
    NotificationSink,
    PrReviewBot,
    RunbookRegistry,
    SecretStore,
    TelemetrySink,
    WorkItemProvider,
)
from sigantry_core.registry import Registry, default_registry


class FabricDataOps:
    """Composition root for the eleven protocol seams.

    Any of the seam kwargs may be ``None``; callers can construct a partially
    wired ``FabricDataOps`` (e.g. telemetry only) without crashing.

    Audit-2026-05-07 W2.4: widened from 6 seams to 11 to match the registry
    surface advertised by ``sigantry doctor``. The 5 added seams --
    ``notifications``, ``secrets``, ``approvals``, ``work_items``,
    ``pr_review_bot`` -- previously had to be wired manually around the
    front door even though their Protocols, registry groups, and reference
    impls had shipped in Phases 11/14/16.
    """

    def __init__(
        self,
        *,
        auth: AuthProvider | None = None,
        telemetry: TelemetrySink | None = None,
        dq_gate: DataQualityGate | None = None,
        deploy_profile: DeployProfile | None = None,
        runbooks: RunbookRegistry | None = None,
        capacity: CapacityPolicy | None = None,
        notifications: NotificationSink | None = None,
        secrets: SecretStore | None = None,
        approvals: ApprovalGate | None = None,
        work_items: WorkItemProvider | None = None,
        pr_review_bot: PrReviewBot | None = None,
        registry: Registry | None = None,
        settings: ToolkitSettings | None = None,
    ) -> None:
        self.auth = auth
        self.telemetry = telemetry
        self.dq_gate = dq_gate
        self.deploy_profile = deploy_profile
        self.runbooks = runbooks
        self.capacity = capacity
        self.notifications = notifications
        self.secrets = secrets
        self.approvals = approvals
        self.work_items = work_items
        self.pr_review_bot = pr_review_bot
        self.registry = registry
        self.settings = settings

    # -- config-driven constructor -----------------------------------------

    @classmethod
    def from_config(
        cls,
        path: str | Path = ".fabric-dataops.toml",
        *,
        registry: Registry | None = None,
    ) -> FabricDataOps:
        """Build a ``FabricDataOps`` from ``.fabric-dataops.toml`` + registry.

        Steps:

        1. ``load_settings(path)`` -- pydantic-settings parses TOML and env.
        2. ``registry`` (or ``default_registry()``) is discovered (idempotent).
        3. For each seam whose settings name a plugin, the registry is asked
           to resolve the named implementation. Classes are instantiated with
           no arguments; instances are used as-is.

        Raises ``KeyError`` if a named plugin is not registered.
        """
        settings = load_settings(path)
        reg = registry if registry is not None else default_registry()
        reg.discover()

        # Audit-2026-05-07 W2.1 + W2.2: every group reference goes through
        # the named registry constants, and the resolution algorithm
        # itself is centralised in ``sigantry_core._dispatch.resolve_seam``.
        auth = resolve_seam(
            _registry.GROUP_AUTH_PROVIDERS,
            settings.auth.provider,
            registry=reg,
            settings=settings,
        )
        telemetry = resolve_seam(
            _registry.GROUP_TELEMETRY_SINKS,
            settings.telemetry.sink,
            registry=reg,
            settings=settings,
        )
        dq_gate = resolve_seam(
            _registry.GROUP_DQ_GATES,
            settings.dq.gate,
            registry=reg,
            settings=settings,
        )
        deploy_profile = resolve_seam(
            _registry.GROUP_DEPLOY_PROFILES,
            settings.deploy.profile,
            registry=reg,
            settings=settings,
        )
        runbooks = resolve_seam(
            _registry.GROUP_RUNBOOK_REGISTRIES,
            settings.runbooks.registry,
            registry=reg,
            settings=settings,
        )
        capacity = resolve_seam(
            _registry.GROUP_CAPACITY_POLICIES,
            settings.capacity.policy,
            registry=reg,
            settings=settings,
        )
        # Audit-2026-05-07 W2.4: 5 new seams join the public front door.
        notifications = resolve_seam(
            _registry.GROUP_NOTIFICATION_SINKS,
            settings.notifications.sink,
            registry=reg,
            settings=settings,
        )
        secrets = resolve_seam(
            _registry.GROUP_SECRET_STORES,
            settings.secrets.store,
            registry=reg,
            settings=settings,
        )
        approvals = resolve_seam(
            _registry.GROUP_APPROVAL_GATES,
            settings.approvals.gate,
            registry=reg,
            settings=settings,
        )
        # work_items reuses the existing Phase-11 ``[release]`` namespace --
        # ``release.provider`` is the ``WorkItemProvider`` plugin name and
        # ``release.ado`` / ``release.github`` carry the per-provider
        # construction kwargs. Keeping that single-source slot avoids
        # operators having to populate the same provider name in two places.
        work_items = resolve_seam(
            _registry.GROUP_WORK_ITEM_PROVIDERS,
            settings.release.provider,
            registry=reg,
            settings=settings,
        )
        pr_review_bot = resolve_seam(
            _registry.GROUP_PR_REVIEW_BOTS,
            settings.pr_review_bots.bot,
            registry=reg,
            settings=settings,
        )

        return cls(
            auth=auth,
            telemetry=telemetry,
            dq_gate=dq_gate,
            deploy_profile=deploy_profile,
            runbooks=runbooks,
            capacity=capacity,
            notifications=notifications,
            secrets=secrets,
            approvals=approvals,
            work_items=work_items,
            pr_review_bot=pr_review_bot,
            registry=reg,
            settings=settings,
        )

    # -- behaviour methods (wired onto dispatchers in Plan 08-02) ----------

    def deploy(
        self,
        ctx: DeployContext,
        *,
        profile_name: str | None = None,
    ) -> DeployResult:
        """Resolve a :class:`DeployProfile` and run plan + apply.

        Prefers the instance's ``deploy_profile`` over a registry lookup. A
        fallback ``profile_name`` can be supplied, else the value is read
        from ``settings.deploy.profile``.
        """
        resolved_name = profile_name
        if resolved_name is None and self.settings is not None:
            resolved_name = self.settings.deploy.profile
        return deploy_orchestrator(
            ctx,
            profile=self.deploy_profile,
            profile_name=resolved_name,
            registry=self.registry,
        )

    def run_dq_gate(
        self,
        suite: str,
        data_ref: DataRef,
        *,
        gate_name: str | None = None,
    ) -> GateResult:
        """Resolve a :class:`DataQualityGate` and run ``suite`` against ``data_ref``.

        Prefers the instance's ``dq_gate`` seam over a registry lookup. A
        fallback ``gate_name`` can be supplied, else read from
        ``settings.dq.gate``.
        """
        resolved_name = gate_name
        if resolved_name is None and self.settings is not None:
            resolved_name = self.settings.dq.gate
        return run_gate_dispatcher(
            suite,
            data_ref,
            gate=self.dq_gate,
            gate_name=resolved_name,
            registry=self.registry,
        )

    def emit(
        self,
        event_name: str,
        properties: dict[str, Any] | None = None,
        *,
        strict: bool = False,
    ) -> None:
        """Emit a telemetry event through the injected sink.

        Routes via :func:`sigantry_core.monitor.emit_telemetry` using
        the instance's ``telemetry`` seam. When no sink is wired the call is a
        silent no-op; ``strict=True`` surfaces sink exceptions.
        """
        emit_telemetry(
            event_name,
            properties,
            sink=self.telemetry,
            strict=strict,
        )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release plugin resources (HTTP pools, background threads, etc.).

        Iterates every seam instance and calls ``.close()`` on any that
        satisfy the :class:`~sigantry_core.protocols.Closeable`
        protocol. Plugins without resources (e.g. ``NoopGate``) are skipped.
        ``close()`` is idempotent: plugins should cope with being called
        repeatedly.

        Prefer the context-manager form (``with FabricDataOps(...) as fdo:``)
        when the lifecycle is scoped to a block; this method exists for
        long-lived objects that cannot use ``with``.
        """
        seams = (
            self.auth,
            self.telemetry,
            self.dq_gate,
            self.deploy_profile,
            self.runbooks,
            self.capacity,
            self.notifications,
            self.secrets,
            self.approvals,
            self.work_items,
            self.pr_review_bot,
        )
        for seam in seams:
            if seam is None:
                continue
            # Duck-type against the Closeable protocol so plugins that
            # do not need lifecycle management are unaffected.
            if isinstance(seam, Closeable):
                try:
                    seam.close()
                except Exception:  # best-effort: one bad plugin must not
                    # prevent the others from closing cleanly.
                    import logging

                    logging.getLogger(__name__).warning(
                        "FabricDataOps.close: seam %r raised during close()",
                        type(seam).__name__,
                        exc_info=True,
                    )

    def __enter__(self) -> FabricDataOps:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


__all__ = ["FabricDataOps"]
