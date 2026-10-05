"""Settings inputs sigantry 1.0.0 read keep the result they had there (1.0.1).

Each test pins one 1.0.0 result for an input 1.0.0 read, or a guard that
keeps a later fix in place while that result comes back. The config file
names come from the loader's own constants, so these tests follow the loader
if a name ever changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

import sigantry_core
from sigantry_core.config import (
    _CONFIG_FILENAME,
    _LEGACY_CONFIG_FILENAME,
    ToolkitSettings,
    load_settings,
)

#: Names sigantry 1.0.0 bound without a prefix (its section models were
#: ``BaseSettings`` with none), cleared so the host environment cannot leak in.
_UNPREFIXED = frozenset(
    {
        "TENANT_ID",
        "PROVIDER",
        "SINK",
        "PROFILE",
        "GATE",
        "REGISTRY",
        "STATIC_MAP",
        "POLICY",
        "AUDIT_DIR",
        "ADO",
        "GITHUB",
        "STORE",
        "BOT",
        "PREVIEW_APIS_ACKNOWLEDGED",
        "EXPECTED_GROUP",
    }
)


@pytest.fixture(autouse=True)
def _isolated_settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No settings variable from the host, and the working dir is ``tmp_path``."""
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith(("FDT_", "SIGANTRY_", "MY_")) or upper in _UNPREFIXED:
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def _write(name: str, body: str) -> Path:
    path = Path(name)
    path.write_text(body, encoding="utf-8")
    return path


def _load(path: Path | None = None) -> tuple[ToolkitSettings, list[warnings.WarningMessage]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = load_settings(path)
    return settings, caught


def _construct(**kwargs: Any) -> tuple[ToolkitSettings, list[warnings.WarningMessage]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = ToolkitSettings(**kwargs)
    return settings, caught


def _messages(caught: list[warnings.WarningMessage], category: type[Warning]) -> list[str]:
    return [str(w.message) for w in caught if w.category is category]


def _as_listed(name: str) -> str:
    """``name`` as ``os.environ`` lists it once set: unchanged on POSIX, in
    upper case on Windows, where environment names are not case-sensitive."""
    return next(listed for listed in os.environ if listed.upper() == name.upper())


def _posix_only(reason: str) -> pytest.MarkDecorator:
    """Skip on Windows, which folds environment names to upper case: a name
    in lower or mixed case cannot be set there, nor two names that differ
    only in letter case."""
    return pytest.mark.skipif(sys.platform == "win32", reason=reason)


# ---------------------------------------------------------------------------
# 1.1 Both config files present: the legacy file is read, as 1.0.0 did
# ---------------------------------------------------------------------------


def test_both_files_read_legacy_as_1_0_0_did_and_warn() -> None:
    _write(
        _LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "T-legacy"\n[auth]\nprovider = "p-legacy"\n'
    )
    _write(_CONFIG_FILENAME, '[core]\ntenant_id = "T-new"\n')

    settings, caught = _load()

    assert (settings.core.tenant_id, settings.auth.provider) == ("T-legacy", "p-legacy")
    both = [m for m in _messages(caught, UserWarning) if m.startswith("Both ")]
    assert len(both) == 1, [str(w.message) for w in caught]
    assert _CONFIG_FILENAME in both[0] and _LEGACY_CONFIG_FILENAME in both[0]


def test_both_files_identical_are_silent() -> None:
    body = '[core]\ntenant_id = "T-same"\n'
    _write(_LEGACY_CONFIG_FILENAME, body)
    _write(_CONFIG_FILENAME, body)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        settings = load_settings()

    assert settings.core.tenant_id == "T-same"


def test_malformed_new_file_beside_legacy_still_loads() -> None:
    """1.0.0 never opened the new file, so a broken one cannot stop the load."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "T-legacy"\n')
    _write(_CONFIG_FILENAME, "core = \n[[??")

    settings, _ = _load()

    assert settings.core.tenant_id == "T-legacy"


def test_from_config_with_both_files_builds() -> None:
    """The new file names a plugin that is not installed; 1.0.0 never read it."""
    from sigantry_core.api import FabricDataOps

    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "t1"\n')
    _write(_CONFIG_FILENAME, '[core]\ntenant_id = "t1"\n[auth]\nprovider = "not-installed"\n')

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ops = FabricDataOps.from_config()

    assert ops.settings.auth.provider is None


# ---------------------------------------------------------------------------
# 1.2 A SIGANTRY_ variable no longer overrides a value 1.0.0 read
# ---------------------------------------------------------------------------


def test_fdt_value_beats_sigantry_value_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "fdt")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "sig")

    settings, caught = _load()

    assert settings.core.tenant_id == "fdt"
    shadowed = [m for m in _messages(caught, UserWarning) if "SIGANTRY_CORE__TENANT_ID" in m]
    assert len(shadowed) == 1, [str(w.message) for w in caught]


@pytest.mark.parametrize("explicit", [False, True], ids=["default-path", "explicit-path"])
def test_legacy_file_value_beats_sigantry_value_and_warns(
    explicit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit path was read by 1.0.0 whatever its name, so it counts too."""
    name = "custom.toml" if explicit else _LEGACY_CONFIG_FILENAME
    path = _write(name, '[core]\ntenant_id = "T-legacy"\n')
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "sig")

    settings, caught = _load(path if explicit else None)

    assert settings.core.tenant_id == "T-legacy"
    shadowed = [m for m in _messages(caught, UserWarning) if "SIGANTRY_CORE__TENANT_ID" in m]
    assert len(shadowed) == 1, [str(w.message) for w in caught]


def test_lower_case_legacy_prefix_beats_sigantry_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """1.0.0 read ``fdt_...`` and ignored ``SIGANTRY_...``, so the former still wins."""
    monkeypatch.setenv("fdt_core__tenant_id", "low")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "sig")

    settings, caught = _load()

    assert settings.core.tenant_id == "low"
    assert any("SIGANTRY_CORE__TENANT_ID" in m for m in _messages(caught, UserWarning))


