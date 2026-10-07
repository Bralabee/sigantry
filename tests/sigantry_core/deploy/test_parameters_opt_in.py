"""Raw-GUID opt-in, ``parameter.yml`` spelling and per-environment scoping.

Persona-swarm item 4c: a stock fabric-cicd ``parameter.yml`` carries raw
GUIDs by design and was refused outright, and validating demanded every
environment's ``$ENV:`` variables in every job. Each test here fails on
the module before this change.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.deploy.parameters import (
    HardcodedGuidError,
    UnknownEnvironmentError,
    load_and_validate,
    resolve_allow_raw_guids,
    resolve_parameters_path,
    substitute_env_references,
    write_substituted_parameters,
)

_GUID = "11111111-2222-3333-4444-555555555555"

_STOCK = f"""
find_replace:
  - find_value: "placeholder-workspace-id"
    item_type: Notebook
    replace_value:
      DEV: "{_GUID}"
"""

_TWO_ENVS = """
find_replace:
  - find_value: "ws"
    replace_value:
      DEV: "$ENV:ONLY_DEV_VAR"
      PROD: "$ENV:ONLY_PROD_VAR"
  - find_value: "prod-only"
    replace_value:
      PROD: "$ENV:ONLY_PROD_VAR"
key_value_replace:
  - find_key: "$.x"
    replace_value:
      _ALL_: "$ENV:SHARED_VAR"
semantic_model_binding:
  default:
    connection_id:
      DEV: "$ENV:ONLY_DEV_VAR"
      PROD: "$ENV:ONLY_PROD_VAR"
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ONLY_DEV_VAR", "ONLY_PROD_VAR", "SHARED_VAR", "SIGANTRY_DEPLOY__ALLOW_RAW_GUIDS"):
        monkeypatch.delenv(var, raising=False)


def _write(tmp_path: Path, text: str, name: str = "parameters.yml") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# spelling
# ---------------------------------------------------------------------------


def test_resolve_accepts_the_fabric_cicd_spelling(tmp_path: Path) -> None:
    singular = _write(tmp_path, _STOCK, name="parameter.yml")
    assert resolve_parameters_path(tmp_path / "parameters.yml") == singular
    assert resolve_parameters_path(tmp_path / "parameter.yml") == singular
    assert resolve_parameters_path(tmp_path) == singular


def test_resolve_prefers_the_named_file_when_both_exist(tmp_path: Path) -> None:
    plural = _write(tmp_path, _STOCK)
    singular = _write(tmp_path, _STOCK, name="parameter.yml")
    assert resolve_parameters_path(plural) == plural
    assert resolve_parameters_path(singular) == singular
    assert resolve_parameters_path(tmp_path) == plural  # docs spelling first


def test_resolve_names_every_spelling_it_tried(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"parameters\.yml.*parameter\.yml"):
        resolve_parameters_path(tmp_path / "parameters.yml")


def test_load_reads_the_other_spelling(tmp_path: Path) -> None:
    singular = _write(tmp_path, _STOCK, name="parameter.yml")
    cfg = load_and_validate(tmp_path / "parameters.yml", allow_raw_guids=True)
    assert cfg.path == str(singular)


# ---------------------------------------------------------------------------
# raw GUIDs
# ---------------------------------------------------------------------------


def test_raw_guid_is_refused_by_default_and_the_message_names_the_opt_in(tmp_path: Path) -> None:
    p = _write(tmp_path, _STOCK)
    with pytest.raises(HardcodedGuidError, match="--allow-raw-guids"):
        load_and_validate(p)


def test_raw_guid_is_allowed_listed_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = _write(tmp_path, _STOCK)
    with caplog.at_level(logging.WARNING, logger="sigantry_core.deploy.parameters"):
        cfg = load_and_validate(p, allow_raw_guids=True)
    assert cfg.raw_guids == ("find_replace.[0].replace_value.DEV",)
    assert cfg.environments_seen == frozenset({"DEV"})
    assert any(_GUID in r.getMessage() and "allowed" in r.getMessage() for r in caplog.records)


def test_reference_forms_are_not_counted_as_raw(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        'find_replace:\n  - find_value: x\n    replace_value:\n      DEV: "$workspace.$id"\n',
    )
    assert load_and_validate(p, allow_raw_guids=True).raw_guids == ()


def test_resolve_allow_raw_guids_flag_wins() -> None:
    assert resolve_allow_raw_guids(True) is True


def test_resolve_allow_raw_guids_reads_the_settings_file(tmp_path: Path) -> None:
    """The documented ``[deploy] allow_raw_guids`` key is read, not only the env var."""
    toml = tmp_path / ".sigantry.toml"
    toml.write_text("[deploy]\nallow_raw_guids = true\n", encoding="utf-8")
    assert resolve_allow_raw_guids(None, settings_path=toml) is True
    toml.write_text("[deploy]\nallow_raw_guids = false\n", encoding="utf-8")
    assert resolve_allow_raw_guids(None, settings_path=toml) is False


