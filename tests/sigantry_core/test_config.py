"""Tests for :mod:`sigantry_core.config` (PROD-03)."""

from __future__ import annotations

import tomllib
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from sigantry_core.config import (
    ReleaseSettings,
    ToolkitSettings,
    WorkflowSettings,
    load_settings,
)

SAMPLE_TOML = """\
[core]
tenant_id = "tenant-abc-123"

[auth]
provider = "azure_default_credential"

[telemetry]
sink = "log_analytics"

[telemetry.log_analytics]
workspace_id = "la-ws-xyz"
endpoint = "https://dce.example.com"

[deploy]
profile = "example_profile"

[dq]
gate = "example_gate"

[runbooks]
registry = "static_map"

[runbooks.static_map]
CapacityCuThreshold = "https://runbooks.example.com/capacity-cu"

[capacity]
policy = "business_hours"
"""


def _write_sample(tmp_path: Path, body: str = SAMPLE_TOML) -> Path:
    p = tmp_path / ".fabric-dataops.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_settings_from_toml(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    settings = load_settings(path)
    assert isinstance(settings, ToolkitSettings)
    assert settings.core.tenant_id == "tenant-abc-123"
    assert settings.auth.provider == "azure_default_credential"
    assert settings.telemetry.sink == "log_analytics"
    assert settings.deploy.profile == "example_profile"
    assert settings.dq.gate == "example_gate"
    assert settings.runbooks.registry == "static_map"
    assert settings.capacity.policy == "business_hours"


def test_load_settings_core_tenant_id_optional(tmp_path: Path) -> None:
    """``core.tenant_id`` is optional at the base level.

    A greenfield consumer who only needs telemetry can omit it. Plugins that
    *need* a tenant id (AuthProvider, AimsDeployProfile) validate it
    themselves at resolve time.
    """
    body = "[auth]\nprovider = 'x'\n"
    path = _write_sample(tmp_path, body)
    settings = load_settings(path)
    assert settings.core.tenant_id is None
    assert settings.auth.provider == "x"


def test_load_settings_core_tenant_id_type_enforced_when_set(tmp_path: Path) -> None:
    """When supplied, ``core.tenant_id`` must still be a string (type fail-fast)."""
    body = "[core]\ntenant_id = 123\n"
    path = _write_sample(tmp_path, body)
    with pytest.raises(ValidationError):
        load_settings(path)


def test_load_settings_namespaced_plugin_table_passes_through(tmp_path: Path) -> None:
    """``[telemetry.log_analytics]`` is accessible via ``settings.telemetry``."""
    path = _write_sample(tmp_path)
    settings = load_settings(path)
    # ``extra="allow"`` exposes the nested log_analytics table via attribute access.
    log_analytics = settings.telemetry.log_analytics
    assert log_analytics["workspace_id"] == "la-ws-xyz"
    assert log_analytics["endpoint"] == "https://dce.example.com"


def test_load_settings_runbook_static_map_roundtrips(tmp_path: Path) -> None:
    """Runbook static map is a typed ``dict[str, str]``."""
    path = _write_sample(tmp_path)
    settings = load_settings(path)
    assert (
        settings.runbooks.static_map["CapacityCuThreshold"]
        == "https://runbooks.example.com/capacity-cu"
    )