def test_sigantry_fills_key_legacy_file_leaves_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "T-legacy"\n')
    monkeypatch.setenv("SIGANTRY_AUTH__PROVIDER", "sig-provider")

    settings, caught = _load()

    assert (settings.core.tenant_id, settings.auth.provider) == ("T-legacy", "sig-provider")
    assert _messages(caught, UserWarning) == []


def test_equal_values_do_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    _write(_LEGACY_CONFIG_FILENAME, "[workflow]\npreview_apis_acknowledged = true\n")
    monkeypatch.setenv("SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")

    settings, caught = _load()

    assert settings.workflow.preview_apis_acknowledged is True
    assert _messages(caught, UserWarning) == []


def test_sigantry_overrides_default_resolved_new_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: over ``.sigantry.toml``, which 1.0.0 never read, env still wins."""
    _write(_CONFIG_FILENAME, '[core]\ntenant_id = "T-new"\n')
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "sig")

    settings, caught = _load()

    assert settings.core.tenant_id == "sig"
    assert _messages(caught, UserWarning) == []


def test_shadow_warning_never_prints_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "VALUE-FROM-FILE"\n')
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "VALUE-FROM-NEW-ENV")
    monkeypatch.setenv("FDT_AUTH__PROVIDER", "VALUE-FROM-LEGACY-ENV")
    monkeypatch.setenv("SIGANTRY_AUTH__PROVIDER", "VALUE-FROM-NEW-ENV-2")

    _, caught = _load()

    text = "\n".join(str(w.message) for w in caught)
    assert "SIGANTRY_CORE__TENANT_ID" in text and "SIGANTRY_AUTH__PROVIDER" in text
    assert "VALUE-FROM" not in text


# ---------------------------------------------------------------------------
# 1.3 FDT_ in another letter case, and FDT_<SECTION>={JSON object}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["fdt_core__tenant_id", "Fdt_Core__Tenant_Id"])
def test_lower_case_legacy_prefix_binds_when_file_is_silent(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "lower")

    settings, caught = _load()

    assert settings.core.tenant_id == "lower"
    assert any(_as_listed(name) in m for m in _messages(caught, DeprecationWarning))


@_posix_only(
    "Windows sets fdt_core__tenant_id as FDT_CORE__TENANT_ID, the exact prefix, which "
    "outranked the file in 1.0.0 too (test_fdt_value_beats_sigantry_value_and_warns)"
)
@pytest.mark.parametrize("name", ["fdt_core__tenant_id", "Fdt_Core__Tenant_Id"])
def test_lower_case_legacy_prefix_ranks_below_file(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: 1.0.0 read these through pydantic-settings, below the file."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "T-legacy"\n')
    monkeypatch.setenv(name, "lower")

    settings, _ = _load()

    assert settings.core.tenant_id == "T-legacy"


@_posix_only("on Windows FDT_CORE__TENANT_ID and fdt_core__tenant_id are one variable")
def test_exact_prefix_beats_lower_case_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "UP")
    monkeypatch.setenv("fdt_core__tenant_id", "low")

    settings, _ = _load()

    assert settings.core.tenant_id == "UP"


def test_legacy_section_json_object_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_WORKFLOW", json.dumps({"preview_apis_acknowledged": True}))

    settings, caught = _load()
    direct, _ = _construct()

    assert settings.workflow.preview_apis_acknowledged is True
    assert direct.workflow.preview_apis_acknowledged is True
    assert any("FDT_WORKFLOW" in m for m in _messages(caught, DeprecationWarning))


def test_lower_case_legacy_dict_field_json_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows the name is set as ``FDT_RUNBOOKS__STATIC_MAP``, which 1.0.0's
    ``load_settings()`` failed on; it binds the same table now."""
    monkeypatch.setenv("fdt_runbooks__static_map", json.dumps({"Alert": "https://x.example"}))

    settings, _ = _load()

    assert settings.runbooks.static_map == {"Alert": "https://x.example"}


