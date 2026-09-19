"""Schema + loader tests for the environments.yml manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sigantry_core.deploy.environments_manifest import (
    EnvironmentsManifest,
    EnvironmentsManifestError,
    EnvTarget,
    load_environments_manifest,
)

_VALID = {
    "schema_version": "1.0",
    "targets": [
        {
            "name": "managed-data-dev",
            "workspace_id": "ws-dev",
            "environment_id": "env-dev",
            "wheels": ["dist/*.whl"],
        },
        {
            "name": "prod",
            "workspace_id": "ws-prod",
            "environment_id": "env-prod",
            "wheels": ["dist/aims_data_platform-1.6.0-py3-none-any.whl"],
            "policy": "pin",
            "gated": True,
        },
    ],
}


def test_valid_manifest_parses() -> None:
    m = EnvironmentsManifest.model_validate(_VALID)
    assert len(m.targets) == 2
    dev, prod = m.targets
    assert dev.policy == "float" and dev.gated is False
    assert prod.policy == "pin" and prod.gated is True


def test_load_from_disk(tmp_path: Path) -> None:
    p = tmp_path / "environments.yml"
    p.write_text(yaml.safe_dump(_VALID), encoding="utf-8")
    m = load_environments_manifest(p)
    assert m.targets[0].name == "managed-data-dev"


def test_extra_keys_forbidden() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        EnvTarget.model_validate(
            {
                "name": "x",
                "workspace_id": "w",
                "environment_id": "e",
                "wheels": ["a.whl"],
                "surprise": 1,
            }
        )


def test_bad_schema_version_rejected(tmp_path: Path) -> None:
    bad = {**_VALID, "schema_version": "2.0"}
    p = tmp_path / "environments.yml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(EnvironmentsManifestError) as ei:
        load_environments_manifest(p)
    assert ei.value.violations


def test_pin_target_rejects_glob() -> None:
    with pytest.raises(Exception):  # noqa: B017
        EnvTarget.model_validate(
            {
                "name": "prod",
                "workspace_id": "w",
                "environment_id": "e",
                "wheels": ["dist/*.whl"],
                "policy": "pin",
            }
        )


def test_invalid_policy_rejected() -> None:
    with pytest.raises(Exception):  # noqa: B017
        EnvTarget.model_validate(
            {
                "name": "x",
                "workspace_id": "w",
                "environment_id": "e",
                "wheels": ["a.whl"],
                "policy": "latest",
            }
        )


def test_empty_wheels_rejected() -> None:
    with pytest.raises(Exception):  # noqa: B017
        EnvTarget.model_validate(
            {"name": "x", "workspace_id": "w", "environment_id": "e", "wheels": []}
        )


def test_duplicate_target_names_rejected(tmp_path: Path) -> None:
    dup = {
        "schema_version": "1.0",
        "targets": [
            {"name": "a", "workspace_id": "w", "environment_id": "e", "wheels": ["x.whl"]},
            {"name": "a", "workspace_id": "w2", "environment_id": "e2", "wheels": ["y.whl"]},
        ],
    }
    p = tmp_path / "environments.yml"
    p.write_text(yaml.safe_dump(dup), encoding="utf-8")
    with pytest.raises(EnvironmentsManifestError):
        load_environments_manifest(p)


def test_empty_targets_rejected(tmp_path: Path) -> None:
    p = tmp_path / "environments.yml"
    p.write_text(yaml.safe_dump({"schema_version": "1.0", "targets": []}), encoding="utf-8")
    with pytest.raises(EnvironmentsManifestError):
        load_environments_manifest(p)
