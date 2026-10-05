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

The inputs sigantry 1.0.0 read are resolved first, by steps modelled on the
ones 1.0.0 took (see :func:`_result_1_0_0`), and the new surfaces,
``.sigantry.toml`` and ``SIGANTRY_`` variables, then fill only the settings
that result leaves unset. The result is validated against this release's
models; CHANGELOG.md, under "Upgrading from 1.0.0", describes ways it differs
from 1.0.0's. In outline:

- when ``.sigantry.toml`` and the legacy file both exist and differ, the
  legacy file is read, with a ``UserWarning``;
- an ``FDT_`` value, in any letter case, outranks a ``SIGANTRY_`` value for
  the same setting, and so does a file 1.0.0 read as well (an explicit
  ``path``, or the legacy file); over a ``.sigantry.toml`` found by default
  resolution, ``SIGANTRY_`` wins;
- a key or section name matches its field whatever its letter case, and
  where it is spelled twice the first spelling wins, as in 1.0.0;
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
from typing import Any, NamedTuple, get_origin

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


class _DefaultEnvPrefix(str):
    """The ``env_prefix`` ``ToolkitSettings`` declares: ``SIGANTRY_``, marked.

    pydantic-settings hands the env source one prefix: the call's
    ``_env_prefix``, or else the ``env_prefix`` of the model's
    ``model_config``, which a subclass inherits unless it declares its own.
    A non-empty prefix chosen either way, ``SIGANTRY_`` included, replaces
    the default prefixes (see :class:`_FilteredEnvSource`), as such a prefix
    replaced ``FDT_`` in 1.0.0. So a prefix is the default only when it is
    this object; a string passed or declared never is, whatever its value.
    """

    __slots__ = ()


_DEFAULT_ENV_PREFIX = _DefaultEnvPrefix(_ENV_PREFIX)

#: Legacy config files the ``fdt_settings_toml`` pytest fixture wrote, by
#: resolved path, for as long as the test that asked for them runs. See
#: :func:`_exempt_fixture_file`.
_FIXTURE_FILES: set[str] = set()

