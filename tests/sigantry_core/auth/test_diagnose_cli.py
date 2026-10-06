"""Typer CLI smoke tests via CliRunner. Mocks token provider + httpx probes."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from typing import TextIO
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from typer.testing import CliRunner

from sigantry_core.auth.audiences import (
    FABRIC_AUDIENCE,
    FABRIC_SCOPE,
    GRAPH_AUDIENCE,
    GRAPH_SCOPE,
)
from sigantry_core.auth.cli import app
from sigantry_core.auth.token_provider import reset_token_provider
from sigantry_core.config import _CONFIG_FILENAME, _LEGACY_CONFIG_FILENAME

_GROUP = "fabric-deployers"
_AUDIENCE_BY_SCOPE = {FABRIC_SCOPE: FABRIC_AUDIENCE, GRAPH_SCOPE: GRAPH_AUDIENCE}


class _ScopedCredential:
    """A credential that issues a different token per scope, and records every request.

    Each token carries the audience of the scope it was requested for, the
    way a real token does, so a test can tell from the bearer value alone
    which resource a request was authorised for. Scopes in ``fail_scopes``
    raise instead, as a credential does when it cannot get that token.
    """

    def __init__(self, make_jwt, *, fail_scopes: tuple[str, ...] = ()) -> None:
        self._make_jwt = make_jwt
        self._fail_scopes = fail_scopes
        self.scopes: list[str] = []
        self.tokens: dict[str, str] = {}

    def get_token(self, *scopes: str, **_kwargs: object) -> AccessToken:
        scope = scopes[0]
        self.scopes.append(scope)
        if scope in self._fail_scopes:
            raise RuntimeError(f"no token for {scope}")
        token = self._make_jwt(
            {
                "aud": _AUDIENCE_BY_SCOPE.get(scope, scope.removesuffix("/.default")),
                "tid": "tid-abc",
                "oid": "oid-123",
                "appid": "app-456",
                "exp": int(time.time()) + 3600,
            }
        )
        self.tokens[scope] = token
        return AccessToken(token, expires_on=int(time.time()) + 3600)

    def audience_of(self, bearer: str) -> str | None:
        """Return the audience of a token this credential issued, else None."""
        for scope, token in self.tokens.items():
            if token == bearer:
                return _AUDIENCE_BY_SCOPE.get(scope)
        return None


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
def cred(make_jwt) -> _ScopedCredential:
    # One token per scope, stable claims - no raw secrets.
    return _ScopedCredential(make_jwt)


def _no_token_in(text: str, cred: _ScopedCredential) -> bool:
    return bool(cred.tokens) and not any(t in text for t in cred.tokens.values())


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
        cred: _ScopedCredential,
    ) -> None:
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
        assert _no_token_in(result.stdout, cred)  # raw token NEVER in output
        # Claims summary IS in output
        assert "tid-abc" in result.stdout

    def test_live_json_output_has_no_token_key(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
    ) -> None:
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
        assert _no_token_in(result.stdout, cred)
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
        cred: _ScopedCredential,
    ) -> None:
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
        cred: _ScopedCredential,
    ) -> None:
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
        assert _no_token_in(result.stdout, cred)


def _member_of(respx_router: respx.MockRouter, groups: list[str]) -> respx.Route:
    """Mock both live probes: tenant settings 200, memberOf returning ``groups``."""
    respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
        return_value=httpx.Response(200, json={"tenantSettings": []})
    )
    return respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
        return_value=httpx.Response(200, json={"value": [{"displayName": g} for g in groups]})
    )


def _invoke_live(runner: CliRunner, cred: _ScopedCredential, args: list[str]):
    with patch("sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred):
        return runner.invoke(app, args)


class TestExpectedGroup:
    """Where the expected Entra group comes from, and what happens without one."""

    def test_unconfigured_group_is_reported_skipped_not_ok(
        self, runner: CliRunner, respx_router: respx.MockRouter, cred: _ScopedCredential
    ) -> None:
        route = _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "skipped"
        assert groups["expected"] is None
        assert groups["detail"].startswith(
            "not checked: neither --expected-group nor the loaded settings named a group"
        )
        assert not route.called

    def test_unconfigured_group_shows_skipped_row_in_table(
        self, runner: CliRunner, respx_router: respx.MockRouter, cred: _ScopedCredential
    ) -> None:
        _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, cred, ["--output", "table"])
        assert result.exit_code == 0, result.output
        assert "skipped" in result.stdout

    def test_expected_group_from_env_var(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SIGANTRY_AUTH__EXPECTED_GROUP", _GROUP)
        route = _member_of(respx_router, ["some-other-group"])
        result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exit_code == 2, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "missing"
        assert groups["expected"] == _GROUP
        assert route.called

    def test_expected_group_from_sigantry_toml(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".sigantry.toml").write_text(
            f'[auth]\nexpected_group = "{_GROUP}"\n', encoding="utf-8"
        )
        _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "ok"
        assert groups["expected"] == _GROUP

    def test_flag_overrides_settings(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SIGANTRY_AUTH__EXPECTED_GROUP", "from-env")
        _member_of(respx_router, [_GROUP])
        result = _invoke_live(runner, cred, ["--output", "json", "--expected-group", _GROUP])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"]["expected"] == _GROUP

    def test_unreadable_settings_file_reports_error_not_skipped(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
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
            result = _invoke_live(runner, cred, ["--output", "json"])
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
        cred: _ScopedCredential,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        (tmp_path / ".sigantry.toml").write_text("[auth\n", encoding="utf-8")
        route = _member_of(respx_router, [_GROUP])
        with caplog.at_level(logging.WARNING, logger="sigantry_core.auth.cli"):
            result = _invoke_live(runner, cred, ["--output", "json", "--expected-group", _GROUP])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"]["status"] == "ok"
        assert route.called
        assert "Could not load Sigantry settings" not in caplog.text

    @pytest.mark.parametrize(
        "name",
        ["[prod] deployers", "team[/old]", "g" * 120, "ops:fire:", "sg:x:deployers"],
        ids=["markup-tag", "closing-tag", "long", "emoji-code", "emoji-code-inside"],
    )
    def test_group_name_survives_json_output_exactly(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        name: str,
    ) -> None:
        _member_of(respx_router, [name])
        result = _invoke_live(runner, cred, ["--output", "json", "--expected-group", name])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["expected"] == name
        assert groups["status"] == "ok"

    @pytest.mark.parametrize("name", ["[prod] deployers", "team[/old]", "ops:fire:", "a:ok:b"])
    def test_group_name_survives_table_output(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        name: str,
    ) -> None:
        _member_of(respx_router, ["someone-else"])
        result = _invoke_live(runner, cred, ["--output", "table", "--expected-group", name])
        assert result.exit_code == 2, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert name in result.stdout

    def test_graph_group_names_survive_json_output_exactly(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
    ) -> None:
        returned = [_GROUP, "other:ok:group", "[x] y"]
        _member_of(respx_router, returned)
        result = _invoke_live(runner, cred, ["--output", "json", "--expected-group", _GROUP])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"]["groups"] == returned

    def test_settings_are_not_read_outside_the_fabric_scope(
        self,
        runner: CliRunner,
        cred: _ScopedCredential,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        (tmp_path / ".sigantry.toml").write_text("[auth\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="sigantry_core.auth.cli"):
            result = _invoke_live(runner, cred, ["--scope", "powerbi", "--output", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"] is None
        assert "Could not load Sigantry settings" not in caplog.text


def _recording_probes(
    respx_router: respx.MockRouter,
    *,
    groups: tuple[str, ...] = (_GROUP,),
    graph_response: httpx.Response | None = None,
) -> dict[str, list[str]]:
    """Mock both probes and record the bearer token each one received."""
    seen: dict[str, list[str]] = {"fabric": [], "graph": []}

    def _bearer(request: httpx.Request) -> str:
        return request.headers.get("Authorization", "").removeprefix("Bearer ")

    def fabric(request: httpx.Request) -> httpx.Response:
        seen["fabric"].append(_bearer(request))
        return httpx.Response(200, json={"tenantSettings": []})

    def graph(request: httpx.Request) -> httpx.Response:
        seen["graph"].append(_bearer(request))
        if graph_response is not None:
            return graph_response
        return httpx.Response(200, json={"value": [{"displayName": g} for g in groups]})

    respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(side_effect=fabric)
    respx_router.get(url__startswith=f"{GRAPH_AUDIENCE}/").mock(side_effect=graph)
    return seen


class TestGraphToken:
    """Microsoft Graph receives only the Graph token, never the Fabric one."""

    @pytest.mark.parametrize(
        "extra", [[], ["--principal-id", "oid-sp"]], ids=["me", "service-principal"]
    )
    def test_group_check_requests_a_graph_token(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        extra: list[str],
    ) -> None:
        _recording_probes(respx_router)
        result = _invoke_live(
            runner, cred, ["--output", "json", "--expected-group", _GROUP, *extra]
        )
        assert result.exit_code == 0, result.output
        assert FABRIC_SCOPE in cred.scopes
        assert GRAPH_SCOPE in cred.scopes
        assert json.loads(result.stdout)["entra_groups"]["status"] == "ok"

    @pytest.mark.parametrize(
        "extra", [[], ["--principal-id", "oid-sp"]], ids=["me", "service-principal"]
    )
    def test_fabric_token_is_never_sent_to_graph(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        extra: list[str],
    ) -> None:
        seen = _recording_probes(respx_router)
        result = _invoke_live(
            runner, cred, ["--output", "json", "--expected-group", _GROUP, *extra]
        )
        assert result.exit_code == 0, result.output
        assert seen["graph"], "the group check sent no request to Graph"
        assert cred.tokens[FABRIC_SCOPE] not in seen["graph"]
        assert [cred.audience_of(t) for t in seen["graph"]] == [GRAPH_AUDIENCE] * len(seen["graph"])
        assert [cred.audience_of(t) for t in seen["fabric"]] == [FABRIC_AUDIENCE]

    def test_no_graph_token_is_requested_when_the_check_is_skipped(
        self, runner: CliRunner, respx_router: respx.MockRouter, cred: _ScopedCredential
    ) -> None:
        seen = _recording_probes(respx_router)
        result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exit_code == 0, result.output
        assert cred.scopes == [FABRIC_SCOPE]
        assert seen["graph"] == []

    def test_no_graph_token_is_reported_as_unavailable_not_missing(
        self, runner: CliRunner, respx_router: respx.MockRouter, make_jwt
    ) -> None:
        cred = _ScopedCredential(make_jwt, fail_scopes=(GRAPH_SCOPE,))
        seen = _recording_probes(respx_router)
        result = _invoke_live(runner, cred, ["--output", "json", "--expected-group", _GROUP])
        assert result.exit_code == 2, result.output
        data = json.loads(result.stdout)
        groups = data["entra_groups"]
        assert groups["status"] == "error"
        assert groups["classification"] == "token_unavailable"
        assert GRAPH_SCOPE in groups["detail"]
        assert seen["graph"] == []
        # The Fabric token still worked: this is a degraded run, not a broken one.
        assert data["tenant_toggles"]["status"] == "ok"

    @pytest.mark.parametrize(
        ("status", "classification"),
        [(401, "token_rejected"), (403, "permission_denied")],
    )
    def test_graph_refusal_is_not_reported_as_a_missing_membership(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        status: int,
        classification: str,
    ) -> None:
        body = {"error": {"code": "Authorization_RequestDenied", "message": "Insufficient"}}
        _recording_probes(respx_router, graph_response=httpx.Response(status, json=body))
        result = _invoke_live(runner, cred, ["--output", "json", "--expected-group", _GROUP])
        assert result.exit_code == 2, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "error"
        assert groups["classification"] == classification
        assert "Microsoft Graph" in groups["detail"]
        assert "not in" not in groups["detail"]


# ---------------------------------------------------------------------------
# sigantry 1.0.0 compatibility (1.0.1)
# ---------------------------------------------------------------------------

#: The detail of a skipped group check, word for word: it says that the 1.0.0
#: built-in group is gone.
_SKIPPED_DETAIL = (
    "not checked: neither --expected-group nor the loaded settings named a group. "
    "sigantry 1.0.0 checked a built-in group name"
)


@pytest.fixture
def settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No settings variable from the host; the working directory is ``tmp_path``."""
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith(("FDT_", "SIGANTRY_")) or upper in {"TENANT_ID", "EXPECTED_GROUP"}:
            monkeypatch.delenv(name)
    return tmp_path


