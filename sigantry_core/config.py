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
``DeprecationWarning`` and then removed (ADR-0011):

- the config filename ``.fabric-dataops.toml``;
- the env prefix ``FDT_``.

Through 1.0.x, every input sigantry 1.0.0 read keeps the result it had there:

- when ``.sigantry.toml`` and the legacy file both exist and differ, the
  legacy file is read, with a ``UserWarning``;
- an ``FDT_`` value outranks a ``SIGANTRY_`` value for the same setting;
- a ``SIGANTRY_`` value only fills a setting the file leaves unset when the
  file is one 1.0.0 read as well (an explicit ``path``, or the legacy file);
  over a ``.sigantry.toml`` found by default resolution it wins;
- ``FDT_`` names in another letter case, and ``FDT_<SECTION>`` holding a JSON
  object, are read again, below the file as in 1.0.0;
- a key in a settings section matches its field whatever its letter case;
- ``ToolkitSettings()`` built directly reads the environment again, through
  the same filter as :func:`load_settings`.

``load_settings(path)`` is the canonical public entry point.
"""

from __future__ import annotations

import contextvars
import copy
import json
import os
import sys
import tomllib
import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import FrameType
from typing import Any, Literal, NamedTuple, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo
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

#: Modules whose frames a settings warning skips when it chooses the line to
#: blame: this package, and pydantic, which calls back into it while
#: validating.
_INTERNAL_MODULE_ROOTS = ("sigantry_core", "pydantic", "pydantic_core", "pydantic_settings")

#: True while :func:`load_settings` builds ``ToolkitSettings``. It has merged
#: the environment into the values it passes already, so the model's own env
#: source must not read it a second time (that would repeat every warning).
_ENV_ALREADY_MERGED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sigantry_env_already_merged", default=False
)


def _warn(message: str, category: type[Warning]) -> None:
    """Warn, attributed to the first caller outside sigantry and pydantic.

    Python shows a ``DeprecationWarning`` by default only when the line it is
    attributed to is in ``__main__``. A fixed ``stacklevel`` lands inside this
    package whenever the loader is reached through ``from_config()`` or a
    validator, which hid the warning from the script that caused it. Python
    3.11 has no ``skip_file_prefixes``, so the frames are walked here.
    """
    level = 1
    frame: FrameType | None = sys._getframe(0)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if not any(
            module == root or module.startswith(root + ".") for root in _INTERNAL_MODULE_ROOTS
        ):
            break
        frame = frame.f_back
        level += 1
    warnings.warn(message, category, stacklevel=level)


# ---------------------------------------------------------------------------
# Sub-models (one per seam). Each allows extra fields so per-plugin namespaced
# tables (e.g. ``[telemetry.log_analytics]``) pass through to ``.extras``.
# ---------------------------------------------------------------------------


class _SeamSubSettings(BaseModel):
    """Base for every seam sub-model -- allows unknown keys through.

    Plain ``BaseModel``, NOT ``BaseSettings``, and that distinction is
    load-bearing. Every section is declared as ``Field(default_factory=...)``,
    so each factory call constructs the sub-model -- and a ``BaseSettings``
    sub-model runs its own ``EnvSettingsSource`` on construction. These have no
    ``env_prefix``, so that source matched BARE environment variables:
    ``TENANT_ID`` bound to ``core.tenant_id``, ``PROVIDER`` to
    ``auth.provider``, ``REGISTRY`` to ``runbooks.registry``. A CI runner
    exporting ``REGISTRY`` for a container registry silently populated settings
    the operator never wrote.

    Dropping the env source on ``ToolkitSettings`` alone did not close that:
    the root model is only one of fourteen. As plain models these carry no env
    source at all, which is what makes ``_apply_env_overrides`` the single env
    path the security note claims it is.
    """

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _match_declared_fields_whatever_their_case(cls, data: Any) -> Any:
        """Map a key onto the declared field it spells in another case.

        Up to 1.0.0 these models were ``BaseSettings`` with
        ``case_sensitive=False``, so ``TENANT_ID`` under ``[core]`` set
        ``tenant_id``. A plain model matches names exactly and would keep that
        key as an extra, leaving the field at its default with no error. Only
        declared fields are matched; any other key keeps its spelling. The
        exact spelling wins over a variant, and among variants the first one
        does. Each variant is reported with a ``DeprecationWarning``.
        """
        if not isinstance(data, dict):
            return data
        declared = {name.lower(): name for name in cls.model_fields}
        out: dict[Any, Any] = {}
        folded: list[str] = []
        for key, value in data.items():
            target = None
            if isinstance(key, str) and key not in cls.model_fields:
                target = declared.get(key.lower())
            if target is None:
                out[key] = value
                continue
            folded.append(key)
            if target not in data and target not in out:
                out[target] = value
        if folded:
            section = _section_name_of(cls)
            names = ", ".join(f"[{section}] {key}" if section else key for key in folded)
            _warn(
                f"Settings key(s) {names} match a settings field only when case is "
                "ignored, which sigantry 1.0.0 allowed. Write them in lower case "
                "(for example tenant_id); case-insensitive matching will be removed "
                "in a future minor release.",
                DeprecationWarning,
            )
        return out


class CoreSettings(_SeamSubSettings):
    # Optional at the base level -- a greenfield consumer doing telemetry
    # only does not need a tenant_id. Plugins that require it (an AuthProvider,
    # a DeployProfile, etc.) validate it themselves at resolve time.
    tenant_id: str | None = None


class AuthSettings(_SeamSubSettings):
    """Settings for the ``AuthProvider`` seam and the ``diagnose-auth`` doctor.

    TOML namespace: ``[auth]``.

    Fields
    ------
    provider : str | None
        Registered ``AuthProvider`` plugin name (resolved by
        ``FabricDataOps.from_config``).
    expected_group : str | None
        Expected Entra group display name for the ``diagnose-auth`` group
        check (e.g. ``"fabric-deployers"``). ``None`` (the default) means the
        check is reported as ``skipped``. Env:
        ``SIGANTRY_AUTH__EXPECTED_GROUP``; the ``--expected-group`` flag wins.
    """

    provider: str | None = None
    expected_group: str | None = None


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


class _FilteredEnvSource(PydanticBaseSettingsSource):
    """The env layer of a ``ToolkitSettings()`` built directly.

    In 1.0.0 the constructor read ``FDT_`` variables itself, and honoured
    ``_env_file``, ``_secrets_dir`` and ``_env_prefix``. This keeps all of
    that, but every input goes through the filter :func:`load_settings` uses
    (:func:`_collect_overrides`), so no unprefixed name and no section this
    model does not declare is read. Values passed to the constructor outrank
    it; within it the process environment outranks the env file, which
    outranks the secrets directory, as in pydantic-settings.

    The raw inputs are taken from the default sources pydantic-settings has
    already built, which have resolved the constructor arguments against
    ``model_config``. Each is read with ``getattr`` and a default, so a
    pydantic-settings release that renames one drops that input rather than
    failing every construction.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._init_kwargs: Mapping[str, Any] = getattr(init_settings, "init_kwargs", None) or {}
        self._env_prefix: str | None = getattr(env_settings, "env_prefix", None)
        self._dotenv_vars: Mapping[str, str | None] = (
            getattr(dotenv_settings, "env_vars", None) or {}
        )
        self._secrets_dir: Any = getattr(file_secret_settings, "secrets_dir", None)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Unused: ``__call__`` builds the whole mapping at once.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if _ENV_ALREADY_MERGED.get():
            return {}
        report = _EnvReport()
        prefix = self._env_prefix
        if prefix == "":
            report.empty_prefix = True
            prefix = _ENV_PREFIX
        dotenv = {k: v for k, v in self._dotenv_vars.items() if v is not None}
        secrets = _read_secrets_dir(self._secrets_dir)
        if prefix is None or prefix.upper() == _ENV_PREFIX:
            layers = [
                _file_style_layer(secrets, report),
                _file_style_layer(dotenv, report),
                # Unknown FDT_ heads: 1.0.0's constructor never bound them,
                # only load_settings did.
                _layer_env(
                    os.environ,
                    report,
                    file_data={},
                    file_read_by_1_0_0=False,
                    unknown_legacy_heads=False,
                ),
            ]
        else:
            layers = [
                _custom_prefix_layer(source, prefix, report)
                for source in (secrets, dotenv, os.environ)
            ]
        data: dict[str, Any] = {}
        for layer in layers:
            _merge_layer(data, layer)
        given = {
            key: value.model_dump(exclude_unset=True) if isinstance(value, BaseModel) else value
            for key, value in self._init_kwargs.items()
        }
        report.bare = _bare_names_1_0_0_would_read(os.environ, data, given)
        report.emit()
        return data


