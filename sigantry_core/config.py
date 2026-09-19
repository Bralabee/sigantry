"""Typed configuration loader (PROD-03).

Loads ``.fabric-dataops.toml`` via ``pydantic-settings`` v2 with fail-fast
validation. The root ``ToolkitSettings`` model composes one sub-model per
seam (``core``, ``auth``, ``telemetry``, ``deploy``, ``dq``, ``runbooks``,
``capacity``) and passes through per-plugin namespaced tables (e.g.
``[telemetry.log_analytics]``) untouched -- the plugin's own pydantic model
is responsible for validating those.

Env-var overrides use the conventional ``FDT_`` prefix with nested-delimiter
``__``:

    FDT_CORE__TENANT_ID=abc-123  -> settings.core.tenant_id == "abc-123"

``load_settings(path)`` is the canonical public entry point.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PREFIX = "FDT_"
_ENV_DELIM = "__"

# ---------------------------------------------------------------------------
# Sub-models (one per seam). Each allows extra fields so per-plugin namespaced
# tables (e.g. ``[telemetry.log_analytics]``) pass through to ``.extras``.
# ---------------------------------------------------------------------------


class _SeamSubSettings(BaseSettings):
    """Base for every seam sub-model -- allows unknown keys through."""

    # BaseSettings subclasses take SettingsConfigDict (a ConfigDict superset);
    # using plain ConfigDict here was a mypy/pydantic-settings type mismatch.
    model_config = SettingsConfigDict(extra="allow")


class CoreSettings(_SeamSubSettings):
    # Optional at the base level -- a greenfield consumer doing telemetry
    # only does not need a tenant_id. Plugins that require it (AuthProvider,
    # AimsDeployProfile, etc.) validate it themselves at resolve time.
    tenant_id: str | None = None


class AuthSettings(_SeamSubSettings):
    provider: str | None = None


class TelemetrySettings(_SeamSubSettings):
    sink: str | None = None


class DeploySettings(_SeamSubSettings):
    profile: str | None = None


class DqSettings(_SeamSubSettings):
    gate: str | None = None


class RunbookSettings(_SeamSubSettings):
    registry: str | None = None
    static_map: dict[str, str] = Field(default_factory=dict)


class CapacitySettings(_SeamSubSettings):
    policy: str | None = None


class ReleaseSettings(_SeamSubSettings):
    """Settings for the release-record + work-item-provider seam (Phase 11).

    TOML namespace: ``[release]`` for shared settings,
    ``[release.ado]`` and ``[release.github]`` for provider-specific
    construction kwargs (e.g. ``organization``, ``project``, ``owner``,
    ``repo``). Per-provider sub-namespaces are typed as plain ``dict`` so
    operators can populate any keyword the provider constructor accepts
    without forcing a schema migration on every new field.

    Fields
    ------
    provider : str | None
        Default work-item provider kind (``"ado"`` or ``"github"``). The
        ``sigantry release record --provider`` flag wins when supplied.
    audit_dir : str | None
        Override the default ``~/.sigantry/audit/`` directory used by
        :func:`sigantry_core.governance.audit.emit_deploy_record`. The
        ``--audit-dir`` CLI flag wins when supplied.
    ado : dict[str, Any]
        Pass-through kwargs for the ADO provider (e.g. ``organization``,
        ``project``, ``tenant_id``).
    github : dict[str, Any]
        Pass-through kwargs for the GitHub provider (e.g. ``owner``,
        ``repo``, ``pat``, ``app_id``, ``private_key_pem``,
        ``installation_id``).
    """

    provider: str | None = None
    audit_dir: str | None = None
    ado: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] = Field(default_factory=dict)


class NotificationSettings(_SeamSubSettings):
    """Settings for the ``NotificationSink`` seam (Phase 16 / W2.4).

    TOML namespace: ``[notifications]`` for the seam slot,
    ``[notifications.<name>]`` for per-plugin construction kwargs (e.g.
    ``[notifications.teams]\\nwebhook_url = "..."``). Any unknown keys
    are accepted via ``extra="allow"`` so plugins with rich config
    schemas (e.g. token rotation, retry policy) populate without
    forcing a base-package schema bump.
    """

    sink: str | None = None


class SecretSettings(_SeamSubSettings):
    """Settings for the ``SecretStore`` seam (Phase 16 / W2.4).

    TOML namespace: ``[secrets]`` for the seam slot,
    ``[secrets.<name>]`` for per-plugin construction kwargs (e.g.
    ``[secrets.key_vault]\\nvault_url = "https://..."``).
    """

    store: str | None = None


class ApprovalSettings(_SeamSubSettings):
    """Settings for the ``ApprovalGate`` seam (Phase 16 / W2.4).

    TOML namespace: ``[approvals]`` for the seam slot,
    ``[approvals.<name>]`` for per-plugin construction kwargs.
    """

    gate: str | None = None


class PrReviewBotSettings(_SeamSubSettings):
    """Settings for the ``PrReviewBot`` seam (Phase 14 / Audit-2026-05-07 W2.5).

    TOML namespace: ``[pr_review_bots]`` for the seam slot,
    ``[pr_review_bots.<name>]`` for per-plugin construction kwargs.
    """

    bot: str | None = None


class WorkflowSettings(_SeamSubSettings):
    """Workflow-level operator preferences (Phase 13 / W2 -- SPEC §Constraints #1).

    TOML namespace: ``[workflow]``.

    Fields
    ------
    preview_apis_acknowledged : bool
        Operator's explicit acknowledgement that Sigantry depends on
        Preview Microsoft Fabric REST endpoints -- primarily the Folders
        endpoint family (Council D constraint #1, Feb 2026 status). When
        False (default), ``sigantry sync apply`` and ``sigantry sync pull``
        emit a one-time WARNING on first invocation per process to
        encourage operators to acknowledge before relying on the Preview
        Folders REST surface. When True, the warning is suppressed.

        Set via ``[workflow]\\npreview_apis_acknowledged = true`` in
        ``.fabric-dataops.toml`` (or the
        ``FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED`` env var).

        See ``docs/reference/api-stability.md`` for the full risk
        acceptance discussion and the revisit triggers.
    """

    preview_apis_acknowledged: bool = False


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class ToolkitSettings(BaseSettings):
    """Root settings composed from the eight seam sub-models.

    Consumers construct via ``load_settings(path)`` rather than calling this
    constructor directly, so TOML loading stays in one place.
    """

    model_config = SettingsConfigDict(
        env_prefix="FDT_",
        env_nested_delimiter="__",
        extra="allow",
        case_sensitive=False,
    )

    core: CoreSettings = Field(default_factory=CoreSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    deploy: DeploySettings = Field(default_factory=DeploySettings)
    dq: DqSettings = Field(default_factory=DqSettings)
    runbooks: RunbookSettings = Field(default_factory=RunbookSettings)
    capacity: CapacitySettings = Field(default_factory=CapacitySettings)
    release: ReleaseSettings = Field(default_factory=ReleaseSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    secrets: SecretSettings = Field(default_factory=SecretSettings)
    approvals: ApprovalSettings = Field(default_factory=ApprovalSettings)
    pr_review_bots: PrReviewBotSettings = Field(default_factory=PrReviewBotSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_settings(
    path: str | Path = ".fabric-dataops.toml",
) -> ToolkitSettings:
    """Load ``ToolkitSettings`` from a TOML file, layered with env-var overrides.

    The TOML file is parsed with stdlib ``tomllib`` (Python 3.11+). The parsed
    mapping is passed as the initial field values to ``ToolkitSettings``;
    pydantic-settings layers ``FDT_`` env vars on top via its normal chain.

    Fail-fast behaviours:

    - Missing TOML file with no env-var override for ``core.tenant_id`` raises
      ``pydantic.ValidationError``.
    - Malformed TOML raises ``tomllib.TOMLDecodeError``.
    - Any field type violation raises ``pydantic.ValidationError``.
    """
    p = Path(path)
    data: dict[str, Any] = {}
    if p.is_file():
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    _apply_env_overrides(data)
    return ToolkitSettings(**data)


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Merge ``FDT_<section>__<key>=value`` env vars onto ``data`` (in-place).

    pydantic-settings v2.1 does not have ``TomlConfigSettingsSource`` (added in
    2.2). We emulate env-layer precedence by walking ``os.environ`` ourselves
    with the ``FDT_`` prefix + ``__`` nested delimiter. Env wins over TOML
    because we write into ``data`` AFTER the TOML parse.
    """
    for raw_key, raw_val in os.environ.items():
        if not raw_key.startswith(_ENV_PREFIX):
            continue
        path = raw_key[len(_ENV_PREFIX) :].lower().split(_ENV_DELIM)
        if not path or path == [""]:
            continue
        cursor: dict[str, Any] = data
        for segment in path[:-1]:
            nxt = cursor.get(segment)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[segment] = nxt
            cursor = nxt
        cursor[path[-1]] = raw_val


__all__ = [
    "ApprovalSettings",
    "AuthSettings",
    "CapacitySettings",
    "CoreSettings",
    "DeploySettings",
    "DqSettings",
    "NotificationSettings",
    "PrReviewBotSettings",
    "ReleaseSettings",
    "RunbookSettings",
    "SecretSettings",
    "TelemetrySettings",
    "ToolkitSettings",
    "WorkflowSettings",
    "load_settings",
]