def _print_warnings_to_stderr(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: TextIO | None = None,
    line: str | None = None,
) -> None:
    """Print a warning the way Python does outside pytest, which records them."""
    sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))


class TestUpgradeFrom100:
    """Runs with no group set, or with 1.0.0's legacy config file and env names.

    The skip is explained in the report and stderr stays empty, for a pipeline
    step set to fail on stderr output; warnings from loading the settings
    neither raise under warnings-as-errors nor reach stderr.
    """

    def test_unconfigured_run_explains_skip_in_detail_and_keeps_stderr_empty(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        settings_env: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _member_of(respx_router, [_GROUP])
        with caplog.at_level(logging.WARNING):
            result = _invoke_live(runner, cred, ["--output", "json"])
            table = _invoke_live(runner, cred, ["--output", "table"])
        assert result.exit_code == 0, result.output
        groups = json.loads(result.stdout)["entra_groups"]
        assert groups["status"] == "skipped"
        assert groups["classification"] == "skipped"
        assert groups["detail"] == _SKIPPED_DETAIL
        assert table.exit_code == 0, table.output
        assert "--expected-group" in table.stdout
        assert result.stderr == ""
        assert table.stderr == ""
        # Under pytest a logged warning goes to caplog, not to stderr.
        assert [
            r.getMessage()
            for r in caplog.records
            if r.name.startswith("sigantry_core") and r.levelno >= logging.WARNING
        ] == []

    def test_cli_unconfigured_does_not_emit_library_warning(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        settings_env: Path,
    ) -> None:
        _member_of(respx_router, [_GROUP])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exit_code == 0, result.output
        assert [str(w.message) for w in caught if "expected_group" in str(w.message)] == []

    @pytest.mark.parametrize("configured", [True, False], ids=["group-in-legacy-file", "no-group"])
    def test_legacy_config_file_under_warnings_as_errors_does_not_crash(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        settings_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        configured: bool,
    ) -> None:
        """``PYTHONWARNINGS=error``: the settings warnings may not stop the run."""
        body = f'[auth]\nexpected_group = "{_GROUP}"\n' if configured else "[core]\n"
        (settings_env / _LEGACY_CONFIG_FILENAME).write_text(body, encoding="utf-8")
        monkeypatch.setenv("FDT_CORE__TENANT_ID", "t-env")
        _member_of(respx_router, [_GROUP])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exception is None or isinstance(result.exception, SystemExit), repr(
            result.exception
        )
        data = json.loads(result.stdout)
        assert result.exit_code == data["exit_code"] == 0
        groups = data["entra_groups"]
        assert groups["status"] == ("ok" if configured else "skipped")
        assert groups["expected"] == (_GROUP if configured else None)

    def test_settings_warnings_are_recorded_not_printed(
        self,
        runner: CliRunner,
        respx_router: respx.MockRouter,
        cred: _ScopedCredential,
        settings_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (settings_env / _LEGACY_CONFIG_FILENAME).write_text(
            f'[auth]\nexpected_group = "{_GROUP}"\n', encoding="utf-8"
        )
        (settings_env / _CONFIG_FILENAME).write_text("[core]\n", encoding="utf-8")
        monkeypatch.setenv("FDT_CORE__TENANT_ID", "t1")
        monkeypatch.setenv("SIGANTRY_CORE__TENANT_ID", "t2")
        monkeypatch.setenv("TENANT_ID", "unprefixed")
        _member_of(respx_router, [_GROUP])
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.showwarning = _print_warnings_to_stderr
            result = _invoke_live(runner, cred, ["--output", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["entra_groups"]["status"] == "ok"
        assert result.stderr == ""