class ToolkitSettings(BaseSettings):
    """Root settings composed of one sub-model per settings section.

    Consumers construct via ``load_settings(path)`` rather than calling this
    constructor directly, so TOML loading stays in one place. Built directly,
    it reads the same filtered environment :func:`load_settings` does (see
    :class:`_FilteredEnvSource`), with constructor values outranking it.

    The field names on this model are also the set of env-var sections
    :func:`_apply_env_overrides` will merge -- adding a section here extends
    that automatically.
    """

    # ``env_prefix`` is declared only so that an explicit
    # ``ToolkitSettings(_env_prefix="")`` can be told apart from no argument
    # (pydantic-settings resolves both to this value). Neither it nor any
    # other env option configures pydantic-settings' own env source, which is
    # never enabled: see ``settings_customise_sources``.
    model_config = SettingsConfigDict(
        extra="allow",
        case_sensitive=False,
        env_prefix=_ENV_PREFIX,
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
        """Init values first, then the filtered env layer; never pydantic's.

        pydantic-settings' own ``EnvSettingsSource`` would be a *second*
        env-reading mechanism alongside ``_apply_env_overrides``, and under
        the ``SIGANTRY_`` prefix the two disagree in a way that breaks
        loading: the source maps a bare ``SIGANTRY_<SECTION>`` var onto the
        whole section field and raises ``SettingsError`` when the value is
        not a parseable table. That turns 13 ordinary-looking variable names
        (``SIGANTRY_RELEASE``, ``SIGANTRY_SECRETS``, ``SIGANTRY_DEPLOY``, ...)
        into hard failures of every command that loads settings.

        :class:`_FilteredEnvSource` reads the environment through the same
        filter as :func:`load_settings`, so a direct construction sees that
        env layer and nothing more.
        """
        return (
            init_settings,
            _FilteredEnvSource(
                settings_cls,
                init_settings=init_settings,
                env_settings=env_settings,
                dotenv_settings=dotenv_settings,
                file_secret_settings=file_secret_settings,
            ),
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
    path: str | Path | None = None,
) -> ToolkitSettings:
    """Load ``ToolkitSettings`` from a TOML file, layered with env-var overrides.

    ``path`` defaults to ``None``, which resolves the config file from the
    working directory -- ``.sigantry.toml``, or the legacy
    ``.fabric-dataops.toml`` with a ``DeprecationWarning``. When both exist
    and differ, the legacy file is read, as sigantry 1.0.0 did, with a
    ``UserWarning``. An explicit ``path`` is used verbatim, whatever it is
    named.

    The TOML file is parsed with stdlib ``tomllib`` (Python 3.11+).
    :func:`_apply_env_overrides` layers the env vars onto the parsed mapping,
    which is then passed as the initial field values to ``ToolkitSettings``.

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
    resolved = _resolve_config(path)
    data: dict[str, Any] = {}
    if resolved.path.is_file():
        with resolved.path.open("rb") as fh:
            data = tomllib.load(fh)
    _apply_env_overrides(data, file_read_by_1_0_0=resolved.read_by_1_0_0)
    token = _ENV_ALREADY_MERGED.set(True)
    try:
        return ToolkitSettings(**data)
    finally:
        _ENV_ALREADY_MERGED.reset(token)


class _ResolvedConfig(NamedTuple):
    path: Path
    #: True when sigantry 1.0.0 would have read this file as well. A
    #: ``SIGANTRY_`` variable, which 1.0.0 ignored, then only fills what the
    #: file leaves unset.
    read_by_1_0_0: bool


def _resolve_config(path: str | Path | None) -> _ResolvedConfig:
    """Return the config file :func:`load_settings` should read.

    An explicit ``path`` is returned verbatim -- any filename works, which is
    what the migration guide tells operators who rename early -- and 1.0.0
    read an explicit path too. With no path:

    - ``.sigantry.toml`` alone is read. 1.0.0 did not read it.
    - The legacy file alone is the one-minor fallback, read with a
      ``DeprecationWarning``.
    - With both, 1.0.0 read the legacy file, so that is still what is read,
      with a ``UserWarning``, which Python shows by default. Two files with
      the same bytes cannot give a different result, so ``.sigantry.toml`` is
      then read without a warning.
    - With neither, the new-style name is returned so the (non-fatal) miss is
      reported against the name operators should create.
    """
    if path is not None:
        return _ResolvedConfig(Path(path), True)
    current = Path(_CONFIG_FILENAME)
    legacy = Path(_LEGACY_CONFIG_FILENAME)
    if not legacy.is_file():
        return _ResolvedConfig(current, False)
    if current.is_file():
        if _same_bytes(current, legacy):
            return _ResolvedConfig(current, True)
        _warn(
            f"Both {_CONFIG_FILENAME} and {_LEGACY_CONFIG_FILENAME} exist in the "
            f"working directory. Reading {_LEGACY_CONFIG_FILENAME}, as sigantry 1.0.0 "
            f"did; {_CONFIG_FILENAME} is ignored. Move your settings into "
            f"{_CONFIG_FILENAME} and delete {_LEGACY_CONFIG_FILENAME} to switch. "
            "Two identical files are read without this warning.",
            UserWarning,
        )
        return _ResolvedConfig(legacy, True)
    _warn(
        f"{_LEGACY_CONFIG_FILENAME} is deprecated: rename it to "
        f"{_CONFIG_FILENAME}. The legacy filename is read for one more "
        "minor release and then removed (ADR-0011).",
        DeprecationWarning,
    )
    return _ResolvedConfig(legacy, True)


def _same_bytes(first: Path, second: Path) -> bool:
    try:
        return first.read_bytes() == second.read_bytes()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Env layer
# ---------------------------------------------------------------------------

#: Settings fields sigantry 1.0.0 bound from an UNPREFIXED environment
#: variable of the same name, in any letter case: its section models were
#: ``BaseSettings`` with no prefix. Used only to name such a variable in a
#: warning; none of them binds any more. Fields added after 1.0.0
#: (``auth.expected_group``) are not listed, because 1.0.0 never read them.
_BARE_NAMES_1_0_0: dict[str, tuple[tuple[str, str], ...]] = {
    "tenant_id": (("core", "tenant_id"),),
    "provider": (("auth", "provider"), ("release", "provider")),
    "sink": (("telemetry", "sink"), ("notifications", "sink")),
    "profile": (("deploy", "profile"),),
    "gate": (("dq", "gate"), ("approvals", "gate")),
    "registry": (("runbooks", "registry"),),
    "static_map": (("runbooks", "static_map"),),
    "policy": (("capacity", "policy"),),
    "audit_dir": (("release", "audit_dir"),),
    "ado": (("release", "ado"),),
    "github": (("release", "github"),),
    "store": (("secrets", "store"),),
    "bot": (("pr_review_bots", "bot"),),
    "preview_apis_acknowledged": (("workflow", "preview_apis_acknowledged"),),
}

_TRUE_STRINGS = frozenset({"1", "on", "t", "true", "y", "yes"})
_FALSE_STRINGS = frozenset({"0", "off", "f", "false", "n", "no"})
_MISSING = object()


class _Override(NamedTuple):
    name: str
    path: tuple[str, ...]
    value: Any


class _EnvReport:
    """What one env merge has to warn about. Names only, never values."""

    def __init__(self) -> None:
        self.legacy: list[str] = []
        self.clobbered: list[str] = []
        self.shadowed: list[str] = []
        self.bare: list[tuple[str, list[str]]] = []
        self.empty_prefix = False

    def emit(self) -> None:
        if self.empty_prefix:
            _warn(
                'ToolkitSettings(_env_prefix="") is not honoured: an empty prefix '
                "would read unprefixed environment variables, so the "
                f"{_ENV_PREFIX} and {_LEGACY_ENV_PREFIX} variables are read instead. "
                f"Use load_settings() with {_ENV_PREFIX} variables.",
                UserWarning,
            )
        if self.clobbered:
            _warn(
                "Ignoring settings env override(s) that would replace a table with "
                f"a scalar: {_names(self.clobbered)}. These name dict-valued "
                "settings fields, which cannot be set from a single env var; use "
                "the config file for them.",
                UserWarning,
            )
        if self.legacy:
            _warn(
                f"The {_LEGACY_ENV_PREFIX} settings env prefix is deprecated: use "
                f"{_ENV_PREFIX} instead (e.g. {_ENV_PREFIX}CORE__TENANT_ID). "
                f"Seen: {_names(self.legacy)}. The legacy prefix is read for "
                "one more minor release and then removed (ADR-0011).",
                DeprecationWarning,
            )
        if self.shadowed:
            _warn(
                f"Ignoring {_ENV_PREFIX} settings variable(s) {_names(self.shadowed)}: "
                f"the same setting is supplied by an {_LEGACY_ENV_PREFIX} variable, or "
                f"by a config file sigantry 1.0.0 also read (the legacy "
                f"{_LEGACY_CONFIG_FILENAME}, or a file passed by path), and through "
                f"1.0.x those still take precedence. Remove the legacy setting to let "
                f"the {_ENV_PREFIX} value apply.",
                UserWarning,
            )
        if self.bare:
            pairs = "; ".join(f"{name} -> {' or '.join(new)}" for name, new in self.bare)
            _warn(
                f"Unprefixed environment variable(s) {_names(n for n, _ in self.bare)} "
                "are no longer read as settings; sigantry 1.0.0 read them. Use the "
                f"prefixed form instead: {pairs}. Unprefixed names stopped binding "
                "because any variable that happened to share a generic name, such as "
                "PROVIDER or GATE, could select which plugin runs.",
                FutureWarning,
            )


def _names(names: Iterable[str]) -> str:
    return ", ".join(dict.fromkeys(names))


def _section_model(section: str) -> type[BaseModel] | None:
    field = ToolkitSettings.model_fields.get(section)
    if field is None:
        return None
    model = field.annotation
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model
    return None


def _section_name_of(model: type[BaseModel]) -> str | None:
    for name, field in ToolkitSettings.model_fields.items():
        if field.annotation is model:
            return name
    return None


def _declared_field(section: str, key: str) -> bool:
    model = _section_model(section)
    return model is not None and key in model.model_fields


def _is_mapping_field(section: str, key: str) -> bool:
    """True if ``<section>.<key>`` is a declared dict-typed settings field.

    Used to refuse a scalar env override that would replace a whole table --
    ``SIGANTRY_RELEASE__GITHUB=x`` against ``ReleaseSettings.github``, say.
    Unknown keys return False: they land in ``extras``, where a scalar is fine.
    """
    model = _section_model(section)
    if model is None:
        return False
    field = model.model_fields.get(key)
    if field is None:
        return False
    annotation = field.annotation
    return annotation is dict or get_origin(annotation) is dict


def _json_object(raw: object) -> dict[str, Any] | None:
    """Return ``raw`` parsed as a JSON object, or ``None`` if it is not one."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _collect_overrides(
    environ: Mapping[str, str | None],
    prefix: str,
    *,
    match: Literal["exact", "variant", "any"],
    report: _EnvReport,
    unknown_heads: bool = False,
    json_forms: bool = False,
) -> list[_Override]:
    """Read ``<PREFIX><SECTION>__<KEY>`` overrides out of ``environ``, sorted.

    This is the one filter every env input goes through. ``match`` says which
    spellings of ``prefix`` count: ``"exact"``; ``"variant"``, the same letters
    in another case only; or ``"any"``. A name is read only when its
    ``<SECTION>`` names a field of ``ToolkitSettings`` (``unknown_heads``
    lifts that, see :func:`_apply_env_overrides`) and the rest of it splits on
    ``__`` into non-empty segments. ``not all(path)`` rejects a degenerate key:
    ``CORE__`` splits to ``["core", ""]`` and would write an empty-string key
    onto the section, where ``extra=allow`` keeps it and ``model_dump()``
    renders it as junk.

    A bare ``<PREFIX><SECTION>`` would replace a whole section table with a
    scalar, and a scalar aimed at a dict-typed field is the same hazard one
    level down, which fails validation on every load for as long as the
    variable is exported. Both are skipped (the second is reported), except
    that with ``json_forms`` a JSON object is read as the table it spells, as
    pydantic-settings' env source read it in 1.0.0.
    """
    known_sections = frozenset(ToolkitSettings.model_fields)
    size = len(prefix)
    overrides: list[_Override] = []
    for name, raw in sorted(environ.items()):
        head = name[:size]
        if raw is None or head.upper() != prefix.upper():
            continue
        if (match == "exact" and head != prefix) or (match == "variant" and head == prefix):
            continue
        path = tuple(name[size:].lower().split(_ENV_DELIM))
        if not all(path):
            continue
        if path[0] not in known_sections:
            if unknown_heads:
                overrides.append(_Override(name, path, raw))
            continue
        if len(path) == 1 or (len(path) == 2 and _is_mapping_field(path[0], path[1])):
            table = _json_object(raw) if json_forms else None
            if table is not None:
                overrides.append(_Override(name, path, table))
            elif len(path) == 2:
                report.clobbered.append(name)
            continue
        overrides.append(_Override(name, path, raw))
    return overrides


