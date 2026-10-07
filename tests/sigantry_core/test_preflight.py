"""Tests for preflight pre-deployment safety probes (ADR-0015).

Each probe is held to one rule: it reports PASS only for something it
checked. The fixtures are shaped like the shipped manifests
(``templates/demo/sync.yml``, ``templates/workspace.example.yml``), not like
a schema the loaders reject, so a probe that reads the manifest the way the
product does is the probe under test.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sigantry_core.auth.audiences import FABRIC_SCOPE
from sigantry_core.auth.errors import TokenAcquisitionError
from sigantry_core.cli import app
from sigantry_core.preflight import cli as preflight_cli
from sigantry_core.preflight.engine import PreflightEngine
from sigantry_core.preflight.models import ProbeStatus
from sigantry_core.preflight.probes import (
    CapacityStateProbe,
    DependencyGraphProbe,
    EntraScopeProbe,
    SchemaSyntaxProbe,
)

_REPO = Path(__file__).resolve().parents[2]
_WORKSPACE_EXAMPLE = _REPO / "templates" / "workspace.example.yml"

_CREDENTIAL_VARS = (
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "AZURE_FEDERATED_TOKEN_FILE",
)

_WS_ID = "11111111-2222-4333-8444-555555555555"
_CAP_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_TENANT_A = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
_TENANT_B = "0b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b"


# ---------------------------------------------------------------------------
# Fixtures and doubles
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no explicit credential configured."""
    for var in _CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sync_tree(tmp_path: Path) -> Path:
    """A sync.yml shaped like templates/demo/sync.yml, with its item tree present."""
    manifest = tmp_path / "sync.yml"
    manifest.write_text(
        """
schema_version: "1.0.0"
items:
  - display_name: Sales
    type: Lakehouse
    local_path: fabric_items/Sales.Lakehouse
  - display_name: LoadOrders
    type: Notebook
    local_path: fabric_items/LoadOrders.Notebook
""",
        encoding="utf-8",
    )
    lakehouse = tmp_path / "fabric_items" / "Sales.Lakehouse"
    lakehouse.mkdir(parents=True)
    (lakehouse / ".platform").write_text(
        json.dumps({"metadata": {"type": "Lakehouse", "displayName": "Sales"}}),
        encoding="utf-8",
    )
    notebook = tmp_path / "fabric_items" / "LoadOrders.Notebook"
    notebook.mkdir(parents=True)
    (notebook / ".platform").write_text(
        json.dumps({"metadata": {"type": "Notebook", "displayName": "LoadOrders"}}),
        encoding="utf-8",
    )
    (notebook / "LoadOrders.ipynb").write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}),
        encoding="utf-8",
    )
    return manifest


@pytest.fixture
def workspace_yml(tmp_path: Path) -> Path:
    target = tmp_path / "workspace.yml"
    shutil.copyfile(_WORKSPACE_EXAMPLE, target)
    return target


def _flat(output: str) -> str:
    """Rich wraps at the terminal width and boxes panels; compare on plain words."""
    return " ".join(re.sub(r"[│┃╭╮╰╯─━┏┓┗┛┣┫┳┻╋┠┨┯┷┿┡┩┢┪┬┴┼]", " ", output).split())


def _jwt(claims: dict[str, Any]) -> str:
    """An unsigned JWT whose payload carries ``claims``."""

    def _b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{_b64({'alg': 'none'})}.{_b64(claims)}.sig"


class FakeProvider:
    """Stands in for TokenProvider: returns a token or raises, never calls Azure."""

    def __init__(self, *, token: str | None = None, error: str | None = None) -> None:
        self._token = token
        self._error = error
        self.calls = 0

    def get_token(self, scope: str) -> str:
        self.calls += 1
        assert scope == FABRIC_SCOPE
        if self._error is not None:
            raise TokenAcquisitionError(
                self._error, scope=scope, credential_used="FakeChainCredential"
            )
        assert self._token is not None
        return self._token

    def last_credential_class(self, scope: str) -> str | None:
        return "FakeChainCredential" if self._token is not None else None


class _Resp:
    def __init__(self, body: Any) -> None:
        self.json_body = body


