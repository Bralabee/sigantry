"""Data models for preflight pre-deployment safety probes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProbeStatus(StrEnum):
    """Execution status of an individual safety probe.

    ``PASS``, ``WARN`` and ``FAIL`` are verdicts: the probe checked what it
    is for. ``SKIP`` is not a verdict: the probe lacked what it needed (a
    credential, a client, a workspace id) and checked nothing. The engine
    fails a ``--strict`` run on a ``SKIP`` and the CLI names every skipped
    probe, so a run can never read as clean because a probe did not look.
    """

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class ProbeResult(BaseModel):
    """Result of an individual preflight probe."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: ProbeStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class PreflightReport(BaseModel):
    """Consolidated preflight validation report.

    ``passed`` is false on any ``FAIL``; under ``strict`` it is also false
    on any ``WARN`` or ``SKIP``. ``has_skips`` is set whenever a probe did
    not check anything, whether or not that failed the run.
    """

    model_config = ConfigDict(frozen=True)

    environment: str
    manifest_path: str
    results: list[ProbeResult] = Field(default_factory=list)
    passed: bool = True
    has_warnings: bool = False
    has_skips: bool = False
    total_duration_ms: float = 0.0

    @property
    def skipped(self) -> list[str]:
        """Names of the probes that checked nothing."""
        return [r.name for r in self.results if r.status == ProbeStatus.SKIP]

    @property
    def failed(self) -> list[str]:
        """Names of the probes that found a blocking problem."""
        return [r.name for r in self.results if r.status == ProbeStatus.FAIL]
