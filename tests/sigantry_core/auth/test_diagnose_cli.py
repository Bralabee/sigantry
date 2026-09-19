"""Typer CLI smoke tests via CliRunner. Mocks token provider + httpx probes."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from typer.testing import CliRunner

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, GRAPH_AUDIENCE
from sigantry_core.auth.cli import app
from sigantry_core.auth.token_provider import reset_token_provider


@pytest.fixture(autouse=True)
def _clean_singleton():
    reset_token_provider()
    yield
    reset_token_provider()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_jwt(make_jwt) -> str:
    # Stable claims for the CLI tests - no raw secrets.
    return make_jwt(
        {
            "aud": "https://api.fabric.microsoft.com",
            "tid": "tid-abc",
            "oid": "oid-123",
            "appid": "app-456",
            "exp": int(time.time()) + 3600,
        }
    )


class TestDryRun:
    def test_dry_run_exits_0(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--dry-run"])
        assert result.exit_code == 0, result.stdout
        assert "dry-run" in result.stdout.lower()

    def test_dry_run_json_output(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--dry-run", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["mode"] == "dry-run"
        assert data["will_acquire_token"] is False

    def test_bad_output_format_exits_4(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--dry-run", "--output", "yaml"])
        assert result.exit_code == 4

    def test_unknown_scope_exits_nonzero(self, runner: CliRunner) -> None:
        # Typer maps BadParameter to a non-zero exit.
        result = runner.invoke(app, ["--dry-run", "--scope", "bogus"])
        assert result.exit_code != 0


class TestLiveMode:
    def test_live_table_output_never_contains_raw_token(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
    ) -> None:
        # Mock the chain to return our JWT.
        cred = MagicMock()
        cred.get_token.return_value = AccessToken(fake_jwt, expires_on=int(time.time()) + 3600)
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(200, json={"tenantSettings": []})
        )
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-fabric-automation"}]}
            )
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--scope", "fabric", "--output", "table"])
        assert result.exit_code == 0, result.stdout
        assert fake_jwt not in result.stdout  # raw token NEVER in output
        # Claims summary IS in output
        assert "tid-abc" in result.stdout

    def test_live_json_output_has_no_token_key(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
    ) -> None:
        cred = MagicMock()
        cred.get_token.return_value = AccessToken(fake_jwt, expires_on=int(time.time()) + 3600)
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(200, json={"tenantSettings": [{"name": "a"}]})
        )
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-fabric-automation"}]}
            )
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--output", "json"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert "token" not in data, data
        assert fake_jwt not in result.stdout
        assert data["token_claims"]["tid"] == "tid-abc"

    def test_exit_3_when_token_acquisition_fails(self, runner: CliRunner) -> None:
        cred = MagicMock()
        cred.get_token.side_effect = RuntimeError("no az login")
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--scope", "fabric", "--output", "json"])
        assert result.exit_code == 3

    def test_exit_2_when_tenant_setting_missing(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
    ) -> None:
        cred = MagicMock()
        cred.get_token.return_value = AccessToken(fake_jwt, expires_on=int(time.time()) + 3600)
        # 403 from Fabric admin -> status = blocked -> exit 2
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(403, json={"errorCode": "ApiNotApplicable"})
        )
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-fabric-automation"}]}
            )
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--output", "json"])
        assert result.exit_code == 2
        data = json.loads(result.stdout)
        assert data["tenant_toggles"]["classification"] == "api_not_enabled"

    def test_mismatched_tenant_id_emits_warning(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
    ) -> None:
        cred = MagicMock()
        cred.get_token.return_value = AccessToken(fake_jwt, expires_on=int(time.time()) + 3600)
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(200, json={"tenantSettings": []})
        )
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-fabric-automation"}]}
            )
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--tenant-id", "wrong-tid", "--output", "table"])
        # Token succeeded so exit is 0 (tenant toggles ok, entra ok) but table has a WARNING row.
        assert "WARNING" in result.stdout
        assert fake_jwt not in result.stdout
