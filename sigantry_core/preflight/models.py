"""Data models for preflight pre-deployment safety probes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProbeStatus(StrEnum):
    """Execution status of an individual safety probe."""

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
    """Consolidated preflight validation report."""

    model_config = ConfigDict(frozen=True)

    environment: str
    manifest_path: str
    results: list[ProbeResult] = Field(default_factory=list)
    passed: bool = True
    has_warnings: bool = False
    total_duration_ms: float = 0.0