def test_load_settings_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``FDT_CORE__TENANT_ID`` overrides the TOML value via pydantic-settings."""
    path = _write_sample(tmp_path)
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "override-tenant")
    settings = load_settings(path)
    assert settings.core.tenant_id == "override-tenant"


def test_load_settings_malformed_toml_raises(tmp_path: Path) -> None:
    """A syntactically broken TOML file raises ``tomllib.TOMLDecodeError``."""
    p = tmp_path / ".fabric-dataops.toml"
    p.write_text("core = \n[[??", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_settings(p)


def test_load_settings_missing_file_returns_empty_settings(tmp_path: Path) -> None:
    """With no file AND no env override, ``load_settings`` returns defaults.

    All seam sub-models ship with sensible defaults (``tenant_id=None``,
    ``sink=None``, etc.), so a consumer with no TOML file gets a fully-wired
    but plugin-less ``ToolkitSettings`` -- useful for direct-DI wiring paths.
    """
    missing = tmp_path / "does-not-exist.toml"
    settings = load_settings(missing)
    assert isinstance(settings, ToolkitSettings)
    assert settings.core.tenant_id is None
    assert settings.telemetry.sink is None


def test_load_settings_defaults_for_optional_sub_settings(tmp_path: Path) -> None:
    """Every seam section has a default; no field is required at the base level."""
    body = "[core]\ntenant_id = 't'\n"
    path = _write_sample(tmp_path, body)
    settings = load_settings(path)
    assert settings.telemetry.sink is None
    assert settings.deploy.profile is None
    assert settings.dq.gate is None
    assert settings.runbooks.registry is None
    assert settings.capacity.policy is None
    assert settings.runbooks.static_map == {}


def test_load_settings_empty_toml_is_valid(tmp_path: Path) -> None:
    """An empty TOML file is a valid starting point for a greenfield consumer."""
    p = tmp_path / ".fabric-dataops.toml"
    p.write_text("", encoding="utf-8")
    settings = load_settings(p)
    assert settings.core.tenant_id is None
    assert settings.telemetry.sink is None


# ---------------------------------------------------------------------------
# ReleaseSettings (Phase 11 -- Plan 11-06, TRACE-05)
# ---------------------------------------------------------------------------


def test_release_settings_default_to_empty(tmp_path: Path) -> None:
    """ReleaseSettings is optional -- empty defaults must not raise.

    A greenfield consumer who does not opt in to release tracking gets a
    fully-formed but empty ``ReleaseSettings`` block.
    """
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text("", encoding="utf-8")
    settings = load_settings(config_path)
    assert isinstance(settings.release, ReleaseSettings)
    assert settings.release.provider is None
    assert settings.release.audit_dir is None
    assert settings.release.ado == {}
    assert settings.release.github == {}


def test_release_settings_load_from_toml_with_per_provider_namespace(
    tmp_path: Path,
) -> None:
    """``[release]`` plus ``[release.ado]`` / ``[release.github]`` round-trip.

    Per-provider sub-namespaces are typed as ``dict[str, Any]`` so any
    provider constructor kwarg flows through without a schema migration.
    """
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text(
        """
[release]
provider = "ado"
audit_dir = "/var/sigantry/audit"

[release.ado]
organization = "myorg"
project = "myproj"
tenant_id = "tenant-xyz"

[release.github]
owner = "myorg"
repo = "myrepo"
""",
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    assert settings.release.provider == "ado"
    assert settings.release.audit_dir == "/var/sigantry/audit"
    assert settings.release.ado["organization"] == "myorg"
    assert settings.release.ado["project"] == "myproj"
    assert settings.release.ado["tenant_id"] == "tenant-xyz"
    assert settings.release.github["owner"] == "myorg"
    assert settings.release.github["repo"] == "myrepo"


def test_release_settings_extra_keys_pass_through(tmp_path: Path) -> None:
    """``extra='allow'`` lets unknown ``[release]`` keys flow through.

    Mirrors the convention used by the other seam sub-models (e.g. the
    ``[telemetry.log_analytics]`` table).
    """
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text(
        """
