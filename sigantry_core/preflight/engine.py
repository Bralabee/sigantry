"""Preflight execution engine for coordinating deployment safety probes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sigantry_core.preflight.models import PreflightReport, ProbeResult, ProbeStatus
from sigantry_core.preflight.probes import (
    BaseProbe,
    CapacityStateProbe,
    DependencyGraphProbe,
    EntraScopeProbe,
    SchemaSyntaxProbe,
)


class PreflightEngine:
    """Coordinates and executes preflight safety probes for deployments."""

    def __init__(self, probes: list[BaseProbe] | None = None) -> None:
        self.probes = probes or [
            SchemaSyntaxProbe(),
            DependencyGraphProbe(),
            EntraScopeProbe(),
            CapacityStateProbe(),
        ]

    def run(
        self,
        *,
        manifest_path: Path,
        environment: str = "dev",
        params_path: Path | None = None,
        client: Any = None,
        strict: bool = False,
    ) -> PreflightReport:
        """Execute all probes and compile a PreflightReport."""
        start_time = time.perf_counter()
        results: list[ProbeResult] = []
        has_warnings = False
        has_failures = False

        for probe in self.probes:
            res = probe.run(
                manifest_path=manifest_path,
                environment=environment,
                params_path=params_path,
                client=client,
            )
            results.append(res)
            if res.status == ProbeStatus.WARN:
                has_warnings = True
            elif res.status == ProbeStatus.FAIL:
                has_failures = True

        total_duration = (time.perf_counter() - start_time) * 1000
        passed = not has_failures and (not strict or not has_warnings)

        return PreflightReport(
            environment=environment,
            manifest_path=str(manifest_path),
            results=results,
            passed=passed,
            has_warnings=has_warnings,
            total_duration_ms=total_duration,
        )
