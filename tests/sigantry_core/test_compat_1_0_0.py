"""Settings sigantry 1.0.0 read keep the value they had there (1.0.1), except
for the changes CHANGELOG.md lists under "Upgrading from 1.0.0".

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
    _FIXTURE_FILES_ENV,
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


_TWO_TABLE_SPELLINGS = {
    "release.ado": '[release.ADO]\nworkspace = "upper"\n[release.ado]\nworkspace = "lower"\n',
    "runbooks.static_map": '[runbooks.STATIC_MAP]\na = "upper"\n[runbooks.static_map]\na = "lower"\n',
}


@_posix_only("Windows sets the exact-case FDT_ name, which made 1.0.0's load_settings() fail")
@pytest.mark.parametrize("value", ["", "[1]", "null", "5", '"s"', "true"])
@pytest.mark.parametrize("field", sorted(_TWO_TABLE_SPELLINGS))
def test_non_object_value_for_a_table_field_puts_its_spelling_first(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pydantic-settings kept a value that is not a JSON object at the field
    the name spells in lower case, so the file's table spelled that way took
    its place, ahead of the other spelling, and won. Without the name, the
    first spelling in the file wins."""
    section, key = field.split(".")
    _write(_LEGACY_CONFIG_FILENAME, _TWO_TABLE_SPELLINGS[field])
    monkeypatch.setenv(f"fdt_{section}__{key}", value)

    settings, caught = _load()

    assert getattr(getattr(settings, section), key) == {
        "workspace" if section == "release" else "a": "lower"
    }
    assert not any("replace a table" in m for m in _messages(caught, UserWarning))


def test_empty_table_field_in_an_env_file_puts_its_spelling_first(tmp_path: Path) -> None:
    """The same through ``ToolkitSettings(_env_file=...)``, with the tables
    passed to the constructor."""
    env_file = tmp_path / "settings.env"
    env_file.write_text("FDT_RELEASE__ADO=\n", encoding="utf-8")

    settings, _ = _construct(
        _env_file=env_file, release={"ADO": {"w": "upper"}, "ado": {"w": "lower"}}
    )

    assert settings.release.ado == {"w": "lower"}