@_posix_only(
    "Windows sets sigantry_core__tenant_id as SIGANTRY_CORE__TENANT_ID, the exact "
    "spelling, which is read"
)
def test_lower_case_new_prefix_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: SIGANTRY_ is new; only its exact spelling is read."""
    monkeypatch.setenv("sigantry_core__tenant_id", "lower-new")

    settings, _ = _load()

    assert settings.core.tenant_id is None


def test_legacy_section_scalar_still_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: a scalar would replace the whole table, which crashed 1.0.0."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "T-legacy"\n')
    monkeypatch.setenv("FDT_CORE", "not-a-table")
    monkeypatch.setenv("fdt_auth", "not-a-table-either")

    settings, _ = _load()

    assert settings.core.tenant_id == "T-legacy"
    assert settings.auth.provider is None


@pytest.mark.parametrize("build", ["load", "construct"])
@pytest.mark.parametrize(
    "order", [("json", "nested"), ("nested", "json")], ids=["json-first", "nested-first"]
)
def test_nested_legacy_name_beats_the_section_json_object(
    order: tuple[str, str], build: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pydantic-settings laid ``fdt_<section>__<key>`` over the JSON object in
    ``fdt_<section>`` in 1.0.0, whichever was exported first."""
    names = {
        "json": ("fdt_core", json.dumps({"tenant_id": "from-json"})),
        "nested": ("fdt_core__tenant_id", "from-nested"),
    }
    for which in order:
        monkeypatch.setenv(*names[which])

    settings, _ = _load() if build == "load" else _construct()

    assert settings.core.tenant_id == "from-nested"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("fdt_core__tenant_id", "from-env", "from-env"),
        ("Fdt_Core__Tenant_Id", "from-env", "from-env"),
        ("fdt_core", json.dumps({"tenant_id": "from-json"}), "from-json"),
    ],
)
def test_legacy_name_beats_a_file_key_spelled_in_another_case(
    name: str, value: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In 1.0.0 the env source's ``tenant_id`` came before the file's
    ``TENANT_ID`` in the merged section, and the first spelling won. A file
    key spelled ``tenant_id`` still wins over these names (see
    ``test_lower_case_legacy_prefix_ranks_below_file``)."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\nTENANT_ID = "from-file"\n')
    monkeypatch.setenv(name, value)

    settings, _ = _load()

    assert settings.core.tenant_id == expected


# ---------------------------------------------------------------------------
# 1.4 FDT_ heads that name no section are merged again (load_settings only)
# ---------------------------------------------------------------------------


def test_legacy_prefix_unknown_section_lands_in_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_MYPLUG__K", "v")

    settings, caught = _load()

    assert settings.model_extra == {"myplug": {"k": "v"}}
    assert any("FDT_MYPLUG__K" in m for m in _messages(caught, DeprecationWarning))


def test_direct_construction_does_not_bind_unknown_legacy_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: 1.0.0's constructor never bound them."""
    monkeypatch.setenv("FDT_MYPLUG__K", "v")

    settings, _ = _construct()

    assert settings.model_extra == {}


def test_new_prefix_unknown_section_is_not_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: SIGANTRY_ is shared with operational variables, credentials included."""
    monkeypatch.setenv("SIGANTRY_MYPLUG__K", "v")
    monkeypatch.setenv("SIGANTRY_SMTP__PASSWORD", "hunter2-SECRET")
    monkeypatch.setenv("FDT_MYPLUG__K2", "v2")

    settings, _ = _load()

    assert settings.model_extra == {"myplug": {"k2": "v2"}}
    assert "SECRET" not in repr(settings.model_dump())


# ---------------------------------------------------------------------------
# 1.5 ToolkitSettings() built directly reads the environment again
# ---------------------------------------------------------------------------


def test_direct_construction_reads_legacy_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "fdt")

    settings, caught = _construct()

    assert settings.core.tenant_id == "fdt"
    assert any("FDT_CORE__TENANT_ID" in m for m in _messages(caught, DeprecationWarning))


def test_direct_construction_reads_new_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "sig")

    settings, _ = _construct()

    assert settings.core.tenant_id == "sig"


def test_direct_construction_init_outranks_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "fdt")
    monkeypatch.setenv("FDT_AUTH__PROVIDER", "from-env")

    settings, _ = _construct(core={"tenant_id": "init"})

    assert (settings.core.tenant_id, settings.auth.provider) == ("init", "from-env")


def test_direct_construction_ignores_bare_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: the env layer goes through the prefix filter; S-1 stays closed."""
    monkeypatch.setenv("TENANT_ID", "bare")
    monkeypatch.setenv("PROVIDER", "bare")

    settings, _ = _construct()

    assert (settings.core.tenant_id, settings.auth.provider) == (None, None)


def test_direct_construction_honours_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "FDT_CORE__TENANT_ID=from-dotenv\nSIGANTRY_AUTH__PROVIDER=from-dotenv\n"
        "TENANT_ID=bare\nFDT_MYPLUG__K=v\n",
        encoding="utf-8",
    )

    settings, _ = _construct(_env_file=env_file)

    assert (settings.core.tenant_id, settings.auth.provider) == ("from-dotenv", "from-dotenv")
    assert settings.model_extra == {}


