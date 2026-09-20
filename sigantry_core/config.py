"""Typed configuration loader (PROD-03).

Loads ``.sigantry.toml`` via ``pydantic-settings`` v2. The root
``ToolkitSettings`` model composes one sub-model per settings section
(``core``, ``auth``, ``telemetry``, ``deploy``, ``dq``, ``runbooks``,
``capacity``, ...) and passes through per-plugin namespaced tables (e.g.
``[telemetry.log_analytics]``) untouched -- the plugin's own pydantic model
is responsible for validating those.

Env-var overrides use the ``SIGANTRY_`` prefix with nested-delimiter ``__``:

    SIGANTRY_CORE__TENANT_ID=abc-123  -> settings.core.tenant_id == "abc-123"

Only ``<PREFIX><SECTION>__<KEY>`` forms are honoured, and ``<SECTION>`` must
name a real settings section -- see :func:`_apply_env_overrides` for why that
restriction is load-bearing rather than tidiness.

Legacy surface, honoured for one more minor release with a
``DeprecationWarning`` and then removed (ADR-0011; V3.X-ROADMAP
LEGACY-SURFACE-DROP item 2):

- the config filename ``.fabric-dataops.toml``, read only when no
  ``.sigantry.toml`` is present;
- the env prefix ``FDT_``, read but outranked by ``SIGANTRY_`` wherever a
  setting is supplied under both.

``load_settings(path)`` is the canonical public entry point.
"""

from __future__ import annotations

import os
import tomllib
import warnings
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_CONFIG_FILENAME = ".sigantry.toml"
_LEGACY_CONFIG_FILENAME = ".fabric-dataops.toml"

_ENV_PREFIX = "SIGANTRY_"
_LEGACY_ENV_PREFIX = "FDT_"
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
        ``.sigantry.toml`` (or the
        ``SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED`` env var).

        See ``docs/reference/api-stability.md`` for the full risk
        acceptance discussion and the revisit triggers.
    """

    preview_apis_acknowledged: bool = False


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class ToolkitSettings(BaseSettings):
    """Root settings composed of one sub-model per settings section.

    Consumers construct via ``load_settings(path)`` rather than calling this
    constructor directly, so TOML loading stays in one place.

    The field names on this model are also the set of env-var sections
    :func:`_apply_env_overrides` will merge -- adding a section here extends
    that automatically.
    """

    # No ``env_prefix``/``env_nested_delimiter`` here on purpose: the env
    # layer is dropped in ``settings_customise_sources`` below, so declaring
    # one would advertise a mechanism that does not run.
    model_config = SettingsConfigDict(
        extra="allow",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use only the init source; :func:`_apply_env_overrides` owns env.

        pydantic-settings' own ``EnvSettingsSource`` would be a *second*
        env-reading mechanism alongside ``_apply_env_overrides``, and under
        the ``SIGANTRY_`` prefix the two disagree in a way that breaks
        loading: the source maps a bare ``SIGANTRY_<SECTION>`` var onto the
        whole section field and raises ``SettingsError`` when the value is
        not a parseable table. That turns 13 ordinary-looking variable names
        (``SIGANTRY_RELEASE``, ``SIGANTRY_SECRETS``, ``SIGANTRY_DEPLOY``, ...)
        into hard failures of every command that loads settings.

        Keeping a single, filtered env path removes that surface and makes
        the precedence rule (env over TOML) readable in one function.
        """
        return (init_settings,)

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
    path: str | Path | None = None,
) -> ToolkitSettings:
    """Load ``ToolkitSettings`` from a TOML file, layered with env-var overrides.

    ``path`` defaults to ``None``, which resolves the config file from the
    working directory -- ``.sigantry.toml``, or the legacy
    ``.fabric-dataops.toml`` with a ``DeprecationWarning``. An explicit
    ``path`` is used verbatim, whatever it is named.

    The TOML file is parsed with stdlib ``tomllib`` (Python 3.11+). The parsed
    mapping is passed as the initial field values to ``ToolkitSettings``;
    :func:`_apply_env_overrides` layers env vars on top.

    Behaviours:

    - Malformed TOML raises ``tomllib.TOMLDecodeError``.
    - Any field type violation raises ``pydantic.ValidationError``.
    - **A missing config file is not an error.** Every settings field is
      optional, so the result is an all-defaults ``ToolkitSettings`` and no
      seam plugin is configured. A caller that requires a particular value
      (``core.tenant_id``, a named seam impl) has to check for it: the loader
      cannot know which of them a given consumer needs, and guessing would
      break the direct-DI wiring path that supplies them in code. This
      docstring previously claimed a missing file raised ``ValidationError``;
      it never did, because no field is required.
    """
    resolved = _resolve_config_path(path)
    data: dict[str, Any] = {}
    if resolved.is_file():
        with resolved.open("rb") as fh:
            data = tomllib.load(fh)
    _apply_env_overrides(data)
    return ToolkitSettings(**data)