@pytest.mark.parametrize("build", ["load", "construct"])
def test_null_section_drops_its_nested_names(build: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``fdt_<section>=null`` left the section out of the env source in 1.0.0,
    its ``__`` names with it."""
    if build == "load":
        if sys.platform == "win32":
            pytest.skip("Windows sets FDT_CORE, which load_settings() reads as a table")
        monkeypatch.setenv("fdt_core", "null")
        monkeypatch.setenv("fdt_core__tenant_id", "x")
        settings, caught = _load()
    else:
        monkeypatch.setenv("FDT_CORE", "null")
        monkeypatch.setenv("FDT_CORE__TENANT_ID", "x")
        settings, caught = _construct()

    assert settings.core.tenant_id is None
    assert not any("replace a table" in m for m in _messages(caught, UserWarning))


def test_null_section_leaves_a_lower_input_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FDT_CORE=null`` in the environment left the section out of that
    source in 1.0.0, so a name in an env file, which ranks below it, still
    set the section."""
    env_file = tmp_path / "settings.env"
    env_file.write_text("FDT_CORE__TENANT_ID=x\n", encoding="utf-8")
    monkeypatch.setenv("FDT_CORE", "null")

    settings, caught = _construct(_env_file=env_file)

    assert settings.core.tenant_id == "x"
    assert not any("replace a table" in m for m in _messages(caught, UserWarning))


def test_section_scalar_over_a_legacy_file_table_is_left_out_and_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1.0.0's ``load_settings()`` wrote ``FDT_CORE=5`` over the file's
    ``[core]`` table and failed validation. The table is kept instead, and
    the name reported."""
    _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "f"\n')
    monkeypatch.setenv("FDT_CORE", "5")

    settings, caught = _load()

    assert settings.core.tenant_id == "f"
    clobbered = [m for m in _messages(caught, UserWarning) if "replace a table" in m]
    assert len(clobbered) == 1
    assert "FDT_CORE" in clobbered[0], clobbered


@pytest.mark.parametrize("value", ["5", '"s"', "[1]", "true"])
@pytest.mark.parametrize("build", ["load", "construct"])
def test_non_object_section_value_drops_its_nested_names(
    build: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``fdt_<section>`` holding other JSON was the section's value in the env
    source, without its ``__`` names; the file's table then replaced it."""
    if build == "load":
        if sys.platform == "win32":
            pytest.skip("Windows sets FDT_CORE, which load_settings() reads as a table")
        _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "f"\n')
        monkeypatch.setenv("fdt_core", value)
        monkeypatch.setenv("fdt_core__region", "r")
        settings, caught = _load()
    else:
        monkeypatch.setenv("FDT_CORE", value)
        monkeypatch.setenv("FDT_CORE__REGION", "r")
        settings, caught = _construct(core={"tenant_id": "f"})

    assert settings.core.tenant_id == "f"
    assert settings.core.model_extra == {}
    assert not any("replace a table" in m for m in _messages(caught, UserWarning))


@pytest.mark.parametrize("case", ["json", "not-json", "secrets", "load-exact-case"])
def test_value_1_0_0_failed_on_is_left_out_and_named(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: 1.0.0 failed on each of these values (validation, or parsing
    the JSON); the value is left out instead, and named in a warning."""
    expected_names = ["FDT_CORE", "FDT_RELEASE__ADO"]
    if case == "json":
        monkeypatch.setenv("FDT_CORE", "[1]")
        monkeypatch.setenv("FDT_RELEASE__ADO", "5")
        settings, caught = _construct()
    elif case == "not-json":
        monkeypatch.setenv("FDT_CORE", "not-json")
        monkeypatch.setenv("FDT_RELEASE__ADO", "not-json")
        settings, caught = _construct()
    elif case == "secrets":
        secrets = tmp_path / "sd"
        secrets.mkdir()
        (secrets / "fdt_core").write_text("5", encoding="utf-8")
        (secrets / "fdt_release").write_text("not-json", encoding="utf-8")
        expected_names = ["fdt_core", "fdt_release"]
        settings, caught = _construct(_secrets_dir=secrets)
    else:
        _write(_LEGACY_CONFIG_FILENAME, '[release.ado]\norganization = "o"\n')
        monkeypatch.setenv("FDT_CORE", "5")
        monkeypatch.setenv("FDT_RELEASE__ADO", "5")
        settings, caught = _load()

    assert settings.core.tenant_id is None
    assert settings.release.ado == ({"organization": "o"} if case == "load-exact-case" else {})
    clobbered = [m for m in _messages(caught, UserWarning) if "replace a table" in m]
    assert len(clobbered) == 1
    assert all(name in clobbered[0] for name in expected_names), clobbered


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


@pytest.mark.parametrize("prefix", ["SIGANTRY_", "sigantry_"])
def test_direct_construction_reads_an_explicit_sigantry_prefix_as_1_0_0_did(
    prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_env_prefix="SIGANTRY_"`` is the model's own prefix, but 1.0.0 read
    every form under it, as under any prefix passed (``MY_`` above), and no
    ``FDT_`` name."""
    monkeypatch.setenv("SIGANTRY_CORE", json.dumps({"tenant_id": "j"}))
    monkeypatch.setenv("SIGANTRY_RELEASE__ADO", json.dumps({"organization": "o"}))
    monkeypatch.setenv("FDT_AUTH__PROVIDER", "not-read-under-a-prefix-passed")

    settings, caught = _construct(_env_prefix=prefix)

    assert settings.core.tenant_id == "j"
    assert settings.release.ado == {"organization": "o"}
    assert settings.auth.provider is None
    assert [str(w.message) for w in caught] == []


def test_explicit_sigantry_prefix_value_wins_over_fdt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_CORE__TENANT_ID", "a")
    monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "b")

    settings, _ = _construct(_env_prefix="SIGANTRY_")

    assert settings.core.tenant_id == "b"


@_posix_only("Windows cannot set a lower-case name")
def test_explicit_sigantry_prefix_matches_any_letter_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("sigantry_core__tenant_id", "low")

    settings, _ = _construct(_env_prefix="SIGANTRY_")

    assert settings.core.tenant_id == "low"


#: Names under ``FDT_`` and ``SIGANTRY_`` set together: 1.0.0 read the
#: ``SIGANTRY_`` ones, in every form, only under a prefix a subclass declared
#: or a call passed, and then no ``FDT_`` one.
_TWO_PREFIX_ENV = {
    "FDT_CORE__TENANT_ID": "fdt",
    "SIGANTRY_CORE__TENANT_ID": "sig",
    "sigantry_core__region": "lowr",
    "SIGANTRY_RELEASE__ADO": json.dumps({"organization": "o"}),
    "FDT_AUTH__PROVIDER": "fdtp",
}

#: Names under ``MY_`` beside the default prefixes.
_MY_PREFIX_ENV = {
    "MY_CORE__TENANT_ID": "my",
    "FDT_AUTH__PROVIDER": "fdtp",
    "SIGANTRY_DQ__GATE": "sigg",
}


def _set_all(monkeypatch: pytest.MonkeyPatch, names: dict[str, str]) -> None:
    for name, value in names.items():
        monkeypatch.setenv(name, value)


def _declaring(prefix: str) -> type[ToolkitSettings]:
    """A subclass that declares ``env_prefix`` in its own ``model_config``."""

    class Declaring(ToolkitSettings):
        model_config = SettingsConfigDict(env_prefix=prefix)

    return Declaring


def _build(
    cls: type[ToolkitSettings], **kwargs: Any
) -> tuple[ToolkitSettings, list[warnings.WarningMessage]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = cls(**kwargs)
    return settings, caught


def _assert_read_sigantry_prefix_only(settings: ToolkitSettings) -> None:
    assert settings.core.tenant_id == "sig"
    assert settings.core.model_extra == {"region": "lowr"}
    assert settings.release.ado == {"organization": "o"}
    assert settings.auth.provider is None


@pytest.mark.parametrize("prefix", ["SIGANTRY_", "sigantry_", "Sigantry_"])
def test_subclass_reads_its_own_sigantry_prefix_as_1_0_0_did(
    prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subclass that declares ``env_prefix="SIGANTRY_"``, in any letter
    case, read every form under it in 1.0.0 and no ``FDT_`` name, as under a
    prefix passed to the constructor, although it spells the default."""
    _set_all(monkeypatch, _TWO_PREFIX_ENV)

    settings, caught = _build(_declaring(prefix))

    _assert_read_sigantry_prefix_only(settings)
    assert [str(w.message) for w in caught] == []


@pytest.mark.parametrize("own_config", [False, True])
def test_grandchild_reads_the_prefix_its_parent_declared(
    own_config: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A class below one that declares ``env_prefix`` inherits that prefix,
    with or without a ``model_config`` of its own that leaves it out."""
    _set_all(monkeypatch, _TWO_PREFIX_ENV)
    parent = _declaring("SIGANTRY_")
    if own_config:

        class GrandChild(parent):  # type: ignore[valid-type,misc]
            model_config = SettingsConfigDict(case_sensitive=False)

    else:

        class GrandChild(parent):  # type: ignore[valid-type,misc,no-redef]
            pass

    settings, caught = _build(GrandChild)

    _assert_read_sigantry_prefix_only(settings)
    assert [str(w.message) for w in caught] == []


def test_subclass_reads_its_own_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prefix a subclass declares is read whatever the call passes, and
    replaces both default prefixes."""
    _set_all(monkeypatch, _MY_PREFIX_ENV)

    settings, caught = _build(_declaring("MY_"))

    assert (settings.core.tenant_id, settings.auth.provider, settings.dq.gate) == (
        "my",
        None,
        None,
    )
    assert [str(w.message) for w in caught] == []


@pytest.mark.parametrize("own_config", [False, True])
def test_subclass_without_its_own_prefix_reads_what_toolkit_settings_reads(
    own_config: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: a subclass that declares no ``env_prefix``, and a class below
    it, inherit the default prefixes, as 1.0.0's inherited ``FDT_``."""
    _set_all(monkeypatch, _TWO_PREFIX_ENV)
    if own_config:

        class Plain(ToolkitSettings):
            model_config = SettingsConfigDict(case_sensitive=False)

    else:

        class Plain(ToolkitSettings):  # type: ignore[no-redef]
            pass

    class GrandPlain(Plain):
        pass

    expected, _ = _construct()

    assert (expected.core.tenant_id, expected.auth.provider) == ("fdt", "fdtp")
    for cls in (Plain, GrandPlain):
        settings, caught = _build(cls)
        assert settings.model_dump() == expected.model_dump()
        assert any("FDT_CORE__TENANT_ID" in m for m in _messages(caught, DeprecationWarning))


@pytest.mark.parametrize("prefix", ["SIGANTRY_", "MY_"])
def test_prefix_passed_to_a_subclass_without_its_own(
    prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_env_prefix`` on a subclass that declares none reads as on
    ``ToolkitSettings`` itself."""

    class Plain(ToolkitSettings):
        pass

    if prefix == "SIGANTRY_":
        _set_all(monkeypatch, _TWO_PREFIX_ENV)
        settings, caught = _build(Plain, _env_prefix=prefix)
        _assert_read_sigantry_prefix_only(settings)
    else:
        _set_all(monkeypatch, _MY_PREFIX_ENV)
        settings, caught = _build(Plain, _env_prefix=prefix)
        assert (settings.core.tenant_id, settings.auth.provider, settings.dq.gate) == (
            "my",
            None,
            None,
        )
    assert [str(w.message) for w in caught] == []


def test_pydantic_settings_hands_the_env_source_the_declared_prefix_object() -> None:
    """The default prefix is told from a declared one by its type, so the
    object ``model_config`` holds must reach the env source unchanged. A
    pydantic-settings release that turned it into a plain string would make
    every construction read as if ``SIGANTRY_`` had been declared; this
    fails instead."""
    seen: list[Any] = []

    class Probe(ToolkitSettings):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            seen.append(env_settings.env_prefix)  # type: ignore[attr-defined]
            return super().settings_customise_sources(
                settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
            )

    Probe()

    declared = ToolkitSettings.model_config["env_prefix"]
    assert type(declared) is not str
    assert seen == [declared]
    assert type(seen[0]) is type(declared)


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


def _declaring_empty_below() -> type[ToolkitSettings]:
    class Below(_declaring("")):  # type: ignore[misc]
        pass

    return Below


@pytest.mark.parametrize(
    ("make", "kwargs", "source"),
    [
        (lambda: ToolkitSettings, {"_env_prefix": ""}, 'ToolkitSettings(_env_prefix="")'),
        (lambda: _declaring(""), {}, 'env_prefix="" in the model_config of Declaring'),
        (_declaring_empty_below, {}, 'env_prefix="" in the model_config of Below'),
        (lambda: _declaring("MY_"), {"_env_prefix": ""}, 'Declaring(_env_prefix="")'),
    ],
    ids=["passed", "declared", "declared-above", "passed-over-declared"],
)
def test_direct_construction_refuses_an_empty_prefix(
    make: Any, kwargs: dict[str, Any], source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty prefix would read unprefixed names, so the default ones are
    read; the warning names the model_config that holds it, or else the call."""
    monkeypatch.setenv("TENANT_ID", "bare")
    monkeypatch.setenv("SIGANTRY_AUTH__PROVIDER", "sig")

    settings, caught = _build(make(), **kwargs)

    assert (settings.core.tenant_id, settings.auth.provider) == (None, "sig")
    assert [m.split(" is not honoured:")[0] for m in _messages(caught, UserWarning)] == [source]


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
# 1.9 The shipped pytest fixture: one file, read without a warning
# ---------------------------------------------------------------------------
#
# sigantry 1.0.0's fixture wrote the legacy file alone, and its loader read
# that file without a warning, so a plugin suite run with warnings as errors
# passed. The fixture still writes that one file; the loader reads it without
# a warning while the test runs. Each file operation below behaves as on
# 1.0.0.

_NEW_FILE_BODY = '[core]\ntenant_id = "from-new-file"\n'


def _load_with_warnings_as_errors() -> ToolkitSettings:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return load_settings()


def test_fixture_writes_the_legacy_file_alone(fdt_settings_toml: Any) -> None:
    path = fdt_settings_toml(core={"tenant_id": "t1"})

    assert path.name == _LEGACY_CONFIG_FILENAME
    assert [entry.name for entry in path.parent.iterdir()] == [_LEGACY_CONFIG_FILENAME]
    assert _load_with_warnings_as_errors().core.tenant_id == "t1"


def test_fixture_keeps_a_new_file_the_test_wrote_first(fdt_settings_toml: Any) -> None:
    """1.0.0 left a ``.sigantry.toml`` alone, and read the legacy file."""
    new_file = _write(_CONFIG_FILENAME, _NEW_FILE_BODY)

    fdt_settings_toml(core={"tenant_id": "t1"})

    assert new_file.read_text(encoding="utf-8") == _NEW_FILE_BODY
    assert _load_with_warnings_as_errors().core.tenant_id == "t1"


def test_writing_a_new_file_leaves_the_returned_file_alone(fdt_settings_toml: Any) -> None:
    path = fdt_settings_toml(core={"tenant_id": "t1"})
    before = path.read_bytes()

    _write(_CONFIG_FILENAME, _NEW_FILE_BODY)

    assert path.read_bytes() == before
    assert _load_with_warnings_as_errors().core.tenant_id == "t1"


def test_renaming_the_returned_file_moves_it(fdt_settings_toml: Any) -> None:
    """The rename moves the file, as on 1.0.0. The moved file is then read
    because ``.sigantry.toml`` alone is read now, which 1.0.0 did not do."""
    path = fdt_settings_toml(core={"tenant_id": "t1"})

    path.rename(path.with_name(_CONFIG_FILENAME))

    assert not path.exists()
    assert [entry.name for entry in path.parent.iterdir()] == [_CONFIG_FILENAME]
    assert _load_with_warnings_as_errors().core.tenant_id == "t1"


def test_deleting_the_returned_file_leaves_no_config(fdt_settings_toml: Any) -> None:
    """1.0.0 read no file after this, and every setting kept its default."""
    path = fdt_settings_toml(core={"tenant_id": "t1"})

    path.unlink()

    assert list(path.parent.iterdir()) == []
    assert _load_with_warnings_as_errors().core.tenant_id is None


def test_replacing_the_returned_file_stays_silent(fdt_settings_toml: Any) -> None:
    """An edit that writes a new file and moves it over the returned one."""
    path = fdt_settings_toml(core={"tenant_id": "t1"})
    staged = path.with_name("staged.toml")
    staged.write_text('[core]\ntenant_id = "edited"\n', encoding="utf-8")

    os.replace(staged, path)

    assert _load_with_warnings_as_errors().core.tenant_id == "edited"


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


def test_fixture_exemption_covers_its_own_file_only(
    fdt_settings_toml: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: a legacy file the fixture did not write still warns."""
    fdt_settings_toml(core={"tenant_id": "t1"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / _LEGACY_CONFIG_FILENAME).write_text('[core]\ntenant_id = "o"\n', "utf-8")
    monkeypatch.chdir(elsewhere)

    settings, caught = _load()

    assert settings.core.tenant_id == "o"
    assert any(
        f"{_LEGACY_CONFIG_FILENAME} is deprecated" in m
        for m in _messages(caught, DeprecationWarning)
    )


def test_fixture_stays_silent_when_the_test_clears_its_environment(
    fdt_settings_toml: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The variable is for the processes a test starts; in the test's own
    process the exemption holds without it."""
    fdt_settings_toml(core={"tenant_id": "t1"})
    monkeypatch.delenv(_FIXTURE_FILES_ENV)

    assert _load_with_warnings_as_errors().core.tenant_id == "t1"


@pytest.mark.parametrize(
    ("listed", "exempt"),
    [
        (lambda here: json.dumps([os.path.normcase(os.path.realpath(here))]), True),
        (lambda here: json.dumps([os.path.normcase(os.path.realpath(here)) + "x"]), False),
        (lambda here: os.path.realpath(here), False),
        (lambda here: json.dumps({"path": os.path.realpath(here)}), False),
        (lambda here: json.dumps([1, ["x"]]), False),
        (lambda here: "", False),
    ],
    ids=["this-path", "another-path", "not-json", "not-a-list", "not-strings", "empty"],
)
def test_fixture_exemption_through_the_environment(
    listed: Any, exempt: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a process the test started sees: no fixture of its own, only the
    variable. Only a JSON list naming this file's path exempts it."""
    here = _write(_LEGACY_CONFIG_FILENAME, '[core]\ntenant_id = "t1"\n')
    monkeypatch.setenv(_FIXTURE_FILES_ENV, listed(here))

    settings, caught = _load()

    assert settings.core.tenant_id == "t1"
    warned = any(
        f"{_LEGACY_CONFIG_FILENAME} is deprecated" in m
        for m in _messages(caught, DeprecationWarning)
    )
    assert warned is not exempt


#: A plugin's tests, run by :func:`test_fixture_file_operations_in_a_plugin_suite`
#: in a pytest of their own. Each asserts what 1.0.0 did, and loads through
#: ``from_config()`` under the run's warnings-as-errors filter.
_PLUGIN_SUITE_OPERATIONS = """
import os
import subprocess
import sys

NEW = ".sigantry.toml"
BODY = '[core]\\ntenant_id = "from-new-file"\\n'
CHILD = (
    "from sigantry_core.api import FabricDataOps; "
    "print(FabricDataOps.from_config().settings.core.tenant_id)"
)


def _from_config(cfg, monkeypatch):
    from sigantry_core.api import FabricDataOps

    monkeypatch.chdir(cfg.parent)
    return FabricDataOps.from_config().settings


def _child(cfg, *flags):
    proc = subprocess.run(
        [sys.executable, *flags, "-c", CHILD],
        cwd=cfg.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr


def test_writes_one_file(fdt_settings_toml, monkeypatch):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    assert [p.name for p in cfg.parent.iterdir()] == [cfg.name]
    assert _from_config(cfg, monkeypatch).core.tenant_id == "t1"


def test_new_file_written_first_is_kept(fdt_settings_toml, tmp_path, monkeypatch):
    (tmp_path / NEW).write_text(BODY, encoding="utf-8")
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    assert (tmp_path / NEW).read_text(encoding="utf-8") == BODY
    assert _from_config(cfg, monkeypatch).core.tenant_id == "t1"


def test_new_file_written_after_leaves_the_returned_file(fdt_settings_toml, monkeypatch):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    before = cfg.read_bytes()
    (cfg.parent / NEW).write_text(BODY, encoding="utf-8")
    assert cfg.read_bytes() == before
    assert _from_config(cfg, monkeypatch).core.tenant_id == "t1"


def test_rename_moves_the_file(fdt_settings_toml, monkeypatch):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    cfg.rename(cfg.with_name(NEW))
    assert not cfg.exists()
    _from_config(cfg, monkeypatch)


def test_delete_leaves_no_config(fdt_settings_toml, monkeypatch):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    cfg.unlink()
    assert list(cfg.parent.iterdir()) == []
    assert _from_config(cfg, monkeypatch).core.tenant_id is None


def test_replace_edit(fdt_settings_toml, monkeypatch):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    staged = cfg.with_name("staged.toml")
    staged.write_text('[core]\\ntenant_id = "edited"\\n', encoding="utf-8")
    os.replace(staged, cfg)
    assert _from_config(cfg, monkeypatch).core.tenant_id == "edited"


def test_in_place_edit(fdt_settings_toml, monkeypatch):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    cfg.write_text('[core]\\ntenant_id = "edited"\\n', encoding="utf-8")
    assert _from_config(cfg, monkeypatch).core.tenant_id == "edited"


def test_child_process_with_warnings_as_errors(fdt_settings_toml):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    assert _child(cfg, "-W", "error::DeprecationWarning") == (0, "t1", "")


def test_child_process_with_default_filters(fdt_settings_toml):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    assert _child(cfg) == (0, "t1", "")


def test_child_process_with_a_new_file_beside(fdt_settings_toml):
    cfg = fdt_settings_toml(core={"tenant_id": "t1"})
    (cfg.parent / NEW).write_text(BODY, encoding="utf-8")
    assert _child(cfg, "-W", "error") == (0, "t1", "")
"""

#: The exemption ends with the test that asked for the file: the same path
#: warns again in a later test. Not a 1.0.0 behaviour (1.0.0 never warned).
_PLUGIN_SUITE_SCOPE = """
import pytest

_SEEN = []


def test_a_fixture_file(fdt_settings_toml):
    _SEEN.append(fdt_settings_toml())


def test_b_same_path_warns_once_that_test_ended(monkeypatch):
    from sigantry_core.config import load_settings

    path = _SEEN[0]
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(path.parent)
    with pytest.warns(DeprecationWarning, match="is deprecated"):
        load_settings()


def test_c_a_child_started_once_that_test_ended_warns():
    import subprocess
    import sys

    path = _SEEN[0]
    proc = subprocess.run(
        [sys.executable, "-c", "from sigantry_core.config import load_settings; load_settings()"],
        cwd=path.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "is deprecated" in proc.stderr, proc.stderr
"""


@pytest.mark.parametrize(
    ("warnings_as_errors", "pythonwarnings"),
    [
        (["-W", "error::DeprecationWarning"], None),
        (["-o", "filterwarnings=error"], None),
        ([], "error::DeprecationWarning"),
    ],
    ids=["W-error-DeprecationWarning", "filterwarnings-error", "PYTHONWARNINGS-error"],
)
def test_fixture_file_operations_in_a_plugin_suite(
    warnings_as_errors: list[str], pythonwarnings: str | None, tmp_path: Path
) -> None:
    """A plugin's own suite: the fixture, each file operation, ``from_config()``,
    with warnings as errors, in the test's process and in one it starts. Every
    test in the operations file passed on 1.0.0."""
    root = Path(sigantry_core.__file__).resolve().parent.parent
    case = tmp_path / "plugin-suite"
    case.mkdir()
    (case / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (case / "test_identity.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            import sigantry_core


            def test_identity():
                here = Path(sigantry_core.__file__).resolve()
                assert here.is_relative_to({str(root)!r}), here
            """
        ),
        encoding="utf-8",
    )
    (case / "test_operations.py").write_text(_PLUGIN_SUITE_OPERATIONS, encoding="utf-8")
    (case / "test_scope.py").write_text(_PLUGIN_SUITE_SCOPE, encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("FDT_", "SIGANTRY_", "PYTEST_", "PYTHONWARNINGS"))
    }
    env["PYTHONPATH"] = str(root)
    if pythonwarnings is not None:
        env["PYTHONWARNINGS"] = pythonwarnings

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            *warnings_as_errors,
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
    assert "14 passed" in proc.stdout, proc.stdout[-3000:]


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
