"""AdoVariableGroupSecretStore tests (Plan 16-02).

Read-modify-write semantics on set per RESEARCH §Pitfall 3 (the entire
variable-group document round-trips through the REST API; existing keys
must be preserved).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import respx

from sigantry_core.auth import TokenProvider
from sigantry_core.protocols import SecretStore
from sigantry_core.secrets.ado_variable_group import AdoVariableGroupSecretStore


def _existing_group_body(extra_vars: int = 8) -> dict:
    """A realistic GET response for an ADO variable group with N existing vars."""
    body = {
        "id": 7,
        "name": "fabric-deploy-secrets",
        "type": "Vsts",
        "description": "fixture",
        "variables": {},
    }
    for i in range(extra_vars):
        body["variables"][f"EXISTING_VAR_{i}"] = {
            "value": f"existing-value-{i}",
            "isSecret": (i % 2 == 0),
        }
    return body


def _make_token_provider() -> TokenProvider:
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "test-bearer-token"
    mp.tenant_id = "test-tenant-id"
    mp.last_credential_class.return_value = "MockCredential"
    return mp


def test_set_preserves_other_vars() -> None:
    """RESEARCH §Pitfall 3 pin: setting one var preserves all 8 existing vars.

    The naive single-key PUT would wipe the 8 other variables. The impl MUST
    GET the full body, mutate one entry, and PUT the full body back.
    """
    body = _existing_group_body(extra_vars=8)
    captured: dict = {}

    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/distributedtask/variablegroups/7").mock(
            return_value=httpx.Response(200, json=body)
        )

        def _put_handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=captured["body"])

        router.put("/myproject/_apis/distributedtask/variablegroups/7").mock(
            side_effect=_put_handler
        )

        store = AdoVariableGroupSecretStore(
            organization="myorg",
            project="myproject",
            group_id=7,
            token_provider=_make_token_provider(),
        )
        store.set("NEW_VAR", "new-secret-value")

    put_body = captured["body"]
    variables = put_body["variables"]
    # 8 existing + 1 new = 9 total.
    assert len(variables) == 9
    # All 8 existing keys preserved.
    for i in range(8):
        key = f"EXISTING_VAR_{i}"
        assert key in variables, f"{key} was wiped by the PUT (Pitfall 3 violation)"
        # Original values preserved (not blanked).
        assert variables[key]["value"] == f"existing-value-{i}"
    # New key carries isSecret: True.
    assert variables["NEW_VAR"]["value"] == "new-secret-value"
    assert variables["NEW_VAR"]["isSecret"] is True


def test_set_marks_isSecret_true() -> None:  # noqa: N802
    # Test name mandated by Plan 16-00 Wave 0 stub naming; mirrors the
    # ADO REST field ``isSecret`` (camelCase server-side; intentional).
    """Every secret stored via set() carries isSecret: True (server-side encryption)."""
    body = _existing_group_body(extra_vars=2)
    captured: dict = {}

    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/distributedtask/variablegroups/7").mock(
            return_value=httpx.Response(200, json=body)
        )

        def _put_handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=captured["body"])

        router.put("/myproject/_apis/distributedtask/variablegroups/7").mock(
            side_effect=_put_handler
        )

        store = AdoVariableGroupSecretStore(
            organization="myorg",
            project="myproject",
            group_id=7,
            token_provider=_make_token_provider(),
        )
        store.set("CONFIDENTIAL_KEY", "topsecret")

    var = captured["body"]["variables"]["CONFIDENTIAL_KEY"]
    assert var == {"value": "topsecret", "isSecret": True}


def test_delete_via_read_modify_write() -> None:
    """delete() GETs the body, removes one key, and PUTs the rest back."""
    body = _existing_group_body(extra_vars=4)
    # Add the var we plan to delete.
    body["variables"]["TO_DELETE"] = {"value": "x", "isSecret": True}
    captured: dict = {}

    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        get_route = router.get("/myproject/_apis/distributedtask/variablegroups/7").mock(
            return_value=httpx.Response(200, json=body)
        )

        def _put_handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=captured["body"])

        put_route = router.put("/myproject/_apis/distributedtask/variablegroups/7").mock(
            side_effect=_put_handler
        )

        store = AdoVariableGroupSecretStore(
            organization="myorg",
            project="myproject",
            group_id=7,
            token_provider=_make_token_provider(),
        )
        store.delete("TO_DELETE")

    assert get_route.call_count == 1
    assert put_route.call_count == 1
    variables = captured["body"]["variables"]
    assert "TO_DELETE" not in variables
    # Other 4 keys preserved.
    assert len(variables) == 4
    for i in range(4):
        assert f"EXISTING_VAR_{i}" in variables


def test_list_keys_filters_by_prefix() -> None:
    """list_keys returns the prefix-filtered subset of variable names."""
    body = _existing_group_body(extra_vars=0)
    body["variables"] = {
        "DEPLOY_KEY_A": {"value": "a", "isSecret": True},
        "DEPLOY_KEY_B": {"value": "b", "isSecret": True},
        "OTHER_VAR": {"value": "c", "isSecret": False},
    }

    with respx.mock(base_url="https://dev.azure.com/myorg") as router:
        router.get("/myproject/_apis/distributedtask/variablegroups/7").mock(
            return_value=httpx.Response(200, json=body)
        )
        store = AdoVariableGroupSecretStore(
            organization="myorg",
            project="myproject",
            group_id=7,
            token_provider=_make_token_provider(),
        )
        keys = store.list_keys(prefix="DEPLOY_")

    assert sorted(keys) == ["DEPLOY_KEY_A", "DEPLOY_KEY_B"]


def test_set_emits_secret_change_record(tmp_audit_dir: Path) -> None:
    """set() emits a SecretChangeRecord on success."""
    body = _existing_group_body(extra_vars=1)

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_audit_dir
    try:
        with respx.mock(base_url="https://dev.azure.com/myorg") as router:
            router.get("/myproject/_apis/distributedtask/variablegroups/7").mock(
                return_value=httpx.Response(200, json=body)
            )
            router.put("/myproject/_apis/distributedtask/variablegroups/7").mock(
                return_value=httpx.Response(200, json=body)
            )
            store = AdoVariableGroupSecretStore(
                organization="myorg",
                project="myproject",
                group_id=7,
                token_provider=_make_token_provider(),
            )
            store.set("NEW_KEY", "v")
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    record = json.loads((tmp_audit_dir / "secret_changes.jsonl").read_text("utf-8").splitlines()[0])
    assert record["operation"] == "set"
    assert record["key"] == "NEW_KEY"
    assert record["store_name"] == "ado_variable_group"
    assert "value" not in record


def test_satisfies_secret_store_protocol() -> None:
    """AdoVariableGroupSecretStore satisfies the runtime_checkable SecretStore Protocol."""
    store = AdoVariableGroupSecretStore(
        organization="myorg",
        project="myproject",
        group_id=7,
        token_provider=_make_token_provider(),
    )
    assert isinstance(store, SecretStore)
    assert store.name == "ado_variable_group"