def test_direct_construction_process_env_outranks_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text("FDT_CORE__TENANT_ID=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "from-process")

    settings, _ = _construct(_env_file=env_file)

    assert settings.core.tenant_id == "from-process"


def test_direct_construction_honours_secrets_dir(tmp_path: Path) -> None:
    secrets = tmp_path / "sd"
    secrets.mkdir()
    (secrets / "fdt_core").write_text(json.dumps({"tenant_id": "from-secret"}), encoding="utf-8")
    (secrets / "tenant_id").write_text("bare", encoding="utf-8")

    settings, _ = _construct(_secrets_dir=secrets)

    assert settings.core.tenant_id == "from-secret"


def test_direct_construction_honours_custom_prefix_through_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_CORE__TENANT_ID", "from-myprefix")
    monkeypatch.setenv("FDT_AUTH__PROVIDER", "not-read-under-a-custom-prefix")

    settings, _ = _construct(_env_prefix="MY_")

    assert settings.core.tenant_id == "from-myprefix"
    assert settings.auth.provider is None


def test_direct_construction_custom_prefix_cannot_reach_unknown_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: a custom prefix is filtered exactly like the default ones."""
    monkeypatch.setenv("MY_SMTP_PASSWORD", "hunter2-SECRET")
    monkeypatch.setenv("MY_MYPLUG__K", "v")
    monkeypatch.setenv("MY_CORE", "not-a-table")
    monkeypatch.setenv("MY_CORE__TENANT_ID", "ok")

    settings, _ = _construct(_env_prefix="MY_")

    assert settings.model_extra == {}
    assert settings.core.tenant_id == "ok"
    assert "SECRET" not in repr(settings.model_dump())


def test_direct_construction_refuses_an_empty_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty prefix would read unprefixed names, so the default ones are read."""
    monkeypatch.setenv("TENANT_ID", "bare")
    monkeypatch.setenv("SIGANTRY_AUTH__PROVIDER", "sig")

    settings, caught = _construct(_env_prefix="")

    assert (settings.core.tenant_id, settings.auth.provider) == (None, "sig")
    assert any("_env_prefix" in m for m in _messages(caught, UserWarning))


def test_direct_construction_reads_exact_case_dict_field_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1.0.0's constructor read a JSON object for a dict-typed field from
    ``FDT_<SECTION>__<FIELD>``, as well as from the lower-case spelling."""
    monkeypatch.setenv("FDT_RUNBOOKS__STATIC_MAP", json.dumps({"Alert": "https://x.example"}))
    monkeypatch.setenv("FDT_RELEASE__ADO", json.dumps({"organization": "o"}))
    monkeypatch.setenv("FDT_RELEASE__GITHUB", json.dumps({"owner": "w"}))

    settings, caught = _construct()

    assert settings.runbooks.static_map == {"Alert": "https://x.example"}
    assert (settings.release.ado, settings.release.github) == (
        {"organization": "o"},
        {"owner": "w"},
    )
    assert not any("replace a table" in m for m in _messages(caught, UserWarning))


@pytest.mark.parametrize("source", ["env_file", "secrets_dir"])
def test_direct_construction_legacy_value_from_a_file_input_outranks_new_prefix(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.0.0 read the ``FDT_`` value from ``_env_file`` or ``_secrets_dir``
    and never read ``SIGANTRY_``, even from the process environment."""
    if source == "env_file":
        target = tmp_path / "settings.env"
        target.write_text("FDT_CORE__TENANT_ID=from-legacy-input\n", encoding="utf-8")
        kwargs: dict[str, Any] = {"_env_file": target}
    else:
        target = tmp_path / "sd"
        target.mkdir()
        (target / "fdt_core").write_text(
            json.dumps({"tenant_id": "from-legacy-input"}), encoding="utf-8"
        )
        kwargs = {"_secrets_dir": target}
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "from-process-new-prefix")

    settings, caught = _construct(**kwargs)

    assert settings.core.tenant_id == "from-legacy-input"
    assert any("SIGANTRY_CORE__TENANT_ID" in m for m in _messages(caught, UserWarning))


