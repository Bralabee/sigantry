"""Preflight execution engine for coordinating deployment safety probes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sigantry_core.preflight.models import PreflightReport, ProbeResult, ProbeStatus
from sigantry_core.preflight.probes import (
    BaseProbe,
    CapacityStateProbe,
    EntraScopeProbe,
    SchemaSyntaxProbe,
)


class PreflightEngine:
    """Coordinates and executes preflight safety probes for deployments.

    The default set is the three probes that can check something against a
    shipped manifest: schema syntax, Entra scope and capacity state.
    ``DependencyGraphProbe`` is importable but not a default: the ``sync.yml``
    schema declares no item dependencies (``extra="forbid"``), so against a
    real manifest it ordered nothing and reported that as a pass.
    """

    def __init__(self, probes: list[BaseProbe] | None = None) -> None:
        self.probes = (
            probes
            if probes is not None
            else [
                SchemaSyntaxProbe(),
                EntraScopeProbe(),
                CapacityStateProbe(),
            ]
        )

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str = "dev",
        params_path: Path | None = None,
        client: Any = None,
        token_provider: Any = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        strict: bool = False,
    ) -> PreflightReport:
        """Execute all probes and compile a PreflightReport.

        ``passed`` is false on any ``FAIL``. Under ``strict`` it is also
        false on any ``WARN`` and on any ``SKIP``: a probe that could not
        check is not a probe that passed.
        """
        start_time = time.perf_counter()
        results: list[ProbeResult] = []
        has_warnings = False
        has_failures = False
        has_skips = False

        for probe in self.probes:
            res = probe.run(
                manifest_path=manifest_path,
                environment=environment,
                params_path=params_path,
                client=client,
                token_provider=token_provider,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            results.append(res)
            if res.status == ProbeStatus.WARN:
                has_warnings = True
            elif res.status == ProbeStatus.FAIL:
                has_failures = True
            elif res.status == ProbeStatus.SKIP:
                has_skips = True

        total_duration = (time.perf_counter() - start_time) * 1000
        passed = not has_failures and (not strict or not (has_warnings or has_skips))

        return PreflightReport(
            environment=environment,
            manifest_path=str(manifest_path),
            results=results,
            passed=passed,
            has_warnings=has_warnings,
            has_skips=has_skips,
            total_duration_ms=total_duration,
        )