class FakeClient:
    """Stands in for FabricRestClient for the capacity probe."""

    def __init__(
        self,
        *,
        workspace: dict[str, Any] | None = None,
        capacities: list[dict[str, Any]] | None = None,
        workspace_error: Exception | None = None,
        capacities_error: Exception | None = None,
    ) -> None:
        self._workspace = workspace
        self._capacities = capacities or []
        self._workspace_error = workspace_error
        self._capacities_error = capacities_error

    def send(self, method: str, path: str, **kwargs: Any) -> _Resp:
        assert method == "GET" and path == f"/v1/workspaces/{_WS_ID}"
        if self._workspace_error is not None:
            raise self._workspace_error
        return _Resp(self._workspace)

    def list_paginated(self, path: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        assert path == "/v1/capacities"
        if self._capacities_error is not None:
            raise self._capacities_error
        yield from self._capacities


def _workspace_payload(capacity_id: str | None = _CAP_ID) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": _WS_ID, "displayName": "demo-dev", "type": "Workspace"}
    if capacity_id is not None:
        payload["capacityId"] = capacity_id
    return payload


def _capacity_payload(state: str, capacity_id: str = _CAP_ID) -> dict[str, Any]:
    return {
        "id": capacity_id,
        "displayName": "F2-dev",
        "sku": {"name": "F2", "tier": "Fabric"},
        "region": "UK South",
        "state": state,
    }


# ---------------------------------------------------------------------------
# Default probe set
# ---------------------------------------------------------------------------


def test_default_probe_set_is_the_three_that_can_check_a_shipped_manifest() -> None:
    names = [p.name for p in PreflightEngine().probes]
    assert names == ["schema_syntax", "entra_scope", "capacity_state"]
    assert "dependency_graph" not in names


def test_explicit_empty_probe_list_is_honoured() -> None:
    assert PreflightEngine(probes=[]).probes == []


# ---------------------------------------------------------------------------
# schema_syntax
# ---------------------------------------------------------------------------


def test_schema_rejects_a_file_that_is_neither_manifest(tmp_path: Path) -> None:
    """The persona reproduction: ``foo: bar`` used to pass."""
    garbage = tmp_path / "garbage.yml"
    garbage.write_text("foo: bar\n", encoding="utf-8")
    result = SchemaSyntaxProbe().run(manifest_path=garbage, environment="dev")
    assert result.status == ProbeStatus.FAIL
    assert "neither a sync.yml nor a workspace.yml" in result.message
    assert set(result.details["errors"]) == {"sync", "workspace"}


def test_schema_rejects_a_missing_manifest(tmp_path: Path) -> None:
    result = SchemaSyntaxProbe().run(manifest_path=tmp_path / "nosuch.yml", environment="dev")
    assert result.status == ProbeStatus.FAIL
    assert "does not exist" in result.message


def test_schema_rejects_unparseable_yaml(tmp_path: Path) -> None:
    broken = tmp_path / "sync.yml"
    broken.write_text("items: [\n", encoding="utf-8")
    result = SchemaSyntaxProbe().run(manifest_path=broken, environment="dev")
    assert result.status == ProbeStatus.FAIL


def test_schema_passes_a_real_sync_manifest_and_counts_what_it_read(sync_tree: Path) -> None:
    result = SchemaSyntaxProbe().run(manifest_path=sync_tree, environment="dev")
    assert result.status == ProbeStatus.PASS
    assert result.details == {
        "manifest_kind": "sync",
        "items": 2,
        "artifacts_checked": 3,
        "params_checked": False,
    }
    assert "2 item path(s) present" in result.message
    assert "3 artifact file(s) parsed" in result.message
    assert "parameters.yml not given" in result.message


def test_schema_fails_when_an_item_path_is_missing(sync_tree: Path) -> None:
    """The demo manifest copied without its item tree used to pass with 0 artifacts."""
    shutil.rmtree(sync_tree.parent / "fabric_items" / "LoadOrders.Notebook")
    result = SchemaSyntaxProbe().run(manifest_path=sync_tree, environment="dev")
    assert result.status == ProbeStatus.FAIL
    assert "item 'LoadOrders'" in result.message
    assert "does not exist" in result.message


def test_schema_fails_on_a_corrupt_platform_file(sync_tree: Path) -> None:
    platform = sync_tree.parent / "fabric_items" / "Sales.Lakehouse" / ".platform"
    platform.write_text("{invalid json", encoding="utf-8")
    result = SchemaSyntaxProbe().run(manifest_path=sync_tree, environment="dev")
    assert result.status == ProbeStatus.FAIL
    assert "invalid JSON" in result.message


def test_schema_fails_on_a_notebook_without_cells(sync_tree: Path) -> None:
    nb = sync_tree.parent / "fabric_items" / "LoadOrders.Notebook" / "LoadOrders.ipynb"
    nb.write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    result = SchemaSyntaxProbe().run(manifest_path=sync_tree, environment="dev")
    assert result.status == ProbeStatus.FAIL
    assert "missing 'cells'" in result.message


def test_schema_passes_the_shipped_workspace_example(workspace_yml: Path) -> None:
    result = SchemaSyntaxProbe().run(manifest_path=workspace_yml, environment="dev")
    assert result.status == ProbeStatus.PASS
    assert result.details["manifest_kind"] == "workspace"
    assert "no item tree applies" in result.message


def test_schema_validates_parameters_when_given(sync_tree: Path, tmp_path: Path) -> None:
    """``--params`` was accepted and never read; a raw GUID now fails the probe."""
    params = tmp_path / "parameters.yml"
    params.write_text(
        'find_replace:\n  - find_value: x\n    replace_value:\n      DEV: "11111111-2222-3333-4444-555555555555"\n',
        encoding="utf-8",
    )
    result = SchemaSyntaxProbe().run(manifest_path=sync_tree, environment="dev", params_path=params)
    assert result.status == ProbeStatus.FAIL
    assert "parameters:" in result.message
    assert "hard-coded GUID" in result.message

    params.write_text(
        'find_replace:\n  - find_value: x\n    replace_value:\n      DEV: "$workspace.$id"\n',
        encoding="utf-8",
    )
    ok = SchemaSyntaxProbe().run(manifest_path=sync_tree, environment="dev", params_path=params)
    assert ok.status == ProbeStatus.PASS
    assert ok.details["params_checked"] is True
    assert "parameters.yml valid" in ok.message


def test_schema_fails_on_a_missing_parameters_file(sync_tree: Path, tmp_path: Path) -> None:
    result = SchemaSyntaxProbe().run(
        manifest_path=sync_tree, environment="dev", params_path=tmp_path / "nosuch.yml"
    )
    assert result.status == ProbeStatus.FAIL
    assert "parameters:" in result.message


# ---------------------------------------------------------------------------
# dependency_graph (kept importable, not a default)
# ---------------------------------------------------------------------------


def test_dependency_probe_skips_a_real_manifest_that_declares_no_dependencies(
    sync_tree: Path,
) -> None:
    result = DependencyGraphProbe().run(manifest_path=sync_tree, environment="dev")
    assert result.status == ProbeStatus.SKIP
    assert result.message.startswith("not checked:")


def test_dependency_probe_still_detects_cycles(tmp_path: Path) -> None:
    manifest = tmp_path / "deps.yml"
    manifest.write_text(
        "items:\n  - name: a\n    depends_on: [b]\n  - name: b\n    depends_on: [a]\n",
        encoding="utf-8",
    )
    result = DependencyGraphProbe().run(manifest_path=manifest, environment="dev")
    assert result.status == ProbeStatus.FAIL
    assert "Circular dependency" in result.message


# ---------------------------------------------------------------------------
# entra_scope
# ---------------------------------------------------------------------------


def test_entra_skips_without_a_provider(tmp_path: Path) -> None:
    result = EntraScopeProbe().run(manifest_path=tmp_path / "sync.yml", environment="prod")
    assert result.status == ProbeStatus.SKIP
    assert result.message.startswith("not checked:")


def test_entra_env_vars_alone_do_not_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The persona reproduction: a fake client id and secret used to PASS."""
    monkeypatch.setenv("AZURE_CLIENT_ID", "not-a-real-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "garbage")
    provider = FakeProvider(error="chain failed to acquire token")
    result = EntraScopeProbe().run(
        manifest_path=tmp_path / "sync.yml", environment="prod", token_provider=provider
    )
    assert provider.calls == 1
    assert result.status == ProbeStatus.FAIL
    assert "AZURE_CLIENT_ID" in result.message
    assert "could not acquire a Fabric token" in result.message
    assert result.details["configured_credential_vars"] == [
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
    ]


def test_entra_skips_when_nothing_is_configured_and_no_token_comes(tmp_path: Path) -> None:
    provider = FakeProvider(error="chain failed to acquire token")
    result = EntraScopeProbe().run(
        manifest_path=tmp_path / "sync.yml", environment="prod", token_provider=provider
    )
    assert result.status == ProbeStatus.SKIP
    assert result.message.startswith("not checked:")
    assert "az login" in result.message


def test_entra_passes_on_a_real_token_and_never_exposes_it(tmp_path: Path) -> None:
    token = _jwt({"tid": _TENANT_A, "appid": "app-1", "exp": 4102444800})
    result = EntraScopeProbe().run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        token_provider=FakeProvider(token=token),
    )
    assert result.status == ProbeStatus.PASS
    assert result.details["tenant_id"] == _TENANT_A
    assert result.details["credential"] == "FakeChainCredential"
    assert result.details["app_id"] == "app-1"
    assert token not in result.model_dump_json()


def test_entra_fails_when_the_token_tenant_differs(tmp_path: Path) -> None:
    token = _jwt({"tid": _TENANT_B})
    result = EntraScopeProbe().run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        token_provider=FakeProvider(token=token),
        tenant_id=_TENANT_A,
    )
    assert result.status == ProbeStatus.FAIL
    assert _TENANT_B in result.message and _TENANT_A in result.message


def test_entra_passes_when_the_token_tenant_matches(tmp_path: Path) -> None:
    token = _jwt({"tid": _TENANT_A})
    result = EntraScopeProbe().run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        token_provider=FakeProvider(token=token),
        tenant_id=_TENANT_A,
    )
    assert result.status == ProbeStatus.PASS


def test_entra_warns_when_a_pinned_tenant_cannot_be_confirmed(tmp_path: Path) -> None:
    token = _jwt({"appid": "app-1"})
    result = EntraScopeProbe().run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        token_provider=FakeProvider(token=token),
        tenant_id=_TENANT_A,
    )
    assert result.status == ProbeStatus.WARN
    assert "no tid claim" in result.message


# ---------------------------------------------------------------------------
# capacity_state
# ---------------------------------------------------------------------------


def _capacity(client: Any, workspace_id: str | None = _WS_ID) -> Any:
    return CapacityStateProbe().run(
        manifest_path=Path("sync.yml"), environment="prod", client=client, workspace_id=workspace_id
    )


def test_capacity_skips_without_a_workspace_id() -> None:
    result = _capacity(FakeClient(workspace=_workspace_payload()), workspace_id=None)
    assert result.status == ProbeStatus.SKIP
    assert "--workspace-id" in result.message


def test_capacity_skips_without_a_client() -> None:
    result = _capacity(None)
    assert result.status == ProbeStatus.SKIP
    assert result.message.startswith("not checked:")


def test_capacity_passes_when_active() -> None:
    client = FakeClient(workspace=_workspace_payload(), capacities=[_capacity_payload("Active")])
    result = _capacity(client)
    assert result.status == ProbeStatus.PASS
    assert result.details["state"] == "Active"
    assert result.details["capacity_name"] == "F2-dev"


@pytest.mark.parametrize("state", ["Paused", "Suspended", "Inactive", "Deleted"])
def test_capacity_fails_when_down(state: str) -> None:
    client = FakeClient(workspace=_workspace_payload(), capacities=[_capacity_payload(state)])
    result = _capacity(client)
    assert result.status == ProbeStatus.FAIL
    assert state in result.message


def test_capacity_warns_on_a_transient_state() -> None:
    client = FakeClient(workspace=_workspace_payload(), capacities=[_capacity_payload("Updating")])
    result = _capacity(client)
    assert result.status == ProbeStatus.WARN


def test_capacity_fails_when_the_workspace_has_none() -> None:
    result = _capacity(FakeClient(workspace=_workspace_payload(capacity_id=None)))
    assert result.status == ProbeStatus.FAIL
    assert "no Fabric capacity assigned" in result.message


def test_capacity_fails_when_the_workspace_cannot_be_read() -> None:
    result = _capacity(FakeClient(workspace_error=RuntimeError("403 Forbidden")))
    assert result.status == ProbeStatus.FAIL
    assert "could not be read" in result.message


def test_capacity_warns_when_the_capacity_is_not_visible() -> None:
    other = _capacity_payload("Active", capacity_id="ffffffff-0000-4000-8000-000000000000")
    result = _capacity(FakeClient(workspace=_workspace_payload(), capacities=[other]))
    assert result.status == ProbeStatus.WARN
    assert "cannot list it" in result.message


def test_capacity_warns_when_capacities_cannot_be_listed() -> None:
    client = FakeClient(workspace=_workspace_payload(), capacities_error=RuntimeError("403"))
    result = _capacity(client)
    assert result.status == ProbeStatus.WARN
    assert "could not be read" in result.message


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def test_engine_strict_fails_on_an_unchecked_probe(sync_tree: Path) -> None:
    """Capacity SKIP + Entra SKIP used to leave ``--strict`` at exit 0."""
    engine = PreflightEngine()
    provider = FakeProvider(error="no credential")
    lenient = engine.run(manifest_path=sync_tree, token_provider=provider, strict=False)
    assert lenient.has_skips is True
    assert lenient.passed is True
    assert lenient.skipped == ["entra_scope", "capacity_state"]

    strict = engine.run(manifest_path=sync_tree, token_provider=provider, strict=True)
    assert strict.passed is False
    assert strict.failed == []


def test_engine_passes_when_every_probe_checked(sync_tree: Path) -> None:
    engine = PreflightEngine()
    client = FakeClient(workspace=_workspace_payload(), capacities=[_capacity_payload("Active")])
    report = engine.run(
        manifest_path=sync_tree,
        token_provider=FakeProvider(token=_jwt({"tid": _TENANT_A})),
        client=client,
        workspace_id=_WS_ID,
        strict=True,
    )
    assert [r.status for r in report.results] == [ProbeStatus.PASS] * 3
    assert report.passed is True and report.has_skips is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider(error="chain failed to acquire token")
    monkeypatch.setattr(preflight_cli, "_make_token_provider", lambda tenant_id: provider)
    return provider


def test_cli_token_provider_seam_pins_the_tenant() -> None:
    provider = preflight_cli._make_token_provider(_TENANT_A)
    assert provider.tenant_id == _TENANT_A


def test_cli_garbage_manifest_exits_1_even_with_credentials_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_token: FakeProvider
) -> None:
    """The persona's exact run: ``foo: bar`` plus fake SPN vars and --strict gave rc 0."""
    monkeypatch.setenv("AZURE_CLIENT_ID", "not-a-real-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "garbage")
    garbage = tmp_path / "garbage.yml"
    garbage.write_text("foo: bar\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["preflight", "--manifest", str(garbage), "--strict"])
    assert result.exit_code == 1
    assert "failed: schema_syntax, entra_scope" in _flat(result.output)
    assert "not checked under --strict: capacity_state" in _flat(result.output)


def test_cli_strict_exits_1_when_a_probe_could_not_check(
    sync_tree: Path, no_token: FakeProvider
) -> None:
    result = CliRunner().invoke(app, ["preflight", "--manifest", str(sync_tree), "--strict"])
    assert result.exit_code == 1
    assert "not checked under --strict: entra_scope, capacity_state" in _flat(result.output)
    assert "simulation successful" not in _flat(result.output)


def test_cli_lenient_run_names_what_it_did_not_check(
    sync_tree: Path, no_token: FakeProvider
) -> None:
    result = CliRunner().invoke(app, ["preflight", "--manifest", str(sync_tree)])
    assert result.exit_code == 0
    assert "2 did not check anything: entra_scope, capacity_state" in _flat(result.output)
    assert "--strict fails on an unchecked probe" in _flat(result.output)


def test_cli_json_carries_has_skips_and_the_not_checked_messages(
    sync_tree: Path, no_token: FakeProvider
) -> None:
    result = CliRunner().invoke(app, ["preflight", "--manifest", str(sync_tree), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["passed"] is True
    assert data["has_skips"] is True
    by_name = {r["name"]: r for r in data["results"]}
    assert by_name["entra_scope"]["status"] == "SKIP"
    assert by_name["entra_scope"]["message"].startswith("not checked:")
    assert by_name["capacity_state"]["status"] == "SKIP"


def test_cli_passes_only_when_every_probe_checked(
    sync_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(token=_jwt({"tid": _TENANT_A}))
    monkeypatch.setattr(preflight_cli, "_make_token_provider", lambda tenant_id: provider)
    fake_client = FakeClient(
        workspace=_workspace_payload(), capacities=[_capacity_payload("Active")]
    )

    class _ClientCtx:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return fake_client

        def __exit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(preflight_cli, "FabricRestClient", _ClientCtx)
    result = CliRunner().invoke(
        app,
        [
            "preflight",
            "--manifest",
            str(sync_tree),
            "--workspace-id",
            _WS_ID,
            "--tenant-id",
            _TENANT_A,
            "--strict",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "all 3 probes checked" in _flat(result.output)


def test_cli_help_documents_the_new_flags_and_strict_meaning() -> None:
    result = CliRunner().invoke(app, ["preflight", "--help"])
    assert result.exit_code == 0
    assert "--workspace-id" in _flat(result.output)
    assert "--tenant-id" in _flat(result.output)
    assert "could not check" in _flat(result.output)


def test_cli_message_with_markup_brackets_is_rendered_verbatim(
    tmp_path: Path, no_token: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe message holding ``[...]`` must not be read as Rich markup."""
    monkeypatch.setenv("COLUMNS", "400")  # keep the long path on one table line
    garbage = tmp_path / "[bold]x.yml"
    garbage.write_text("foo: bar\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["preflight", "--manifest", str(garbage)])
    assert result.exit_code == 1
    assert "MarkupError" not in result.output
    assert "[bold]x.yml" in _flat(result.output)