def test_load_settings_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings merges env itself; the model's env source must stay out."""
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "fdt")

    _, caught = _load()

    assert len(_messages(caught, DeprecationWarning)) == 1, [str(w.message) for w in caught]


def test_pydantic_settings_still_exposes_the_inputs_the_env_source_reads(
    tmp_path: Path,
) -> None:
    """The direct env source reads these four attributes with ``getattr`` and a
    default, so a rename in pydantic-settings would drop an input silently.
    This fails instead."""
    env_file = tmp_path / "x.env"
    env_file.write_text("FDT_CORE__TENANT_ID=v\n", encoding="utf-8")
    seen: dict[str, Any] = {}

    class Probe(BaseSettings):
        model_config = SettingsConfigDict(extra="allow")

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            seen["init_kwargs"] = dict(init_settings.init_kwargs)  # type: ignore[attr-defined]
            seen["env_prefix"] = env_settings.env_prefix  # type: ignore[attr-defined]
            seen["env_vars"] = dict(dotenv_settings.env_vars)  # type: ignore[attr-defined]
            seen["secrets_dir"] = file_secret_settings.secrets_dir  # type: ignore[attr-defined]
            return (init_settings,)

    Probe(_env_file=env_file, _env_prefix="MY_", _secrets_dir=tmp_path, a="1")

    assert seen == {
        "init_kwargs": {"a": "1"},
        "env_prefix": "MY_",
        "env_vars": {"fdt_core__tenant_id": "v"},
        "secrets_dir": tmp_path,
    }