[release]
provider = "github"
unknown_field = "future-use"
""",
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    assert settings.release.provider == "github"
    # ``extra='allow'`` exposes unknown fields as attributes.
    assert settings.release.unknown_field == "future-use"


def test_release_settings_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``FDT_RELEASE__PROVIDER`` overrides the TOML value via the env layer."""
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text(
        '[release]\nprovider = "ado"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FDT_RELEASE__PROVIDER", "github")
    settings = load_settings(config_path)
    assert settings.release.provider == "github"


def test_release_settings_partial_load_with_only_per_provider_namespace(
    tmp_path: Path,
) -> None:
    """``[release.ado]`` alone (no top-level ``[release]``) still loads."""
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text(
        """
[release.ado]
organization = "solo-org"
project = "solo-proj"
""",
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    assert settings.release.provider is None
    assert settings.release.audit_dir is None
    assert settings.release.ado == {
        "organization": "solo-org",
        "project": "solo-proj",
    }
    assert settings.release.github == {}


# ---------------------------------------------------------------------------
# WorkflowSettings (Phase 13 -- Plan 13-07, SPEC §Constraints #1)
# ---------------------------------------------------------------------------


def test_workflow_settings_default_preview_apis_acknowledged_false(
    tmp_path: Path,
) -> None:
    """Default ``ToolkitSettings().workflow.preview_apis_acknowledged is False``.

    Plan 13-07 / SPEC §Constraints #1: the runtime gate defaults False so
    operators see the one-time WARNING on first ``sigantry sync apply`` /
    ``sigantry sync pull`` until they explicitly acknowledge the Preview
    Folders REST endpoint dependency (Council D #1).
    """
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text("", encoding="utf-8")
    settings = load_settings(config_path)
    assert isinstance(settings.workflow, WorkflowSettings)
    assert settings.workflow.preview_apis_acknowledged is False


def test_workflow_settings_toml_round_trip(tmp_path: Path) -> None:
    """``[workflow]`` namespace loads ``preview_apis_acknowledged`` from TOML.

    Setting the flag in ``.fabric-dataops.toml`` is the documented
    operator-side acknowledgement path (per docs/reference/api-stability.md).
    """
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text(
        """
[workflow]
preview_apis_acknowledged = true
""",
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    assert settings.workflow.preview_apis_acknowledged is True


def test_workflow_settings_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED=true`` overrides the TOML value.

    Mirrors the Phase 11 ``FDT_RELEASE__PROVIDER`` precedent: env wins over
    TOML so CI runners can opt in without touching the operator's checked-in
    config file.
    """
    config_path = tmp_path / ".fabric-dataops.toml"
    config_path.write_text(
        """
[workflow]
preview_apis_acknowledged = false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    settings = load_settings(config_path)
    assert settings.workflow.preview_apis_acknowledged is True


# ---------------------------------------------------------------------------
# Config-surface cutover (ADR-0011 / V3.X-ROADMAP LEGACY-SURFACE-DROP item 2)
# ---------------------------------------------------------------------------


def test_default_path_reads_sigantry_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_settings()`` with no argument reads ``.sigantry.toml``.

    This is the whole point of the cutover: before it, a file with the
    documented name was parsed by nobody and the operator got silent defaults.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sigantry.toml").write_text(
        '[core]\ntenant_id = "from-new-name"\n', encoding="utf-8"
    )
    assert load_settings().core.tenant_id == "from-new-name"


def test_default_path_falls_back_to_legacy_filename_with_deprecation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy filename still loads, but says it is on the way out."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".fabric-dataops.toml").write_text(
        '[core]\ntenant_id = "from-legacy-name"\n', encoding="utf-8"
    )
    with pytest.deprecated_call(match=r"\.fabric-dataops\.toml is deprecated"):
        settings = load_settings()
    assert settings.core.tenant_id == "from-legacy-name"


def test_new_filename_wins_over_legacy_and_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With both files present the new name wins and no deprecation fires."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sigantry.toml").write_text('[core]\ntenant_id = "new"\n', encoding="utf-8")
    (tmp_path / ".fabric-dataops.toml").write_text(
        '[core]\ntenant_id = "legacy"\n', encoding="utf-8"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        settings = load_settings()
    assert settings.core.tenant_id == "new"


def test_default_path_with_no_config_file_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No config file anywhere still yields all-defaults, as documented."""
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.core.tenant_id is None
    assert settings.approvals.gate is None


def test_explicit_path_is_used_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit path overrides resolution and may be named anything."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sigantry.toml").write_text('[core]\ntenant_id = "ignored"\n', encoding="utf-8")
    custom = tmp_path / "somewhere-else.toml"
    custom.write_text('[core]\ntenant_id = "explicit"\n', encoding="utf-8")
    assert load_settings(custom).core.tenant_id == "explicit"


def test_sigantry_env_prefix_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SIGANTRY_CORE__TENANT_ID`` is the new env-override surface."""
    path = _write_sample(tmp_path)
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "from-sigantry-env")
    assert load_settings(path).core.tenant_id == "from-sigantry-env"


def test_legacy_env_prefix_still_works_but_deprecates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FDT_`` keeps working for one minor, with a warning naming the key."""
    path = _write_sample(tmp_path)
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "from-legacy-env")
    with pytest.deprecated_call(match="FDT_CORE__TENANT_ID"):
        settings = load_settings(path)
    assert settings.core.tenant_id == "from-legacy-env"


def test_sigantry_env_prefix_wins_over_legacy_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both prefixes set for one setting: the new prefix is authoritative."""
    path = _write_sample(tmp_path)
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "legacy-value")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "new-value")
    with pytest.deprecated_call():
        settings = load_settings(path)
    assert settings.core.tenant_id == "new-value"


def test_unrelated_sigantry_env_vars_are_not_swept_into_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operational ``SIGANTRY_*`` vars must not become settings fields.

    ``SIGANTRY_`` is shared with the product's operational env vars, several
    of which are credentials. ``ToolkitSettings`` allows extra fields, so an
    unfiltered env sweep would bind them onto the settings object and render
    them in ``model_dump()``. The positive-control assertion at the end is
    what stops this test passing vacuously: a sweep that merged *nothing*
    would satisfy the negative assertions on its own.
    """
    path = _write_sample(tmp_path)
    monkeypatch.setenv("SIGANTRY_SMTP_PASSWORD", "hunter2-SECRET")
    monkeypatch.setenv("SIGANTRY_GITHUB_TEST_PAT", "ghp_SECRET")
    monkeypatch.setenv("SIGANTRY_FABRIC_TOKEN", "bearer-SECRET")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "legitimate-tenant")

    settings = load_settings(path)
    dumped = settings.model_dump()

    leaked = {k: v for k, v in dumped.items() if isinstance(v, str) and "SECRET" in v}
    assert leaked == {}, f"credentials leaked into settings: {sorted(leaked)}"
    assert not hasattr(settings, "smtp_password")
    assert not hasattr(settings, "github_test_pat")
    assert not hasattr(settings, "fabric_token")
    # Positive control: the real setting DID come through the same sweep.
    assert settings.core.tenant_id == "legitimate-tenant"


def test_bare_section_env_var_does_not_clobber_the_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SIGANTRY_CORE=x`` must not replace the ``[core]`` table with a string."""
    path = _write_sample(tmp_path)
    monkeypatch.setenv("SIGANTRY_CORE", "not-a-table")
    settings = load_settings(path)
    assert settings.core.tenant_id == "tenant-abc-123"


