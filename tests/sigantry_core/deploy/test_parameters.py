"""Unit tests for sigantry_core.deploy.parameters (DEPLOY-03, T-4-03)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigantry_core.deploy.parameters import (
    HardcodedGuidError,
    ParametersConfig,
    load_and_validate,
    substitute_env_references,
    write_substituted_parameters,
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "parameters.yml"
    p.write_text(text, encoding="utf-8")
    return p


def test_hardcoded_guid_error_is_value_error() -> None:
    """HardcodedGuidError is a subclass of ValueError — callers can catch
    either.
    """
    assert issubclass(HardcodedGuidError, ValueError)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_and_validate(tmp_path / "does-not-exist.yml")


def test_load_empty_yaml_returns_empty_config(tmp_path: Path) -> None:
    """yaml.safe_load of an empty file returns None; loader normalises to {}."""
    p = _write(tmp_path, "")
    cfg = load_and_validate(p)
    assert isinstance(cfg, ParametersConfig)
    assert cfg.raw == {}
    assert cfg.path == str(p)
    assert cfg.environments_seen == frozenset()


def test_load_rejects_hardcoded_guid_with_trail(tmp_path: Path) -> None:
    """Raw GUID anywhere outside $items./$workspace./_ALL_ must raise, and the
    error message must include the GUID + dotted trail for actionable
    remediation (T-4-03)."""
    p = _write(
        tmp_path,
        """
find_replace:
  - find_value: "placeholder"
    replace_value:
      DEV: "11111111-2222-3333-4444-555555555555"
""",
    )
    with pytest.raises(HardcodedGuidError) as ei:
        load_and_validate(p)
    msg = str(ei.value)
    assert "11111111-2222-3333-4444-555555555555" in msg
    assert "find_replace" in msg
    assert "DEV" in msg


def test_load_accepts_items_reference_with_guid_shape(tmp_path: Path) -> None:
    """A $items.<Type>.<Name>.$id reference must pass even though GUIDs are
    substituted inside at runtime."""
    p = _write(
        tmp_path,
        """
find_replace:
  - find_value: "placeholder"
    replace_value:
      DEV: "$items.Lakehouse.Bronze.$id"
      TEST: "$workspace.$id"
      PROD: "$ENV:FABRIC_WORKSPACE_ID"
""",
    )
    # Set env var for the PROD branch.
    import os

    os.environ["FABRIC_WORKSPACE_ID"] = "anything"
    try:
        cfg = load_and_validate(p)
    finally:
        os.environ.pop("FABRIC_WORKSPACE_ID", None)
    assert cfg.environments_seen == frozenset({"DEV", "TEST", "PROD"})


def test_load_rejects_unresolved_env_reference(tmp_path: Path) -> None:
    """$ENV:<VAR> where VAR is not in os.environ raises RuntimeError."""
    p = _write(
        tmp_path,
        """
find_replace:
  - find_value: "placeholder"
    replace_value:
      DEV: "$ENV:THIS_SHOULD_BE_ABSENT_XYZ_42"
""",
    )
    import os

    # Make sure it's not set.
    os.environ.pop("THIS_SHOULD_BE_ABSENT_XYZ_42", None)
    with pytest.raises(RuntimeError, match=r"\$ENV:THIS_SHOULD_BE_ABSENT_XYZ_42"):
        load_and_validate(p)


def test_load_accepts_resolved_env_reference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_VAR_OK", "some-value")
    p = _write(
        tmp_path,
        """
key_value_replace:
  - find_key: "$.properties.x"
    replace_value:
      DEV: "$ENV:MY_VAR_OK"
""",
    )
    cfg = load_and_validate(p)
    assert "DEV" in cfg.environments_seen


def test_environments_seen_excludes_all_wildcard(tmp_path: Path) -> None:
    """_ALL_ is a wildcard — not an environment name."""
    p = _write(
        tmp_path,
        """
key_value_replace:
  - find_key: "$.x"
    replace_value:
      _ALL_: "$workspace.$id"
""",
    )
    cfg = load_and_validate(p)
    assert cfg.environments_seen == frozenset()


def test_environments_seen_aggregates_across_sections(tmp_path: Path) -> None:
    """find_replace + key_value_replace + spark_pool + semantic_model_binding
    all contribute environment keys."""
    p = _write(
        tmp_path,
        """
find_replace:
  - find_value: "a"
    replace_value:
      DEV: "$workspace.$id"

key_value_replace:
  - find_key: "$.x"
    replace_value:
      TEST: "$items.Lakehouse.Bronze.$id"

spark_pool:
  - instance_pool_id: "pool-a"
    replace_value:
      PROD:
        type: "Capacity"
        name: "prod-pool"

semantic_model_binding:
  default:
    connection_id:
      STAGING: "$workspace.$id"