# ---------------------------------------------------------------------------
# 1.6 Settings keys match their field whatever their letter case
# ---------------------------------------------------------------------------


def test_section_keys_match_declared_fields_whatever_their_case() -> None:
    _write(
        _LEGACY_CONFIG_FILENAME,
        '[core]\nTENANT_ID = "upper"\n[workflow]\nPreview_Apis_Acknowledged = true\n'
        '[auth]\nPROVIDER = "pp"\n',
    )

    settings, caught = _load()

    assert settings.core.tenant_id == "upper"
    assert settings.workflow.preview_apis_acknowledged is True
    assert settings.auth.provider == "pp"
    assert settings.core.model_extra == {}
    case = [m for m in _messages(caught, DeprecationWarning) if "case is ignored" in m]
    assert any("[core] TENANT_ID" in m for m in case), case


@pytest.mark.parametrize(
    ("section", "body", "expected"),
    [
        ("core", 'TENANT_ID = "variant"\ntenant_id = "exact"\n', "variant"),
        ("core", 'tenant_id = "exact"\nTENANT_ID = "variant"\n', "exact"),
        ("core", 'TENANT_ID = "first"\nTenant_Id = "second"\n', "first"),
        ("workflow", "PREVIEW_APIS_ACKNOWLEDGED = true\npreview_apis_acknowledged = false\n", True),
    ],
    ids=["mixed-first", "exact-first", "two-variants", "bool-mixed-first"],
)
def test_two_spellings_keep_the_first_as_1_0_0_did(
    section: str, body: str, expected: object
) -> None:
    """1.0.0 took the first spelling of a field in the section, whichever it was."""
    _write(_LEGACY_CONFIG_FILENAME, f"[{section}]\n" + body)

    settings, _ = _load()

    model = getattr(settings, section)
    field = "tenant_id" if section == "core" else "preview_apis_acknowledged"
    assert getattr(model, field) == expected
    assert model.model_extra == {}


def test_constructor_values_keep_the_first_spelling_too() -> None:
    """``ToolkitSettings(core={...})`` resolved two spellings the same way in 1.0.0."""
    settings, caught = _construct(core={"TENANT_ID": "variant", "tenant_id": "exact"})

    assert settings.core.tenant_id == "variant"
    case = [m for m in _messages(caught, DeprecationWarning) if "case is ignored" in m]
    assert len(case) == 1 and "[core] TENANT_ID" in case[0], case


def test_legacy_env_override_beats_mixed_case_toml_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _write(_LEGACY_CONFIG_FILENAME, '[core]\nTENANT_ID = "upper"\n')
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "from-env")

    settings, _ = _load()

    assert settings.core.tenant_id == "from-env"


def test_new_env_override_beats_mixed_case_toml_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _write(_CONFIG_FILENAME, '[core]\nTENANT_ID = "upper"\n')
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "from-env")

    settings, _ = _load()

    assert settings.core.tenant_id == "from-env"