def test_unprefixed_env_vars_do_not_bind_to_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A BARE env var must never reach a settings field.

    Dropping pydantic-settings' env source on ``ToolkitSettings`` closed only
    the root model. Every section is ``Field(default_factory=<SubModel>)``, and
    while those sub-models were ``BaseSettings`` with no ``env_prefix``, each
    factory call ran its own ``EnvSettingsSource`` that matched UNPREFIXED
    names: ``TENANT_ID`` -> ``core.tenant_id``, ``PROVIDER`` ->
    ``auth.provider``, ``REGISTRY`` -> ``runbooks.registry``. A CI runner
    exporting ``REGISTRY`` for a container registry silently populated settings
    the operator never wrote.

    The existing sweep test cannot catch this: it only sets ``SIGANTRY_``-
    prefixed names, so it passes identically whether or not the hole is open.

    The config body here must leave the seam fields UNSET. ``SAMPLE_TOML``
    populates ``auth.provider``, ``telemetry.sink``, ``deploy.profile``,
    ``dq.gate`` and ``runbooks.registry``, and an init value outranks an env
    source -- so using it would mask the leak and make this test vacuous. That
    was measured: with ``SAMPLE_TOML``, reverting the sub-models to
    ``BaseSettings`` left this test green.
    """
    path = _write_sample(tmp_path, "[core]\ntenant_id = 'from-toml'\n")
    for bare in ("TENANT_ID", "PROVIDER", "REGISTRY", "SINK", "PROFILE", "GATE"):
        monkeypatch.setenv(bare, f"LEAKED-from-bare-{bare}")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "legitimate-tenant")

    settings = load_settings(path)

    leaked = [
        f"{section}.{field}"
        for section, field in (
            ("auth", "provider"),
            ("runbooks", "registry"),
            ("telemetry", "sink"),
            ("deploy", "profile"),
            ("dq", "gate"),
        )
        if "LEAKED" in str(getattr(getattr(settings, section), field))
    ]
    assert not leaked, f"bare env vars bound to settings field(s): {leaked}"
    # Positive control: the SUPPORTED prefixed form still binds, so this test
    # is not passing merely because no env override works at all.
    assert settings.core.tenant_id == "legitimate-tenant"


@pytest.mark.parametrize(
    "var",
    ["SIGANTRY_RELEASE__GITHUB", "SIGANTRY_RELEASE__ADO", "SIGANTRY_RUNBOOKS__STATIC_MAP"],
)
def test_scalar_env_var_aimed_at_a_table_is_skipped_not_fatal(
    var: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scalar aimed at a dict-typed field must not brick every settings load.

    ``SIGANTRY_RELEASE__GITHUB=x`` used to reach ``cursor[path[-1]] = raw_val``
    and hand pydantic a string where a mapping was declared, raising
    ``ValidationError`` out of ``load_settings`` for as long as the variable
    stayed exported -- the same "scalar replaces a table" hazard the bare-
    section guard removed, surviving one level deeper.
    """
    path = _write_sample(tmp_path)
    monkeypatch.setenv(var, "definitely-not-a-table")

    with pytest.warns(UserWarning, match="replace a table"):
        settings = load_settings(path)

    # The declared table is intact, and the rest of the config still loaded.
    assert isinstance(settings.release.github, dict)
    assert isinstance(settings.release.ado, dict)
    assert isinstance(settings.runbooks.static_map, dict)
    assert settings.core.tenant_id == "tenant-abc-123"


def test_env_key_with_an_empty_trailing_segment_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SIGANTRY_CORE__`` splits to ``["core", ""]`` and cleared the length guard.

    It then wrote an empty-string key onto the section, which ``extra="allow"``
    happily keeps and ``model_dump()`` renders as junk.
    """
    path = _write_sample(tmp_path)
    monkeypatch.setenv("SIGANTRY_CORE__", "junk")

    dumped = load_settings(path).core.model_dump()

    assert "" not in dumped, f"empty-string key written onto [core]: {sorted(dumped)}"
    assert dumped["tenant_id"] == "tenant-abc-123"
