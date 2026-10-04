"""Typer CLI smoke tests via CliRunner. Mocks token provider + httpx probes."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from typer.testing import CliRunner

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, GRAPH_AUDIENCE
from sigantry_core.auth.cli import app
from sigantry_core.auth.token_provider import reset_token_provider

_GROUP = "fabric-deployers"


@pytest.fixture(autouse=True)
def _clean_singleton():
    reset_token_provider()
    yield
    reset_token_provider()


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No developer config file or env var may supply an expected group."""
    monkeypatch.chdir(tmp_path)
    for var in ("SIGANTRY_AUTH__EXPECTED_GROUP", "FDT_AUTH__EXPECTED_GROUP"):
        monkeypatch.delenv(var, raising=False)


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
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(
                app, ["--scope", "fabric", "--output", "table", "--expected-group", _GROUP]
            )
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
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--output", "json", "--expected-group", _GROUP])
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
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(app, ["--output", "json", "--expected-group", _GROUP])
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
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
            result = runner.invoke(
                app,
                ["--tenant-id", "wrong-tid", "--output", "table", "--expected-group", _GROUP],
            )
        # Token succeeded so exit is 0 (tenant toggles ok, entra ok) but table has a WARNING row.
        assert "WARNING" in result.stdout
        assert fake_jwt not in result.stdout


def _member_of(respx_router: respx.MockRouter, groups: list[str]) -> respx.Route:
    """Mock both live probes: tenant settings 200, memberOf returning ``groups``."""
    respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
        return_value=httpx.Response(200, json={"tenantSettings": []})
    )
    return respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
        return_value=httpx.Response(200, json={"value": [{"displayName": g} for g in groups]})
    )


def _invoke_live(runner: CliRunner, fake_jwt: str, args: list[str]):
    cred = MagicMock()
    cred.get_token.return_value = AccessToken(fake_jwt, expires_on=int(time.time()) + 3600)
    with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
        return runner.invoke(app, args)


class TestExpectedGroup:
    """Where the expected Entra group comes from, and what happens without one."""

    def test_unconfigured_group_is_reported_skipped_not_ok(
        self, runner: CliRunner, respx_router: respx.MockRouter, fake_jwt: str
    ) -> None:
        route = _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, fake_jwt, ["--output", "json"])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "skipped"
        assert groups["expected"] is None
        assert groups["detail"] == "not checked: no expected group configured"
        assert not route.called

    def test_unconfigured_group_shows_skipped_row_in_table(
        self, runner: CliRunner, respx_router: respx.MockRouter, fake_jwt: str
    ) -> None:
        _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, fake_jwt, ["--output", "table"])
        assert result.exit_code == 0, result.output
        assert "skipped" in result.stdout

    def test_expected_group_from_env_var(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SIGANTRY_AUTH__EXPECTED_GROUP", _GROUP)
        route = _member_of(respx_router, ["some-other-group"])
        result = _invoke_live(runner, fake_jwt, ["--output", "json"])
        assert result.exit_code == 2, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "missing"
        assert groups["expected"] == _GROUP
        assert route.called

    def test_expected_group_from_sigantry_toml(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".sigantry.toml").write_text(
            f'[auth]\nexpected_group = "{_GROUP}"\n', encoding="utf-8"
        )
        _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, fake_jwt, ["--output", "json"])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "ok"
        assert groups["expected"] == _GROUP

    def test_flag_overrides_settings(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SIGANTRY_AUTH__EXPECTED_GROUP", "from-env")
        _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, fake_jwt, ["--output", "json", "--expected-group", _GROUP])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"]["expected"] == _GROUP

    def test_unreadable_settings_file_reports_error_not_skipped(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The env value is lost when the file fails to parse, so the group is
        # unknown, not unset: a configured check must not turn into a skip.
        monkeypatch.setenv("SIGANTRY_AUTH__EXPECTED_GROUP", _GROUP)
        (tmp_path / ".sigantry.toml").write_text("[auth\n", encoding="utf-8")
        route = _member_of(respx_router, [_GROUP])
        with caplog.at_level(logging.WARNING, logger="sigantry_core.auth.cli"):
            result = _invoke_live(runner, fake_jwt, ["--output", "json"])
        assert result.exit_code == 2, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "error"
        assert groups["detail"].startswith("not checked: settings could not be loaded")
        assert not route.called
        assert "Could not load Sigantry settings" in caplog.text

    def test_flag_needs_no_settings_file(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        (tmp_path / ".sigantry.toml").write_text("[auth\n", encoding="utf-8")
        route = _member_of(respx_router, [_GROUP])
        with caplog.at_level(logging.WARNING, logger="sigantry_core.auth.cli"):
            result = _invoke_live(
                runner, fake_jwt, ["--output", "json", "--expected-group", _GROUP]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"]["status"] == "ok"
        assert route.called
        assert "Could not load Sigantry settings" not in caplog.text

    @pytest.mark.parametrize(
        "name",
        ["[prod] deployers", "team[/old]", "g" * 120],
        ids=["markup-tag", "closing-tag", "long"],
    )
    def test_group_name_survives_json_output_exactly(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        name: str,
    ) -> None:
        _member_of(respx_router, [name])
        result = _invoke_live(runner, fake_jwt, ["--output", "json", "--expected-group", name])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["expected"] == name
        assert groups["status"] == "ok"

    @pytest.mark.parametrize("name", ["[prod] deployers", "team[/old]"])
    def test_group_name_survives_table_output(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        fake_jwt: str,
        name: str,
    ) -> None:
        _member_of(respx_router, ["someone-else"])
        result = _invoke_live(runner, fake_jwt, ["--output", "table", "--expected-group", name])
        assert result.exit_code == 2, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert name in result.stdout

    def test_settings_are_not_read_outside_the_fabric_scope(
        self,
        runner: CliRunner,
        fake_jwt: str,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        (tmp_path / ".sigantry.toml").write_text("[auth\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="sigantry_core.auth.cli"):
            result = _invoke_live(runner, fake_jwt, ["--scope", "powerbi", "--output", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"] is None
        assert "Could not load Sigantry settings" not in caplog.text