def test_mixed_case_key_in_legacy_file_beats_sigantry_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file's ``TENANT_ID`` is the setting 1.0.0 read; a lower layer's
    ``tenant_id`` must not win just by being spelled exactly."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\nTENANT_ID = "upper"\n')
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "sig")

    settings, _ = _load()

    assert settings.core.tenant_id == "upper"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"SIGANTRY_CORE__REGION": "uk"},
        {"SIGANTRY_AUTH__PROVIDER": "p"},
        {"FDT_CORE__REGION": "uk"},
    ],
    ids=["no-env", "new-prefix-other-key", "new-prefix-other-section", "legacy-other-key"],
)
def test_two_spellings_resolve_the_same_way_whatever_else_is_set(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variable for another setting cannot change which spelling wins."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "exact"\nTENANT_ID = "variant"\n')
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    settings, _ = _load()

    assert settings.core.tenant_id == "exact"


@pytest.mark.parametrize("filename", [_LEGACY_CONFIG_FILENAME, _CONFIG_FILENAME])
def test_table_in_another_case_is_filled_by_new_prefix_not_replaced(
    filename: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[release.ADO]`` is ``release.ado``. A ``SIGANTRY_`` key it leaves
    unset fills it; nothing the table sets is lost, and nothing is warned."""
    _write(filename, '[release.ADO]\norganization = "f-org"\n')
    monkeypatch.setenv("SIGANTRY_RELEASE__ADO__PROJECT", "p")

    settings, caught = _load()

    assert settings.release.ado == {"organization": "f-org", "project": "p"}
    assert _messages(caught, UserWarning) == []


def test_unknown_keys_keep_their_case() -> None:
    """Guard: only declared fields fold; plugin tables keep their spelling."""
    _write(
        _LEGACY_CONFIG_FILENAME,
        '[telemetry]\nSink_Name = "kept"\n[telemetry.log_analytics]\nWorkspace_Id = "w"\n',
    )

    settings, _ = _load()

    assert settings.telemetry.model_extra == {
        "Sink_Name": "kept",
        "log_analytics": {"Workspace_Id": "w"},
    }


# ---------------------------------------------------------------------------
# Deprecation warnings point at the caller, through from_config() too
# ---------------------------------------------------------------------------


def test_deprecation_is_attributed_to_the_caller_through_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python shows a DeprecationWarning by default only when it is attributed
    to ``__main__``; one attributed to sigantry's own module never shows."""
    from sigantry_core.api import FabricDataOps

    _write(_LEGACY_CONFIG_FILENAME, "[workflow]\nPreview_Apis_Acknowledged = true\n")
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "t1")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        FabricDataOps.from_config()

    settings_deprecations = [
        w
        for w in caught
        if w.category is DeprecationWarning
        and any(
            marker in str(w.message)
            for marker in ("FDT_", _LEGACY_CONFIG_FILENAME, "case is ignored")
        )
    ]
    # The legacy file, the FDT_ variable and the Preview_Apis_Acknowledged spelling.
    assert len(settings_deprecations) == 3, [str(w.message) for w in caught]
    assert {Path(w.filename).name for w in settings_deprecations} == {Path(__file__).name}


# ---------------------------------------------------------------------------
# 1.8 diagnose-auth records and drops the settings warnings, as sync does
# ---------------------------------------------------------------------------


def test_diagnose_auth_settings_load_prints_no_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1.0.0 printed nothing here; a stderr line fails a step set to fail on it."""
    from sigantry_core.auth.cli import _resolve_expected_group

    monkeypatch.setenv("TENANT_ID", "exported-for-another-tool")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _resolve_expected_group(None) == (None, None)

    assert [str(w.message) for w in caught] == []
    assert capsys.readouterr().err == ""


def test_diagnose_auth_settings_load_survives_warnings_as_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PYTHONWARNINGS=error``: a settings warning may not stop the command."""
    from sigantry_core.auth.cli import _resolve_expected_group

    monkeypatch.setenv("TENANT_ID", "exported-for-another-tool")
    _write(_LEGACY_CONFIG_FILENAME, '[auth]\nexpected_group = "g"\n')

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _resolve_expected_group(None) == ("g", None)


# ---------------------------------------------------------------------------
# 1.9 The shipped pytest fixture under -W error::DeprecationWarning
# ---------------------------------------------------------------------------


def test_fixture_still_returns_the_legacy_path(fdt_settings_toml: Any) -> None:
    path = fdt_settings_toml(core={"tenant_id": "t1"})

    assert path.name == _LEGACY_CONFIG_FILENAME
    twin = path.parent / _CONFIG_FILENAME
    assert twin.is_file() and twin.read_bytes() == path.read_bytes()


def test_fixture_stays_silent_after_the_test_edits_the_returned_file(
    fdt_settings_toml: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin test edits the file the fixture returned, then loads with
    warnings as errors. On 1.0.0 there was one file, and no warning."""
    from sigantry_core.api import FabricDataOps

    path = fdt_settings_toml(core={"tenant_id": "t1"})
    path.write_text(path.read_text(encoding="utf-8") + '[telemetry]\nnote = "x"\n', "utf-8")
    monkeypatch.chdir(path.parent)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ops = FabricDataOps.from_config()

    assert ops.settings.telemetry.model_extra == {"note": "x"}


def test_fixture_default_path_load_is_silent_under_error_filter(tmp_path: Path) -> None:
    """A plugin's test suite: fixture, chdir, ``from_config()``, with
    ``-W error::DeprecationWarning``. It passed on 1.0.0."""
    root = Path(sigantry_core.__file__).resolve().parent.parent
    case = tmp_path / "plugin-suite"
    case.mkdir()
    (case / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (case / "test_plugin_user.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            import sigantry_core


            def test_identity():
                here = Path(sigantry_core.__file__).resolve()
                assert here.is_relative_to({str(root)!r}), here


            def test_from_config_default_path(fdt_settings_toml, monkeypatch):
                from sigantry_core.api import FabricDataOps

                cfg = fdt_settings_toml(core={{"tenant_id": "t1"}})
                monkeypatch.chdir(cfg.parent)
                FabricDataOps.from_config()
            """
        ),
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("FDT_", "SIGANTRY_", "PYTEST_", "PYTHONWARNINGS"))
    }
    env["PYTHONPATH"] = str(root)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-W",
            "error::DeprecationWarning",
            "-c",
            str(case / "pytest.ini"),
            "--rootdir",
            str(case),
            str(case),
        ],
        cwd=case,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert "2 passed" in proc.stdout, proc.stdout[-3000:]