#: The same paths, as a JSON list, for the processes that test starts: the
#: fixture sets this variable while the test runs and they inherit it. It
#: starts with an underscore so no settings prefix, and no suite that clears
#: ``SIGANTRY_`` names, reaches it.
_FIXTURE_FILES_ENV = "_SIGANTRY_PYTEST_FIXTURE_FILES"


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
        declared fields are matched; any other key keeps its spelling. Where a
        field is spelled more than once, the first spelling wins, as it did in
        1.0.0. Each variant is reported with a ``DeprecationWarning``.

        :func:`load_settings` and a ``ToolkitSettings()`` built directly hand
        these models keys already matched this way (:func:`_fold_case`), so
        this only acts on a section model built directly.
        """
        if not isinstance(data, dict):
            return data
        declared = {name.lower(): name for name in cls.model_fields}
        out: dict[Any, Any] = {}
        folded: list[str] = []
        for key, value in data.items():
            target = None
            if isinstance(key, str):
                target = declared.get(key.lower())
            if target is None:
                out[key] = value
                continue
            if key != target:
                folded.append(key)
            if target not in out:
                out[target] = value
        if folded:
            section = _section_name_of(cls)
            _warn(
                _case_message(f"[{section}] {key}" if section else key for key in folded),
                DeprecationWarning,
            )
        return out


def _case_message(names: Iterable[str]) -> str:
    return (
        f"Settings key(s) {_names(names)} match a settings field only when case is "
        "ignored, which sigantry 1.0.0 allowed. Write them in lower case "
        "(for example tenant_id); case-insensitive matching will be removed "
        "in a future minor release."
    )


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
    """The only settings source of a ``ToolkitSettings()`` built directly.

    In 1.0.0 the constructor read ``FDT_`` variables itself, and honoured
    ``_env_file``, ``_secrets_dir``, ``_env_prefix`` and the ``env_prefix`` a
    subclass declares. This reads those inputs again: the values passed to
    the constructor and the names under the ``FDT_`` prefix, or under a
    non-empty prefix the caller passed or a subclass declared, are resolved
    by :func:`_result_1_0_0`. Without such a prefix, the ``SIGANTRY_``
    variables then fill what that leaves unset; with one, nothing does.
    CHANGELOG.md, under "Upgrading from 1.0.0", describes ways the result
    differs from 1.0.0's. The environment, the env file and the secrets
    directory are read through a filter, so no unprefixed name and no section
    this model does not declare is read from them.

    It returns the constructor values too, and pydantic-settings' own init
    source is left out (see ``settings_customise_sources``): merged a second
    time, a key the constructor spelled exactly would replace the value 1.0.0
    took from an earlier spelling of it.

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
        given = dict(self._init_kwargs)
        if _ENV_ALREADY_MERGED.get():
            return given
        report = _EnvReport()
        prefix = self._env_prefix
        if prefix == "":
            # pydantic-settings takes the call's ``_env_prefix``, or else the
            # ``env_prefix`` in ``model_config``: when that one is not empty,
            # the empty prefix was passed. When it is empty, the call may have
            # passed "" too, which this source cannot tell, and the warning
            # names ``model_config``.
            name = self.settings_cls.__name__
            if self.settings_cls.model_config.get("env_prefix") == "":
                report.empty_prefix = f'env_prefix="" in the model_config of {name}'
            else:
                report.empty_prefix = f'{name}(_env_prefix="")'
            prefix = None
        # A non-empty prefix the caller passed, or a subclass declared,
        # replaces the default prefixes, whatever its value, ``SIGANTRY_``
        # included, as it replaced ``FDT_`` in 1.0.0; only the one
        # ``ToolkitSettings`` declares is the default.
        custom = prefix is not None and not isinstance(prefix, _DefaultEnvPrefix)
        old_prefix = prefix if custom and prefix else _LEGACY_ENV_PREFIX
        dotenv = {k: v for k, v in self._dotenv_vars.items() if v is not None}
        # Highest rank first, as in pydantic-settings: the process environment,
        # then the env file, then the secrets directory.
        old = _result_1_0_0(
            given,
            [
                _env_source_1_0_0(os.environ, old_prefix, report),
                _env_source_1_0_0(dotenv, old_prefix, report),
                _secrets_source_1_0_0(self._secrets_dir, old_prefix, report),
            ],
            report,
        )
        new: list[list[_Override]] = []
        if not custom:
            # Under a prefix of the caller's choosing, the default SIGANTRY_
            # names are not read: only the names under that prefix, above.
            secrets = _read_secrets_dir(self._secrets_dir)
            new = [
                _collect_overrides(os.environ, _ENV_PREFIX, exact=True, report=report),
                _collect_overrides(dotenv, _ENV_PREFIX, exact=False, report=report),
                _collect_overrides(secrets, _ENV_PREFIX, exact=False, report=report),
            ]
        data = _fill_new_surfaces(old, {}, new, report, given=given)
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

    # ``env_prefix`` is the marked default (:class:`_DefaultEnvPrefix`), so a
    # prefix a call passes or a subclass declares can be told from it. Neither
    # it nor any other env option configures pydantic-settings' own env
    # source, which is never enabled: see ``settings_customise_sources``.
    model_config = SettingsConfigDict(
        extra="allow",
        case_sensitive=False,
        env_prefix=_DEFAULT_ENV_PREFIX,
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
        """One source: the filtered env layer, which carries the init values.

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
        env layer and nothing more. It merges the constructor values itself,
        so ``init_settings`` is only read from, never returned.
        """
        return (
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
    and the result is passed as the initial field values to ``ToolkitSettings``.

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
    data = _apply_env_overrides(data, file_read_by_1_0_0=resolved.read_by_1_0_0)
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

    A legacy file the ``fdt_settings_toml`` fixture wrote is read the same
    way, without either warning, in the test's process and in any process it
    starts (see :func:`_exempt_fixture_file`).
    """
    if path is not None:
        return _ResolvedConfig(Path(path), True)
    current = Path(_CONFIG_FILENAME)
    legacy = Path(_LEGACY_CONFIG_FILENAME)
    if not legacy.is_file():
        return _ResolvedConfig(current, False)
    fixture = _is_fixture_file(legacy)
    if current.is_file():
        if _same_bytes(current, legacy):
            return _ResolvedConfig(current, True)
        if not fixture:
            _warn(
                f"Both {_CONFIG_FILENAME} and {_LEGACY_CONFIG_FILENAME} exist in the "
                f"working directory. Reading {_LEGACY_CONFIG_FILENAME}, as sigantry 1.0.0 "
                f"did; {_CONFIG_FILENAME} is ignored. Move your settings into "
                f"{_CONFIG_FILENAME} and delete {_LEGACY_CONFIG_FILENAME} to switch. "
                "Two identical files are read without this warning.",
                UserWarning,
            )
        return _ResolvedConfig(legacy, True)
    if not fixture:
        _warn(
            f"{_LEGACY_CONFIG_FILENAME} is deprecated: rename it to "
            f"{_CONFIG_FILENAME}. The legacy filename is read for one more "
            "minor release and then removed (ADR-0011).",
            DeprecationWarning,
        )
    return _ResolvedConfig(legacy, True)


def _fixture_key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(path))


def _exempt_fixture_file(path: Path) -> str:
    """Read the legacy file at ``path`` without a warning while it is exempt.

    Only the ``fdt_settings_toml`` pytest fixture calls this, for the file it
    writes, and it lifts the exemption when the test that asked for the file
    ends (:func:`_release_fixture_file`). sigantry 1.0.0's fixture wrote that
    file under the legacy name and its loader read it silently, so a plugin's
    suite run with warnings as errors passed, and so did a script the test
    ran in a process of its own; the file is the fixture's choice, not the
    plugin author's, so a deprecation warning about it is not one they can
    act on. The exemption is for that one path: any other legacy file, and
    this one once the test ends, warns as before.

    Returns the value the fixture sets :data:`_FIXTURE_FILES_ENV` to for the
    test, so the processes it starts read the file silently too: the paths
    that variable already lists, and this one. The variable is kept (not the
    in-process set alone) because a child process has no other way to learn
    the path; the set is kept as well because a test may clear the
    environment in its own process.
    """
    key = _fixture_key(path)
    _FIXTURE_FILES.add(key)
    return json.dumps(sorted(_inherited_fixture_files() | {key}))


def _release_fixture_file(path: Path) -> None:
    _FIXTURE_FILES.discard(_fixture_key(path))


def _inherited_fixture_files() -> set[str]:
    """The paths :data:`_FIXTURE_FILES_ENV` lists; none when it is unset or malformed."""
    raw = os.environ.get(_FIXTURE_FILES_ENV)
    if not raw:
        return set()
    try:
        listed = json.loads(raw)
    except ValueError:
        return set()
    if not isinstance(listed, list):
        return set()
    return {item for item in listed if isinstance(item, str)}


def _is_fixture_file(path: Path) -> bool:
    keys = _FIXTURE_FILES | _inherited_fixture_files()
    if not keys:
        return False
    try:
        return _fixture_key(path) in keys
    except (OSError, ValueError):
        return False


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
#: (``auth.expected_group``) are not listed: 1.0.0 had no such field to bind.
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
        self.case: list[str] = []
        self.shadowed: list[str] = []
        self.bare: list[tuple[str, list[str]]] = []
        #: Where an empty env prefix came from, as the warning names it.
        self.empty_prefix: str | None = None

    def emit(self) -> None:
        if self.empty_prefix is not None:
            _warn(
                f"{self.empty_prefix} is not honoured: an empty prefix "
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
        if self.case:
            _warn(_case_message(self.case), DeprecationWarning)
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
    parsed = _json_value(raw)
    return parsed if isinstance(parsed, dict) else None


def _json_value(raw: object) -> Any:
    """Return ``raw`` parsed as JSON, or ``_MISSING`` if it is not JSON."""
    if not isinstance(raw, str):
        return _MISSING
    try:
        return json.loads(raw)
    except ValueError:
        return _MISSING


class _NotATable:
    """A value 1.0.0's env source read where a table belongs, that is not one.

    pydantic-settings kept such a value -- JSON that is not an object, or an
    empty ``<PREFIX><SECTION>__<DICT_FIELD>`` -- and a higher-ranked table at
    the same key replaced it, so its only lasting effect was where the key sat
    (which of two spellings of a field came first), and, for a section, that
    the section's ``__`` names in the same input were not read. Where nothing
    replaced it, 1.0.0 failed validation; :func:`_result_1_0_0` then drops it.
    """

    __slots__ = ("names",)

    def __init__(self, names: Iterable[str]) -> None:
        self.names = list(names)


# -- The inputs sigantry 1.0.0 read, resolved by steps modelled on 1.0.0's ----
#
# 1.0.0's load_settings() wrote its own FDT_ pass over the parsed file and
# passed the result to ToolkitSettings, whose pydantic-settings sources (2.15,
# case-insensitive) then did the rest. The functions below repeat those steps
# on the same inputs. They leave out what made 1.0.0 fail or write junk keys,
# and unprefixed names (see _bare_names_1_0_0_would_read). What they return is
# validated against this release's models, so the result can still differ
# from 1.0.0's; CHANGELOG.md, under "Upgrading from 1.0.0", describes how.


def _deep_update(lower: Mapping[str, Any], higher: Mapping[str, Any]) -> dict[str, Any]:
    """pydantic-settings' merge of two sources: ``higher`` wins at each leaf,
    mappings merge key by key, and ``lower``'s keys keep their place first."""
    merged = dict(lower)
    for key, value in higher.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_update(current, value)
        else:
            merged[key] = value
    return merged


def _fold_case(
    model: type[BaseModel], data: Mapping[Any, Any], report: _EnvReport, section: str | None
) -> dict[Any, Any]:
    """Match keys onto ``model``'s fields whatever their letter case.

    This is what a case-insensitive ``BaseSettings`` did with the values it was
    given in 1.0.0: for each field, the first key in ``data`` that spells it
    supplies the value and every other spelling is dropped. Keys that name no
    field keep their spelling and their value. Each other spelling is named in
    ``report``.
    """
    taken: set[Any] = set()
    folded: dict[Any, Any] = {}
    for field in model.model_fields:
        spelled = [k for k in data if isinstance(k, str) and k.lower() == field]
        if spelled:
            folded[field] = data[spelled[0]]
            taken.update(spelled)
            report.case.extend(
                (f"[{section}] {k}" if section else k) for k in spelled if k != field
            )
    folded.update((k, v) for k, v in data.items() if k not in taken)
    return folded


def _fold_sections(data: Mapping[str, Any], report: _EnvReport) -> dict[str, Any]:
    """Each declared section's keys, matched onto its fields (see :func:`_fold_case`)."""
    folded = dict(data)
    for section in ToolkitSettings.model_fields:
        model = _section_model(section)
        value = folded.get(section)
        if model is not None and isinstance(value, dict):
            folded[section] = _fold_case(model, value, report, section)
    return folded


def _legacy_pass_1_0_0(
    data: dict[str, Any], environ: Mapping[str, str], report: _EnvReport
) -> None:
    """1.0.0's own ``FDT_`` pass in :func:`load_settings`, in place over the file.

    Every name that starts with ``FDT_`` in exactly that case is written, in
    the order the environment lists them, so a later name replaces an earlier
    one for the same setting, as in 1.0.0. The rest of the name is lower-cased
    and split on ``__``; a head that names no section is kept as a top-level
    extra, and a scalar met on the way to a deeper name is replaced by a table.

    A scalar written where a table belongs -- ``FDT_<SECTION>``, or a dict-typed
    field -- made 1.0.0 fail, unless a later, deeper name replaced it with a
    table again (dropping what the file had there). One still in place at the
    end is taken back out, and the value it replaced restored; the env source
    reads such a name when it holds a JSON object, and any other value is
    reported. A degenerate name (``FDT_CORE__`` splits
    to ``["core", ""]``) is skipped: 1.0.0 wrote it as a junk key.
    """
    known = frozenset(ToolkitSettings.model_fields)
    size = len(_LEGACY_ENV_PREFIX)
    replaced: dict[tuple[str, ...], tuple[str, Any]] = {}
    for name, raw in environ.items():
        if not name.startswith(_LEGACY_ENV_PREFIX):
            continue
        path = tuple(name[size:].lower().split(_ENV_DELIM))
        if not all(path):
            continue
        report.legacy.append(name)
        cursor: dict[str, Any] = data
        for segment in path[:-1]:
            nxt = cursor.get(segment)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[segment] = nxt
            cursor = nxt
        if path[0] in known and (
            len(path) == 1 or (len(path) == 2 and _is_mapping_field(path[0], path[1]))
        ):
            replaced[path] = (name, cursor.get(path[-1], _MISSING))
        cursor[path[-1]] = raw
    for path, (name, before) in replaced.items():
        parent: Any = data
        for segment in path[:-1]:
            parent = parent.get(segment) if isinstance(parent, dict) else None
        if not isinstance(parent, dict) or isinstance(parent.get(path[-1]), dict):
            continue  # a deeper name made it a table again, as it did in 1.0.0
        if before is _MISSING:
            parent.pop(path[-1], None)
        else:
            parent[path[-1]] = before
        if _json_object(environ.get(name)) is None:
            report.clobbered.append(name)


def _env_source_1_0_0(
    environ: Mapping[str, str | None], prefix: str, report: _EnvReport
) -> dict[str, Any]:
    """What pydantic-settings' env source gave ``ToolkitSettings`` in 1.0.0.

    Names match the prefix and the section in any letter case; where two names
    differ only in case, the later one's value counts. Per section,
    ``<PREFIX><SECTION>`` holding a JSON object is the base, and the
    ``<PREFIX><SECTION>__<KEY>`` names are laid over it, so a nested name beats
    the JSON object whatever the order. ``<PREFIX><SECTION>`` holding JSON
    ``null`` leaves the section out, its ``__`` names too; holding other JSON
    that is not an object, it is a :class:`_NotATable` and its ``__`` names
    are not read. A dict-typed field takes a JSON object; an empty value or
    other JSON there is a :class:`_NotATable`. Only declared sections are
    read, and a value 1.0.0 failed to parse is skipped. Used for the process
    environment and for an env file.
    """
    low_prefix = prefix.lower()
    values: dict[str, str] = {}
    spelled: dict[str, list[str]] = {}
    for name, raw in environ.items():
        key = name.lower()
        if raw is None or not key.startswith(low_prefix):
            continue
        values[key] = raw
        spelled.setdefault(key, []).append(name)
    legacy = prefix.upper() == _LEGACY_ENV_PREFIX
    result: dict[str, Any] = {}
    for section in ToolkitSettings.model_fields:
        model = _section_model(section)
        if model is None:
            continue
        head = low_prefix + section
        used: list[str] = []
        parsed = _json_value(values.get(head))
        table = parsed if isinstance(parsed, dict) else None
        if parsed is not _MISSING and table is None:
            # Read as JSON and not an object: pydantic-settings returned it
            # as the section's value without reading the ``__`` names.
            if legacy:
                report.legacy.extend(spelled[head])
            if parsed is not None:
                result[section] = _NotATable(spelled[head])
            continue
        if table is not None:
            used.append(head)
        elif head in values:
            report.clobbered.extend(spelled[head])  # not JSON: 1.0.0 failed on it
        nested: dict[str, Any] = {}
        start = head + _ENV_DELIM
        for key, raw in values.items():
            if not key.startswith(start):
                continue
            *parents, last = key[len(start) :].split(_ENV_DELIM)
            if not all((*parents, last)):
                continue
            value: Any = raw
            if not parents and _is_mapping_field(section, last):
                # pydantic-settings parsed a non-empty value as JSON, and
                # failed when it was not JSON; any value but an object is
                # kept in its place.
                value = _json_value(raw) if raw else _NotATable(spelled[key])
                if value is _MISSING:
                    report.clobbered.extend(spelled[key])
                    continue
                if not isinstance(value, dict | _NotATable):
                    value = _NotATable(spelled[key])
            cursor: Any = nested
            for parent in parents:
                cursor = cursor.setdefault(parent, {}) if isinstance(cursor, dict) else cursor
            if isinstance(cursor, dict):
                cursor[last] = value
            used.append(key)
        if table is None and not nested:
            continue
        merged = _deep_update(table or {}, nested)
        # The env source renamed keys onto the fields they spell in any case;
        # where two spell one field, the later one wins.
        declared = {field.lower(): field for field in model.model_fields}
        result[section] = {
            declared.get(k.lower(), k) if isinstance(k, str) else k: v for k, v in merged.items()
        }
        if legacy:
            report.legacy.extend(n for key in used for n in spelled[key])
    return result


def _secrets_source_1_0_0(secrets_dir: Any, prefix: str, report: _EnvReport) -> dict[str, Any]:
    """What pydantic-settings' secrets source gave ``ToolkitSettings`` in 1.0.0.

    A file named ``<PREFIX><SECTION>`` in any letter case, holding a JSON
    object, sets that section: the first such name in directory order, in the
    last directory that has one. JSON ``null`` leaves the section out, and
    other JSON is a :class:`_NotATable`. Nested names are not read from files,
    and a file 1.0.0 failed to parse is skipped and reported.
    """
    dirs = [secrets_dir] if isinstance(secrets_dir, str | os.PathLike) else list(secrets_dir or ())
    result: dict[str, Any] = {}
    for section in ToolkitSettings.model_fields:
        model = _section_model(section)
        wanted = (prefix + section).lower()
        for directory in reversed(dirs):
            try:
                found = next(
                    (e for e in Path(directory).expanduser().iterdir() if e.name.lower() == wanted),
                    None,
                )
                raw = (
                    found.read_text(encoding="utf-8").strip() if found and found.is_file() else None
                )
            except (OSError, UnicodeDecodeError):
                continue
            if raw is None:
                continue
            parsed = _json_value(raw)
            if model is None or found is None:
                pass
            elif parsed is _MISSING:
                report.clobbered.append(found.name)  # not JSON: 1.0.0 failed on it
            else:
                if isinstance(parsed, dict):
                    declared = {field.lower(): field for field in model.model_fields}
                    result[section] = {declared.get(k.lower(), k): v for k, v in parsed.items()}
                elif parsed is not None:
                    result[section] = _NotATable([found.name])
                if prefix.upper() == _LEGACY_ENV_PREFIX:
                    report.legacy.append(found.name)
            break
    return result


def _result_1_0_0(
    given: Mapping[str, Any], sources: Iterable[Mapping[str, Any]], report: _EnvReport
) -> dict[str, Any]:
    """What ``ToolkitSettings`` was given, merged with its env sources (highest
    rank first) in the order 1.0.0 merged them.

    The given values' section names are matched first, as pydantic-settings
    matched the constructor's arguments; the sources are merged below them;
    then each section's keys are matched onto its fields. That order is why a
    file key spelled ``TENANT_ID`` lost to an ``fdt_core__tenant_id`` variable
    in 1.0.0 while a file key spelled ``tenant_id`` won, and it is kept.

    A :class:`_NotATable` still in place once the sources are merged made
    1.0.0 fail validation. It is dropped, before the keys are matched, and
    named in a warning.
    """
    state = _fold_case(ToolkitSettings, given, report, None)
    for source in sources:
        state = _deep_update(source, state)
    return _fold_sections(_drop_not_a_table(state, report), report)


def _drop_not_a_table(state: Mapping[str, Any], report: _EnvReport) -> dict[str, Any]:
    """``state`` without the :class:`_NotATable` values the env sources left
    in it: at a section, or at a field of a section's table."""
    kept: dict[str, Any] = {}
    for key, value in state.items():
        if isinstance(value, _NotATable):
            report.clobbered.extend(value.names)
            continue
        if isinstance(value, dict) and any(isinstance(v, _NotATable) for v in value.values()):
            for v in value.values():
                if isinstance(v, _NotATable):
                    report.clobbered.extend(v.names)
            value = {k: v for k, v in value.items() if not isinstance(v, _NotATable)}
        kept[key] = value
    return kept


# -- The surfaces 1.0.0 did not read ------------------------------------------


def _collect_overrides(
    environ: Mapping[str, str | None],
    prefix: str,
    *,
    exact: bool,
    report: _EnvReport,
) -> list[_Override]:
    """Read ``<PREFIX><SECTION>__<KEY>`` overrides out of ``environ``, sorted.

    The filter every ``SIGANTRY_`` input goes through. With ``exact``, the
    prefix must be spelled as given; otherwise in any case (an env file's
    names arrive lower-cased). A name is read only when its ``<SECTION>``
    names a field of ``ToolkitSettings`` and the rest of it splits on ``__``
    into non-empty segments. ``not all(path)`` rejects a degenerate key:
    ``CORE__`` splits to ``["core", ""]`` and would write an empty-string key
    onto the section, where ``extra=allow`` keeps it and ``model_dump()``
    renders it as junk.

    A bare ``<PREFIX><SECTION>`` would replace a whole section table with a
    scalar, and a scalar aimed at a dict-typed field is the same hazard one
    level down, which fails validation on every load for as long as the
    variable is exported. Both are skipped, and the second is reported.
    """
    known_sections = frozenset(ToolkitSettings.model_fields)
    size = len(prefix)
    overrides: list[_Override] = []
    for name, raw in sorted(environ.items()):
        head = name[:size]
        if raw is None or (head != prefix if exact else head.upper() != prefix.upper()):
            continue
        path = tuple(name[size:].lower().split(_ENV_DELIM))
        if len(path) < 2 or not all(path) or path[0] not in known_sections:
            continue
        if len(path) == 2 and _is_mapping_field(path[0], path[1]):
            report.clobbered.append(name)
            continue
        overrides.append(_Override(name, path, raw))
    return overrides


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


def _filled(target: Mapping[str, Any], layer: Mapping[str, Any]) -> dict[str, Any]:
    """``target`` plus every leaf of ``layer`` it does not set.

    Neither argument is changed: ``target`` can hold the caller's own
    dicts, passed to the constructor.
    """
    merged = dict(target)
    for key, value in layer.items():
        current = merged.get(key)
        if key not in merged:
            merged[key] = copy.deepcopy(value)
        elif isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _filled(current, value)
    return merged


def _get_leaf(data: Mapping[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    """Return ``(present, value)`` at ``path``."""
    cursor: Any = data
    for segment in path:
        if isinstance(cursor, BaseModel):
            cursor = cursor.model_dump(exclude_unset=True)
        if not isinstance(cursor, dict) or segment not in cursor:
            return False, None
        cursor = cursor[segment]
    return True, cursor


def _passed_in(given: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    """True if the constructor's values set ``path``, or a whole model or
    value on the way to it."""
    cursor: Any = given
    for segment in path:
        if not isinstance(cursor, dict):
            return True
        if segment not in cursor:
            return False
        cursor = cursor[segment]
    return True


def _same_value(current: Any, raw: str) -> bool:
    """True if a file or env value and an env string mean the same setting."""
    if current == raw:
        return True
    if isinstance(current, bool):
        return raw.strip().lower() in (_TRUE_STRINGS if current else _FALSE_STRINGS)
    if isinstance(current, int | float):
        return str(current) == raw.strip()
    return False


def _fill_new_surfaces(
    old: Mapping[str, Any],
    new_file: Mapping[str, Any],
    new_vars: Iterable[Iterable[_Override]],
    report: _EnvReport,
    *,
    given: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``old``, from :func:`_result_1_0_0`, filled from the surfaces 1.0.0 did
    not read.

    ``new_file`` is a ``.sigantry.toml`` 1.0.0 would not have opened, and
    ``new_vars`` the ``SIGANTRY_`` overrides from each input, highest rank
    first; a variable outranks the file. Together they only add keys ``old``
    leaves unset, and change no value ``old`` holds. A
    ``SIGANTRY_`` variable whose value a different legacy value keeps out is
    named in ``report``; one outranked by a value passed to the constructor
    (``given``) is not, since that is not a legacy setting.
    """
    ranked = [list(overrides) for overrides in new_vars]
    new = _fold_sections(_fold_case(ToolkitSettings, new_file, report, None), report)
    for overrides in reversed(ranked):
        new = _deep_update(new, _override_layer(overrides))
    merged = _filled(old, new)
    given = _fold_sections(
        _fold_case(ToolkitSettings, given or {}, _EnvReport(), None), _EnvReport()
    )
    for override in (o for overrides in ranked for o in overrides):
        if _get_leaf(new, override.path) != (True, override.value):
            continue  # replaced by another SIGANTRY_ variable, not by a legacy source
        if _passed_in(given, override.path):
            continue
        present, current = _get_leaf(merged, override.path)
        if not present or not _same_value(current, override.value):
            report.shadowed.append(override.name)
    report.bare = _bare_names_1_0_0_would_read(os.environ, merged)
    return merged


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
    environ: Mapping[str, str], supplied: Mapping[str, Any]
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
            if not _get_leaf(supplied, (section, field))[0]
            and (not _is_mapping_field(section, field) or _json_object(raw) is not None)
        ]
        if replacements:
            found.append((name, replacements))
    return found


def _apply_env_overrides(
    data: dict[str, Any], *, file_read_by_1_0_0: bool = False
) -> dict[str, Any]:
    """Return the parsed config ``data`` with the settings env vars layered on.

    pydantic-settings v2.1 does not have ``TomlConfigSettingsSource`` (added in
    2.2). We emulate env-layer precedence by walking ``os.environ`` ourselves.

    A value from an input sigantry 1.0.0 read outranks one from an input it
    did not. The inputs 1.0.0 read -- the file when ``file_read_by_1_0_0`` (an
    explicit path, or the legacy file), and ``FDT_`` variables in any letter
    case -- are resolved by steps modelled on 1.0.0's
    (:func:`_legacy_pass_1_0_0`, :func:`_env_source_1_0_0`,
    :func:`_result_1_0_0`); CHANGELOG.md, under "Upgrading from 1.0.0",
    describes ways the result differs from 1.0.0's. In outline:
    ``FDT_<SECTION>__<KEY>`` spelled with an exact ``FDT_`` wins; then the
    file; then ``FDT_`` names in another letter case and ``FDT_<SECTION>``
    holding a JSON object, which pydantic-settings
    read below the file -- but above a file key spelled in another case. The
    surfaces 1.0.0 did not read then fill what that leaves unset:
    ``SIGANTRY_<SECTION>__<KEY>``, over ``.sigantry.toml`` when that is the
    file (found by default resolution).

    A ``SIGANTRY_`` value that a different legacy value outranks is named in a
    ``UserWarning``; legacy names and keys matched in another case in a
    ``DeprecationWarning``; unprefixed names 1.0.0 would have read in a
    ``FutureWarning``. Equal values are silent.

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
    old_file, new_file = (data, {}) if file_read_by_1_0_0 else ({}, data)
    _legacy_pass_1_0_0(old_file, os.environ, report)
    old = _result_1_0_0(
        old_file, [_env_source_1_0_0(os.environ, _LEGACY_ENV_PREFIX, report)], report
    )
    new_vars = [_collect_overrides(os.environ, _ENV_PREFIX, exact=True, report=report)]
    merged = _fill_new_surfaces(old, new_file, new_vars, report)
    report.emit()
    return merged


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