def _merge_layer(
    target: dict[str, Any],
    layer: Mapping[str, Any],
    *,
    fill: bool = False,
    _section: str | None = None,
    _depth: int = 0,
) -> None:
    """Merge ``layer`` onto ``target`` in place, deep.

    Mappings merge key by key and anything else replaces, which is how
    pydantic-settings merges its sources. With ``fill``, a value already in
    ``target`` is kept and ``layer`` only adds what is missing.

    One level down, in a known section, a key matches its declared field
    whatever its case, as :class:`_SeamSubSettings` does. So a layer that sets
    ``tenant_id`` removes a lower layer's ``TENANT_ID``: otherwise a mixed-case
    key in the file would outrank an env override of the same field.
    """
    for key, value in layer.items():
        slot = key
        if _depth == 1 and _section is not None and isinstance(key, str):
            folded = key.lower()
            if _declared_field(_section, folded):
                variants = [k for k in target if isinstance(k, str) and k.lower() == folded]
                if fill and variants:
                    slot = key if key in target else variants[0]
                elif not fill:
                    for variant in variants:
                        if variant != key:
                            del target[variant]
        current = target.get(slot, _MISSING)
        if isinstance(value, Mapping) and isinstance(current, dict):
            _merge_layer(
                current,
                value,
                fill=fill,
                _section=key if _depth == 0 else _section,
                _depth=_depth + 1,
            )
        elif not fill or current is _MISSING:
            target[slot] = copy.deepcopy(value)