# ---------------------------------------------------------------------------
# S-1 Unprefixed names stay unbound, and are named in a FutureWarning
# ---------------------------------------------------------------------------


def test_bare_name_warns_and_does_not_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ID", "bare")
    monkeypatch.setenv("GATE", "bare")

    settings, caught = _load()

    assert (settings.core.tenant_id, settings.dq.gate, settings.approvals.gate) == (None,) * 3
    future = _messages(caught, FutureWarning)
    assert len(future) == 1, [str(w.message) for w in caught]
    assert "TENANT_ID -> SIGANTRY_CORE__TENANT_ID" in future[0]
    assert "GATE -> SIGANTRY_DQ__GATE or SIGANTRY_APPROVALS__GATE" in future[0]


def test_bare_name_no_warning_when_file_sets_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """1.0.0 ranked an unprefixed name below every other source."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\nTenant_Id = "from-file"\n')
    monkeypatch.setenv("TENANT_ID", "bare")
    monkeypatch.setenv("FDT_DQ__GATE", "from-env")
    monkeypatch.setenv("GATE", "bare")

    _, caught = _load()

    future = _messages(caught, FutureWarning)
    assert len(future) == 1, future
    assert "TENANT_ID" not in future[0]
    assert "GATE -> SIGANTRY_APPROVALS__GATE." in future[0]


def test_bare_dict_field_scalar_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GITHUB=x`` aimed at a table crashed 1.0.0; it was never a working setting."""
    monkeypatch.setenv("GITHUB", "x")
    monkeypatch.setenv("ADO", json.dumps({"organization": "o"}))
    monkeypatch.setenv("EXPECTED_GROUP", "added-after-1.0.0")

    _, caught = _load()

    future = _messages(caught, FutureWarning)
    assert len(future) == 1, future
    assert "ADO -> SIGANTRY_RELEASE__ADO" in future[0]
    assert "GITHUB" not in future[0] and "EXPECTED_GROUP" not in future[0]


def test_warning_never_contains_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name is reported as the environment lists it: ``store`` on POSIX,
    ``STORE`` on Windows, which folds names to upper case."""
    monkeypatch.setenv("TENANT_ID", "VALUE-tenant")
    monkeypatch.setenv("store", "VALUE-store")

    _, caught = _load()

    text = "\n".join(_messages(caught, FutureWarning))
    assert "TENANT_ID" in text and f"{_as_listed('store')} -> SIGANTRY_SECRETS__STORE" in text
    assert "VALUE-" not in text