def _resolve_config_path(path: str | Path | None) -> Path:
    """Return the config file :func:`load_settings` should read.

    An explicit ``path`` is returned verbatim -- any filename works, which is
    what the migration guide tells operators who rename early. With no path,
    ``.sigantry.toml`` wins, the legacy ``.fabric-dataops.toml`` is the
    one-minor fallback, and when neither exists the new-style name is returned
    so the (non-fatal) miss is reported against the name operators should
    create.
    """
    if path is not None:
        return Path(path)
    current = Path(_CONFIG_FILENAME)
    if current.is_file():
        return current
    legacy = Path(_LEGACY_CONFIG_FILENAME)
    if legacy.is_file():
        warnings.warn(
            f"{_LEGACY_CONFIG_FILENAME} is deprecated: rename it to "
            f"{_CONFIG_FILENAME}. The legacy filename is read for one more "
            "minor release and then removed (ADR-0011).",
            DeprecationWarning,
            stacklevel=3,
        )
        return legacy
    return current


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Merge ``SIGANTRY_<section>__<key>=value`` env vars onto ``data`` (in-place).

    pydantic-settings v2.1 does not have ``TomlConfigSettingsSource`` (added in
    2.2). We emulate env-layer precedence by walking ``os.environ`` ourselves
    with the ``SIGANTRY_`` prefix + ``__`` nested delimiter. Env wins over TOML
    because we write into ``data`` AFTER the TOML parse.

    The legacy ``FDT_`` prefix is still read for one more minor release with a
    ``DeprecationWarning``. The legacy pass runs first, so where the same
    setting is supplied under both prefixes the ``SIGANTRY_`` value wins.

    Only ``<PREFIX><SECTION>__<KEY>`` forms are merged, and ``<SECTION>`` must
    name a field of ``ToolkitSettings``. **That restriction is load-bearing.**
    ``SIGANTRY_`` is also the prefix of the product's operational env vars --
    ``SIGANTRY_SMTP_PASSWORD``, ``SIGANTRY_GITHUB_TEST_PAT``,
    ``SIGANTRY_FABRIC_TOKEN`` and ~68 others. ``ToolkitSettings`` sets
    ``extra="allow"``, so an unfiltered sweep would bind every one of those --
    credentials included -- onto the settings object as an extra field, where
    ``model_dump()`` renders them in clear. The old ``FDT_`` prefix was
    namespace-exclusive and so never exposed this; adopting the shared
    ``SIGANTRY_`` prefix is what makes the filter necessary.

    The section names are read from the model rather than listed here, so a
    newly added seam cannot fall out of sync with this function.
    """
    known_sections = frozenset(ToolkitSettings.model_fields)
    legacy_keys: list[str] = []
    # Legacy first: a SIGANTRY_ value for the same setting then overwrites it.
    for prefix in (_LEGACY_ENV_PREFIX, _ENV_PREFIX):
        for raw_key, raw_val in sorted(os.environ.items()):
            if not raw_key.startswith(prefix):
                continue
            path = raw_key[len(prefix) :].lower().split(_ENV_DELIM)
            # A bare ``<PREFIX><SECTION>`` would replace a whole section table
            # with a scalar; anything whose head is not a section is not ours.
            if len(path) < 2 or path[0] not in known_sections:
                continue
            if prefix == _LEGACY_ENV_PREFIX:
                legacy_keys.append(raw_key)
            cursor: dict[str, Any] = data
            for segment in path[:-1]:
                nxt = cursor.get(segment)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cursor[segment] = nxt
                cursor = nxt
            cursor[path[-1]] = raw_val
    if legacy_keys:
        warnings.warn(
            f"The {_LEGACY_ENV_PREFIX} settings env prefix is deprecated: use "
            f"{_ENV_PREFIX} instead (e.g. {_ENV_PREFIX}CORE__TENANT_ID). "
            f"Seen: {', '.join(legacy_keys)}. The legacy prefix is read for "
            "one more minor release and then removed (ADR-0011).",
            DeprecationWarning,
            stacklevel=3,
        )


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