def test_resolve_allow_raw_guids_reads_the_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml = tmp_path / ".sigantry.toml"
    toml.write_text("[deploy]\n", encoding="utf-8")
    monkeypatch.setenv("SIGANTRY_DEPLOY__ALLOW_RAW_GUIDS", "true")
    assert resolve_allow_raw_guids(None, settings_path=toml) is True


def test_resolve_allow_raw_guids_defaults_off_when_settings_are_broken(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    toml = tmp_path / ".sigantry.toml"
    toml.write_text("[deploy\nallow_raw_guids = true\n", encoding="utf-8")  # not TOML
    with caplog.at_level(logging.WARNING, logger="sigantry_core.deploy.parameters"):
        assert resolve_allow_raw_guids(None, settings_path=toml) is False
    assert any("settings could not be loaded" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# environment scoping
# ---------------------------------------------------------------------------


def test_only_the_target_environment_must_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write(tmp_path, _TWO_ENVS)
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    monkeypatch.setenv("SHARED_VAR", "shared")
    cfg = load_and_validate(p, environment="DEV")
    assert cfg.environments_seen == frozenset({"DEV", "PROD"})
    with pytest.raises(RuntimeError, match="ONLY_PROD_VAR"):
        load_and_validate(p, environment="PROD")
    with pytest.raises(RuntimeError, match="ONLY_PROD_VAR"):
        load_and_validate(p)  # no target: every slot must resolve, as before


def test_all_wildcard_slots_are_checked_for_every_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write(tmp_path, _TWO_ENVS)
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    with pytest.raises(RuntimeError, match="SHARED_VAR"):
        load_and_validate(p, environment="DEV")


def test_slots_outside_the_environment_maps_are_always_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write(
        tmp_path,
        'find_replace:\n  - find_value: "$ENV:TOP_LEVEL_VAR"\n    replace_value:\n      DEV: "x"\n',
    )
    monkeypatch.delenv("TOP_LEVEL_VAR", raising=False)
    with pytest.raises(RuntimeError, match="TOP_LEVEL_VAR"):
        load_and_validate(p, environment="DEV")


def test_undeclared_environment_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write(tmp_path, _TWO_ENVS)
    with pytest.raises(UnknownEnvironmentError, match=r"PRDO.*declared: DEV, PROD"):
        load_and_validate(p, environment="PRDO")


def test_undeclared_environment_is_fine_with_a_wildcard_or_an_empty_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHARED_VAR", "shared")
    p = _write(
        tmp_path, 'key_value_replace:\n  - find_key: "$.x"\n    replace_value:\n      _ALL_: "v"\n'
    )
    assert load_and_validate(p, environment="ANYTHING").environments_seen == frozenset()
    empty = _write(tmp_path, "", name="parameter.yml")
    assert load_and_validate(empty, environment="ANYTHING").raw == {}


def test_substitution_keeps_only_the_target_and_wildcard_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing another environment owns, resolved or not, reaches fabric-cicd."""
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    monkeypatch.setenv("SHARED_VAR", "shared")
    doc = yaml.safe_load(_TWO_ENVS)
    out = substitute_env_references(doc, environment="DEV")
    assert out["find_replace"] == [{"find_value": "ws", "replace_value": {"DEV": "dev"}}]
    assert out["key_value_replace"] == [{"find_key": "$.x", "replace_value": {"_ALL_": "shared"}}]
    assert out["semantic_model_binding"] == {"default": {"connection_id": {"DEV": "dev"}}}
    assert "$ENV:" not in yaml.safe_dump(out)
    assert (
        doc["find_replace"][0]["replace_value"]["PROD"] == "$ENV:ONLY_PROD_VAR"
    )  # input untouched


def test_substitution_without_a_target_expands_every_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    monkeypatch.setenv("ONLY_PROD_VAR", "prod")
    monkeypatch.setenv("SHARED_VAR", "shared")
    out = substitute_env_references(yaml.safe_load(_TWO_ENVS))
    assert out["find_replace"][0]["replace_value"] == {"DEV": "dev", "PROD": "prod"}
    assert len(out["find_replace"]) == 2


def test_substitution_drops_a_connection_with_no_slot_for_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONLY_PROD_VAR", "prod")
    doc = {"semantic_model_binding": {"default": {"connection_id": {"PROD": "$ENV:ONLY_PROD_VAR"}}}}
    # fabric-cicd 1.3.0 refuses ``default: {}`` (``connection_id`` is required), so a
    # binding with no slot for the target is dropped whole, not left empty.
    assert substitute_env_references(doc, environment="DEV") == {}


def test_write_substituted_parameters_is_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    monkeypatch.setenv("SHARED_VAR", "shared")
    cfg = load_and_validate(_write(tmp_path, _TWO_ENVS), environment="DEV")
    target = write_substituted_parameters(
        cfg, tmp_path / "out" / "parameters.yml", environment="DEV"
    )
    text = target.read_text(encoding="utf-8")
    assert "PROD" not in text and "$ENV:" not in text and "dev" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_config_validate_refuses_then_allows_a_stock_file(tmp_path: Path) -> None:
    p = _write(tmp_path, _STOCK, name="parameter.yml")
    runner = CliRunner()
    refused = runner.invoke(app, ["config", "validate", str(p)])
    assert refused.exit_code == 1
    assert "--allow-raw-guids" in refused.output
    allowed = runner.invoke(app, ["config", "validate", str(p), "--allow-raw-guids"])
    assert allowed.exit_code == 0, allowed.output
    assert "OK -- 1 environment(s) parsed: DEV" in allowed.output
    assert "1 raw GUID(s) allowed: find_replace.[0].replace_value.DEV" in allowed.output


def test_config_validate_reads_the_other_spelling_and_says_so(tmp_path: Path) -> None:
    _write(
        tmp_path,
        'find_replace:\n  - find_value: x\n    replace_value:\n      DEV: "v"\n',
        name="parameter.yml",
    )
    result = CliRunner().invoke(app, ["config", "validate", str(tmp_path / "parameters.yml")])
    assert result.exit_code == 0, result.output
    assert "note: read" in result.output and "parameter.yml" in result.output


def test_config_validate_scopes_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write(tmp_path, _TWO_ENVS)
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    monkeypatch.setenv("SHARED_VAR", "shared")
    runner = CliRunner()
    assert runner.invoke(app, ["config", "validate", str(p), "-e", "DEV"]).exit_code == 0
    prod = runner.invoke(app, ["config", "validate", str(p), "-e", "PROD"])
    assert prod.exit_code == 1 and "ONLY_PROD_VAR" in prod.output
    typo = runner.invoke(app, ["config", "validate", str(p), "-e", "PRDO"])
    assert typo.exit_code == 1 and "not declared" in typo.output


def test_config_validate_missing_file_exits_2_naming_both_spellings(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["config", "validate", str(tmp_path / "parameters.yml")])
    assert result.exit_code == 2
    assert "parameter.yml" in result.output


def test_deploy_commands_expose_the_opt_in() -> None:
    runner = CliRunner()
    run_help = runner.invoke(app, ["deploy", "run", "--help"]).output
    validate_help = runner.invoke(app, ["deploy", "validate", "--help"]).output
    assert "--allow-raw-guids" in run_help
    assert "--allow-raw-guids" in validate_help and "--environment" in validate_help
    assert "--no-allow-raw-guids" in run_help and "--no-allow-raw-guids" in validate_help


# ---------------------------------------------------------------------------
# Review round 1 on PR #88: the wildcard in any case, model bindings in the
# environment check and in scoping, the settings opt-in wherever the file is
# read, and an explicit --no-allow-raw-guids.
# ---------------------------------------------------------------------------

_LOWER_ALL = """
find_replace:
  - find_value: "a"
    replace_value:
      DEV: "dev-a"
      PROD: "prod-a"
  - find_value: "b"
    replace_value:
      _all_: "z"
"""

_MODELS_ONLY = """
semantic_model_binding:
  models:
    - semantic_model_name: sales
      connection_id:
        PROD: "$ENV:ONLY_PROD_VAR"
"""


def test_lowercase_wildcard_slot_is_kept_in_scope(tmp_path: Path) -> None:
    """fabric-cicd matches ``_ALL_`` in any case; a ``_all_`` entry must reach it."""
    cfg = load_and_validate(_write(tmp_path, _LOWER_ALL), environment="DEV")
    assert cfg.environments_seen == frozenset({"DEV", "PROD"})
    out = substitute_env_references(cfg.raw, environment="DEV")
    assert [e["find_value"] for e in out["find_replace"]] == ["a", "b"]
    assert out["find_replace"][1]["replace_value"] == {"_all_": "z"}


def test_lowercase_wildcard_counts_for_the_environment_check(tmp_path: Path) -> None:
    only = 'find_replace:\n  - find_value: "b"\n    replace_value:\n      _all_: "z"\n'
    cfg = load_and_validate(_write(tmp_path, only), environment="ANY")
    assert cfg.environments_seen == frozenset()


def test_model_binding_maps_count_for_the_environment_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONLY_PROD_VAR", "p")
    with pytest.raises(UnknownEnvironmentError):
        load_and_validate(_write(tmp_path, _MODELS_ONLY), environment="PRDO")
    cfg = load_and_validate(_write(tmp_path, _MODELS_ONLY), environment="PROD")
    assert cfg.environments_seen == frozenset({"PROD"})


def test_scoped_binding_with_no_slot_for_the_target_is_dropped_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduced in review: ``default: {}`` / a model entry without ``connection_id``
    made fabric-cicd 1.3.0 stop with "Deployment terminated due to an invalid
    parameter file" on a DEV deploy whose binding names only PROD."""
    monkeypatch.setenv("ONLY_PROD_VAR", "prod")
    default_only = {
        "semantic_model_binding": {"default": {"connection_id": {"PROD": "$ENV:ONLY_PROD_VAR"}}}
    }
    assert substitute_env_references(default_only, environment="DEV") == {}
    models_only = {
        "semantic_model_binding": {
            "models": [
                {"semantic_model_name": "m", "connection_id": {"PROD": "$ENV:ONLY_PROD_VAR"}}
            ]
        }
    }
    assert substitute_env_references(models_only, environment="DEV") == {}


def test_scoped_binding_keeps_the_parts_that_bind_the_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONLY_DEV_VAR", "dev")
    monkeypatch.setenv("ONLY_PROD_VAR", "prod")
    doc = {
        "semantic_model_binding": {
            "default": {"connection_id": {"DEV": "$ENV:ONLY_DEV_VAR"}},
            "models": [
                {"semantic_model_name": "m1", "connection_id": {"PROD": "$ENV:ONLY_PROD_VAR"}},
                {"semantic_model_name": "m2", "connection_id": {"_ALL_": "$ENV:ONLY_DEV_VAR"}},
            ],
        }
    }
    assert substitute_env_references(doc, environment="DEV") == {
        "semantic_model_binding": {
            "default": {"connection_id": {"DEV": "dev"}},
            "models": [{"semantic_model_name": "m2", "connection_id": {"_ALL_": "dev"}}],
        }
    }


def test_legacy_binding_list_is_untouched_by_scoping() -> None:
    doc = {"semantic_model_binding": [{"connection_id": _GUID, "semantic_model_name": "m"}]}
    assert substitute_env_references(doc, environment="DEV") == doc


def test_load_and_validate_reads_the_settings_opt_in_when_the_caller_passes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback, sync and preflight call the validator without the flag; the
    documented settings key must still apply there. An explicit False wins."""
    p = _write(tmp_path, _STOCK)
    (tmp_path / ".sigantry.toml").write_text("[deploy]\nallow_raw_guids = true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = load_and_validate(p)
    assert cfg.raw_guids == ("find_replace.[0].replace_value.DEV",)
    with pytest.raises(HardcodedGuidError):
        load_and_validate(p, allow_raw_guids=False)


def test_resolve_allow_raw_guids_explicit_false_beats_the_settings(tmp_path: Path) -> None:
    toml = tmp_path / ".sigantry.toml"
    toml.write_text("[deploy]\nallow_raw_guids = true\n", encoding="utf-8")
    assert resolve_allow_raw_guids(False, settings_path=toml) is False
    assert resolve_allow_raw_guids(None, settings_path=toml) is True


def test_config_validate_no_allow_raw_guids_overrides_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PROD job can refuse raw GUIDs for one run although CI sets the env var."""
    p = _write(tmp_path, _STOCK, name="parameter.yml")
    monkeypatch.setenv("SIGANTRY_DEPLOY__ALLOW_RAW_GUIDS", "true")
    runner = CliRunner()
    assert runner.invoke(app, ["config", "validate", str(p)]).exit_code == 0
    refused = runner.invoke(app, ["config", "validate", str(p), "--no-allow-raw-guids"])
    assert refused.exit_code == 1 and "hard-coded GUID" in refused.output
    both = runner.invoke(
        app, ["config", "validate", str(p), "--allow-raw-guids", "--no-allow-raw-guids"]
    )
    assert both.exit_code == 1 and "cannot both be given" in both.output


def test_load_reports_the_path_as_written_when_used_as_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows CI: ``str(Path("p[/x].yml"))`` is ``p[\\x].yml``, so the CLI printed a
    spelling the operator never typed (``test_validate_params_and_graph_paths_printed_as_written``).
    On every platform ``str(Path("./p.yml"))`` is ``p.yml``, which reproduces it here."""
    body = 'find_replace:\n  - find_value: x\n    replace_value:\n      DEV: "v"\n'
    _write(tmp_path, body, name="p.yml")
    _write(tmp_path, body, name="parameter.yml")
    monkeypatch.chdir(tmp_path)
    assert load_and_validate("./p.yml").path == "./p.yml"
    # A substituted sibling spelling is reported resolved: the CLI's note depends on it.
    assert load_and_validate("./parameters.yml").path == str(Path("./parameter.yml"))