def _nest(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    nested: Any = value
    for segment in reversed(path):
        nested = {segment: nested}
    return dict(nested)


def _override_layer(overrides: Iterable[_Override]) -> dict[str, Any]:
    """One layer in which a later override replaces an earlier one.

    Overrides arrive sorted by name, so the result does not depend on the
    order the variables were exported in. A deeper path replaces a scalar
    on its way, as it always has.
    """
    layer: dict[str, Any] = {}
    for override in overrides:
        cursor = layer
        for segment in override.path[:-1]:
            nxt = cursor.get(segment)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[segment] = nxt
            cursor = nxt
        cursor[override.path[-1]] = override.value
    return layer


def _fill_layer(overrides: Iterable[_Override]) -> dict[str, Any]:
    """One layer in which the first override for a setting wins."""
    layer: dict[str, Any] = {}
    for override in overrides:
        _merge_layer(layer, _nest(override.path, override.value), fill=True)
    return layer


def _get_leaf(data: Mapping[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    """Return ``(present, value)`` at ``path``; field keys match in any case."""
    cursor: Any = data
    for depth, segment in enumerate(path):
        if not isinstance(cursor, dict):
            return False, None
        if segment in cursor:
            cursor = cursor[segment]
            continue
        folded = segment.lower()
        if depth == 1 and _declared_field(path[0], folded):
            spelled = next((k for k in cursor if isinstance(k, str) and k.lower() == folded), None)
            if spelled is not None:
                cursor = cursor[spelled]
                continue
        return False, None
    return True, cursor


def _same_value(current: Any, raw: str) -> bool:
    """True if a file or env value and an env string mean the same setting."""
    if current == raw:
        return True
    if isinstance(current, bool):
        return raw.strip().lower() in (_TRUE_STRINGS if current else _FALSE_STRINGS)
    if isinstance(current, int | float):
        return str(current) == raw.strip()
    return False


def _layer_env(
    environ: Mapping[str, str],
    report: _EnvReport,
    *,
    file_data: dict[str, Any],
    file_read_by_1_0_0: bool,
    unknown_legacy_heads: bool,
) -> dict[str, Any]:
    """Merge the process environment and ``file_data``; see :func:`_apply_env_overrides`."""
    new = _collect_overrides(environ, _ENV_PREFIX, match="exact", report=report)
    legacy = _collect_overrides(
        environ,
        _LEGACY_ENV_PREFIX,
        match="exact",
        report=report,
        unknown_heads=unknown_legacy_heads,
    )
    # Forms 1.0.0 read only through pydantic-settings' env source: FDT_ in
    # another letter case, and FDT_<SECTION>={JSON object}. That source ranked
    # below the values load_settings passed in, so these rank below the file.
    legacy_low = [
        *(
            o
            for o in _collect_overrides(
                environ, _LEGACY_ENV_PREFIX, match="exact", report=_EnvReport(), json_forms=True
            )
            if len(o.path) == 1
        ),
        *_collect_overrides(
            environ, _LEGACY_ENV_PREFIX, match="variant", report=report, json_forms=True
        ),
    ]
    report.legacy.extend(o.name for o in (*legacy, *legacy_low))
    new_layer = _override_layer(new)
    low_layer = _fill_layer(legacy_low)
    legacy_layer = _override_layer(legacy)
    if file_read_by_1_0_0:
        layers = [new_layer, low_layer, file_data, legacy_layer]
    else:
        layers = [file_data, new_layer, low_layer, legacy_layer]
    merged: dict[str, Any] = {}
    for layer in layers:
        _merge_layer(merged, layer)
    for override in new:
        if _get_leaf(new_layer, override.path) != (True, override.value):
            continue  # replaced by another SIGANTRY_ variable, not by a legacy source
        present, current = _get_leaf(merged, override.path)
        if not present or not _same_value(current, override.value):
            report.shadowed.append(override.name)
    return merged


def _file_style_layer(source: Mapping[str, str | None], report: _EnvReport) -> dict[str, Any]:
    """An env file or secrets directory under the default prefixes.

    pydantic-settings hands the env file's names over lower-cased, so the
    letter case of a prefix cannot be checked here. ``FDT_`` outranks
    ``SIGANTRY_`` within the source, as in the process environment.
    """
    legacy = _collect_overrides(
        source, _LEGACY_ENV_PREFIX, match="any", report=report, json_forms=True
    )
    report.legacy.extend(o.name for o in legacy)
    layer = _override_layer(_collect_overrides(source, _ENV_PREFIX, match="any", report=report))
    _merge_layer(layer, _fill_layer(legacy))
    return layer


def _custom_prefix_layer(
    source: Mapping[str, str | None], prefix: str, report: _EnvReport
) -> dict[str, Any]:
    """One input read under the caller's ``_env_prefix``, as 1.0.0 did."""
    overrides = _collect_overrides(source, prefix, match="any", report=report, json_forms=True)
    if prefix.upper() == _LEGACY_ENV_PREFIX:
        report.legacy.extend(o.name for o in overrides)
    return _fill_layer(overrides)


def _read_secrets_dir(secrets_dir: Any) -> dict[str, str]:
    """Read a pydantic-settings ``secrets_dir``: one file per variable, named
    after it, holding its value. A later directory wins, as in pydantic-settings;
    anything unreadable is skipped."""
    if not secrets_dir:
        return {}
    dirs = [secrets_dir] if isinstance(secrets_dir, str | os.PathLike) else list(secrets_dir)
    found: dict[str, str] = {}
    for directory in dirs:
        try:
            entries = sorted(Path(directory).expanduser().iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file():
                    found[entry.name] = entry.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
    return found


def _bare_names_1_0_0_would_read(
    environ: Mapping[str, str], *supplied: Mapping[str, Any]
) -> list[tuple[str, list[str]]]:
    """Unprefixed variables 1.0.0 would have bound, with their prefixed forms.

    Reported per field: only where nothing else supplies the field, since
    1.0.0 ranked an unprefixed name below every other source. A value aimed
    at a dict-typed field counts only if it is a JSON object, because 1.0.0
    failed on anything else.
    """
    found: list[tuple[str, list[str]]] = []
    for name, raw in sorted(environ.items()):
        targets = _BARE_NAMES_1_0_0.get(name.lower(), ())
        replacements = [
            f"{_ENV_PREFIX}{section.upper()}{_ENV_DELIM}{field.upper()}"
            for section, field in targets
            if not any(_get_leaf(source, (section, field))[0] for source in supplied)
            and (not _is_mapping_field(section, field) or _json_object(raw) is not None)
        ]
        if replacements:
            found.append((name, replacements))
    return found


def _apply_env_overrides(data: dict[str, Any], *, file_read_by_1_0_0: bool = False) -> None:
    """Layer settings env overrides onto the parsed config ``data`` (in place).

    pydantic-settings v2.1 does not have ``TomlConfigSettingsSource`` (added in
    2.2). We emulate env-layer precedence by walking ``os.environ`` ourselves
    with the ``SIGANTRY_`` prefix + ``__`` nested delimiter.

    Precedence, highest first. Every source sigantry 1.0.0 read outranks
    ``SIGANTRY_``, which it did not, so no upgrade changes a value 1.0.0 set:

    1. ``FDT_<SECTION>__<KEY>``, the deprecated legacy prefix, exact case;
    2. the file, when ``file_read_by_1_0_0`` (an explicit path, or the legacy
       file);
    3. ``FDT_`` names in another letter case, and ``FDT_<SECTION>`` holding a
       JSON object. 1.0.0 read these through pydantic-settings' own env
       source, below the file;
    4. ``SIGANTRY_<SECTION>__<KEY>``;
    5. the file otherwise (``.sigantry.toml`` found by default resolution).

    A ``SIGANTRY_`` value that a different legacy value outranks is named in a
    ``UserWarning``; legacy names in a ``DeprecationWarning``; unprefixed
    names 1.0.0 would have read in a ``FutureWarning``. Equal values are
    silent.

    Only ``<PREFIX><SECTION>__<KEY>`` forms are merged, and ``<SECTION>`` must
    name a field of ``ToolkitSettings``. **That restriction is load-bearing.**
    ``SIGANTRY_`` is also the prefix of the product's operational env vars --
    ``SIGANTRY_SMTP_PASSWORD``, ``SIGANTRY_GITHUB_TEST_PAT``,
    ``SIGANTRY_FABRIC_TOKEN`` and ~68 others. ``ToolkitSettings`` sets
    ``extra="allow"``, so an unfiltered sweep would bind every one of those --
    credentials included -- onto the settings object as an extra field, where
    ``model_dump()`` renders them in clear. The old ``FDT_`` prefix was
    namespace-exclusive and so never exposed this; adopting the shared
    ``SIGANTRY_`` prefix is what makes the filter necessary. For the same
    reason only the exact-case ``FDT_`` pass keeps 1.0.0's merge of a head
    that names no section, as a top-level extra; ``SIGANTRY_`` never does.

    The section names are read from the model rather than listed here, so a
    newly added seam cannot fall out of sync with this function.
    """
    report = _EnvReport()
    merged = _layer_env(
        os.environ,
        report,
        file_data=data,
        file_read_by_1_0_0=file_read_by_1_0_0,
        unknown_legacy_heads=True,
    )
    report.bare = _bare_names_1_0_0_would_read(os.environ, merged)
    data.clear()
    data.update(merged)
    report.emit()


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