""",
    )
    cfg = load_and_validate(p)
    assert cfg.environments_seen == frozenset({"DEV", "TEST", "PROD", "STAGING"})


def test_rejects_hardcoded_guid_in_spark_pool(tmp_path: Path) -> None:
    """GUID hidden under spark_pool.replace_value.DEV still gets rejected."""
    p = _write(
        tmp_path,
        """
spark_pool:
  - instance_pool_id: "pool-x"
    replace_value:
      DEV:
        type: "Capacity"
        name: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
""",
    )
    with pytest.raises(HardcodedGuidError):
        load_and_validate(p)


def test_rejects_scalar_yaml(tmp_path: Path) -> None:
    """A file that yaml.safe_loads to a non-dict must raise ValueError (not
    silently pass validation)."""
    p = _write(tmp_path, "'just a string'\n")
    with pytest.raises(ValueError):
        load_and_validate(p)


# ---------------------------------------------------------------------------
# $ENV: substitution -- toolkit-side expansion (fabric-cicd v1.0.0 does NOT
# accept $ENV: refs in replace_value slots; the toolkit substitutes itself
# into a tempfile copy that is handed to FabricWorkspace).
# ---------------------------------------------------------------------------


def test_substitute_env_references_expands_single_token(monkeypatch) -> None:
    """A single ``$ENV:VAR`` token is replaced with ``os.environ[VAR]``."""
    monkeypatch.setenv("FABRIC_WS", "abc-123")
    doc = {"find_replace": [{"find_value": "ph", "replace_value": {"DEV": "$ENV:FABRIC_WS"}}]}
    out = substitute_env_references(doc)
    assert out["find_replace"][0]["replace_value"]["DEV"] == "abc-123"
    # Original document is left untouched (deep copy semantics).
    assert doc["find_replace"][0]["replace_value"]["DEV"] == "$ENV:FABRIC_WS"


def test_substitute_env_references_expands_multiple_tokens_in_one_string(
    monkeypatch,
) -> None:
    """Multiple ``$ENV:`` tokens inside one string each get replaced."""
    monkeypatch.setenv("PFX", "pre")
    monkeypatch.setenv("SFX", "post")
    doc = {"k": "$ENV:PFX-middle-$ENV:SFX"}
    assert substitute_env_references(doc) == {"k": "pre-middle-post"}


def test_substitute_env_references_no_op_without_tokens(monkeypatch) -> None:
    """A document without any ``$ENV:`` tokens round-trips unchanged."""
    doc = {"find_replace": [{"find_value": "x", "replace_value": {"DEV": "$workspace.$id"}}]}
    out = substitute_env_references(doc)
    assert out == doc
    # Distinct objects (deep copy).
    assert out is not doc
    assert out["find_replace"] is not doc["find_replace"]


def test_substitute_env_references_raises_on_unset_var(monkeypatch) -> None:
    """Direct callers see KeyError when a referenced var is unset."""
    monkeypatch.delenv("UNSET_ENV_FOR_TEST_42", raising=False)
    doc = {"k": "$ENV:UNSET_ENV_FOR_TEST_42"}
    with pytest.raises(KeyError, match=r"UNSET_ENV_FOR_TEST_42"):
        substitute_env_references(doc)


def test_substitute_env_references_preserves_non_string_scalars(monkeypatch) -> None:
    """Ints / bools / None pass through untouched -- only strings are walked."""
    doc = {"items_to_include": ["a"], "count": 42, "enabled": True, "skip": None}
    assert substitute_env_references(doc) == doc


def test_write_substituted_parameters_round_trips(tmp_path: Path, monkeypatch) -> None:
    """``write_substituted_parameters`` writes a YAML file with $ENV: tokens
    expanded; reloading via load_and_validate yields the substituted values
    AND drops the $ENV: pre-flight requirement (since no tokens remain).
    """
    monkeypatch.setenv("WS_FROM_ENV", "expanded-workspace-id")
    src = _write(
        tmp_path,
        """
find_replace:
  - find_value: "PLACEHOLDER"
    replace_value:
      DEV: "$ENV:WS_FROM_ENV"
""",
    )
    cfg = load_and_validate(src)
    target = tmp_path / "out" / "parameters.yml"
    written = write_substituted_parameters(cfg, target)
    assert written == target
    assert target.is_file()
    # Re-load the substituted file and prove the $ENV: token is gone.
    monkeypatch.delenv("WS_FROM_ENV", raising=False)
    reloaded = load_and_validate(target)  # would raise if $ENV: remained unset
    assert reloaded.raw["find_replace"][0]["replace_value"]["DEV"] == "expanded-workspace-id"


def test_write_substituted_parameters_creates_parent_dir(tmp_path: Path, monkeypatch) -> None:
    """The target's parent directory is created on demand."""
    monkeypatch.setenv("X", "y")
    cfg = load_and_validate(
        _write(
            tmp_path,
            """
find_replace:
  - find_value: "p"
    replace_value:
      DEV: "$ENV:X"
""",
        )
    )
    target = tmp_path / "deeply" / "nested" / "parameters.yml"
    write_substituted_parameters(cfg, target)
    assert target.is_file()
