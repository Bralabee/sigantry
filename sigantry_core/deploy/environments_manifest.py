"""``environments.yml`` model — the config-driven target set for ``env sync-all``.

A single declarative manifest describes *which* Fabric Environments should be
kept in sync with *which* wheels, and *how* (float vs pin, gated vs open). It is
the "config-driven" half of fleet wheel deployment: the publish pipeline emits a
new wheel, and ``sigantry env sync-all --manifest environments.yml`` fans that
out across every listed Environment — with the risk controls (gating, pinning)
expressed as data, not as copy-pasted pipeline YAML.

Mirrors the conventions of :mod:`sigantry_core.sync.manifest`: pydantic v2,
``extra="forbid"``, ``frozen=True``, SemVer-pinned ``schema_version`` validated
to major 1, and ``yaml.safe_load`` exclusively (no arbitrary object construction).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

_ALLOWED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"1.0", "1.0.0"})
_ALLOWED_POLICIES: Final[frozenset[str]] = frozenset({"float", "pin"})
_GLOB_CHARS: Final[frozenset[str]] = frozenset("*?[")


class EnvironmentsManifestError(ValueError):
    """Raised on YAML-parse or schema-validation failure of an environments manifest.

    Carries structured ``violations`` so callers render errors without importing
    pydantic (matches :class:`sigantry_core.sync.manifest.ManifestValidationError`).
    """

    def __init__(self, message: str, *, violations: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.violations: list[dict[str, Any]] = violations or []


class EnvTarget(BaseModel):
    """One Fabric Environment to keep in sync, plus its risk policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    workspace_id: str
    environment_id: str
    # Wheel sources: literal paths and/or globs (resolved relative to the run cwd).
    wheels: list[str] = Field(min_length=1)
    # float: globs allowed, deploy whatever is present (tracks latest build).
    # pin:   every entry must be a literal path (no glob) — refuses ambiguous/"latest"
    #        deploys, the safe choice for production environments.
    policy: str = "float"
    # gated targets (e.g. PROD) are SKIPPED unless the operator passes
    # --include-gated. This is the primary guard that lets an automatic, on-release
    # trigger keep DEV/SANDBOX current without ever touching production unattended.
    gated: bool = False

    @field_validator("name", "workspace_id", "environment_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("policy")
    @classmethod
    def _validate_policy(cls, value: str) -> str:
        if value not in _ALLOWED_POLICIES:
            raise ValueError(f"policy {value!r} not in {sorted(_ALLOWED_POLICIES)}")
        return value

    @field_validator("wheels")
    @classmethod
    def _wheels_non_empty_strings(cls, value: list[str]) -> list[str]:
        for entry in value:
            if not entry or not entry.strip():
                raise ValueError("wheel entries must be non-empty strings")
        return value

    @model_validator(mode="after")
    def _pin_targets_forbid_globs(self) -> EnvTarget:
        if self.policy == "pin":
            for entry in self.wheels:
                if any(ch in entry for ch in _GLOB_CHARS):
                    raise ValueError(
                        f"pin target {self.name!r}: wheel entry {entry!r} must be a literal "
                        "path, not a glob (pin refuses ambiguous/latest deploys)"
                    )
        return self


class EnvironmentsManifest(BaseModel):
    """Top-level ``environments.yml`` structure (SemVer-pinned via ``schema_version``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    targets: list[EnvTarget] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        if value not in _ALLOWED_SCHEMA_VERSIONS:
            raise ValueError(f"schema_version {value!r} not in {sorted(_ALLOWED_SCHEMA_VERSIONS)}")
        return value

    @field_validator("targets")
    @classmethod
    def _unique_target_names(cls, value: list[EnvTarget]) -> list[EnvTarget]:
        seen: set[str] = set()
        for target in value:
            if target.name in seen:
                raise ValueError(f"duplicate target name {target.name!r}")
            seen.add(target.name)
        return value


def load_environments_manifest(path: Path | str) -> EnvironmentsManifest:
    """Read ``environments.yml`` from disk, parse via PyYAML, validate via pydantic.

    Raises :class:`EnvironmentsManifestError` with structured ``violations`` on any
    YAML-parse or validation failure.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise EnvironmentsManifestError(
            f"environments manifest at {p} failed YAML parse: {exc}",
            violations=[{"field": "<root>", "reason": str(exc), "severity": "error"}],
        ) from exc

    try:
        return EnvironmentsManifest.model_validate(payload)
    except ValidationError as exc:
        violations: list[dict[str, Any]] = [
            {
                "field": ".".join(str(part) for part in err["loc"]),
                "reason": err.get("msg", ""),
                "severity": "error",
            }
            for err in exc.errors()
        ]
        raise EnvironmentsManifestError(
            f"environments manifest at {p} failed validation: {exc.error_count()} error(s)",
            violations=violations,
        ) from exc
