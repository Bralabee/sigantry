"""Probe functions - classify_http_error, decode_token_claims, check_*."""

from __future__ import annotations

import httpx
import pytest
import respx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, GRAPH_AUDIENCE
from sigantry_core.auth.diagnose import (
    ExpectedEntraGroup,
    build_report,
    check_entra_group,
    check_tenant_toggles,
    classify_http_error,
    decode_token_claims,
)


class TestClassifyHttpError:
    def test_200_is_ok(self) -> None:
        assert classify_http_error(httpx.Response(200)) == "ok"

    def test_401_is_token_rejected(self) -> None:
        assert classify_http_error(httpx.Response(401)) == "token_rejected"

    def test_403_is_api_not_enabled(self) -> None:
        assert classify_http_error(httpx.Response(403)) == "api_not_enabled"

    @pytest.mark.parametrize("status", [404, 409, 429, 500, 502, 504])
    def test_other_statuses_bucket_to_other(self, status: int) -> None:
        assert classify_http_error(httpx.Response(status)) == "other"


class TestDecodeTokenClaims:
    def test_returns_subset_of_whitelisted_claims(self, make_jwt) -> None:
        tok = make_jwt(
            {
                "aud": "https://api.fabric.microsoft.com",
                "iss": "https://sts.windows.net/tid/",
                "tid": "tid-xyz",
                "oid": "oid-123",
                "appid": "app-456",
                "exp": 9999999999,
                "extra_leak": "should-not-surface",
            }
        )
        claims = decode_token_claims(tok)
        assert claims["aud"] == "https://api.fabric.microsoft.com"
        assert claims["tid"] == "tid-xyz"
        assert claims["oid"] == "oid-123"
        assert claims["appid"] == "app-456"
        assert "extra_leak" not in claims

    def test_malformed_token_returns_empty(self) -> None:
        assert decode_token_claims("not-a-jwt") == {}
        assert decode_token_claims("") == {}
        assert decode_token_claims("aaa.bbb") == {}  # bbb isn't valid b64 json


class TestCheckTenantToggles:
    def test_ok_counts_toggles(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(
                200, json={"tenantSettings": [{"name": "a"}, {"name": "b"}]}
            )
        )
        with httpx.Client() as client:
            result = check_tenant_toggles("fake-token", client=client)
        assert result["status"] == "ok"
        assert result["toggles_visible"] == 2
        assert result["classification"] == "ok"

    def test_401_is_degraded_token_rejected(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(401)
        )
        with httpx.Client() as client:
            result = check_tenant_toggles("fake-token", client=client)
        assert result["status"] == "degraded"
        assert result["classification"] == "token_rejected"

    def test_403_is_blocked_api_not_enabled(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(403, json={"errorCode": "ApiNotApplicable"})
        )
        with httpx.Client() as client:
            result = check_tenant_toggles("fake-token", client=client)
        assert result["status"] == "blocked"
        assert result["classification"] == "api_not_enabled"

    def test_500_is_blocked_other(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings").mock(
            return_value=httpx.Response(500)
        )
        with httpx.Client() as client:
            result = check_tenant_toggles("fake-token", client=client)
        assert result["status"] == "blocked"
        assert result["classification"] == "other"


class TestCheckEntraGroup:
    def test_ok_when_expected_group_present(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {"displayName": "sg-fabric-automation"},
                        {"displayName": "sg-other"},
                    ]
                },
            )
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", client=client)
        assert result["status"] == "ok"
        assert ExpectedEntraGroup in result["groups"]

    def test_missing_when_group_not_found(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": "sg-other"}]})
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", client=client)
        assert result["status"] == "missing"

    def test_service_principal_path_with_principal_id(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(
            f"{GRAPH_AUDIENCE}/v1.0/servicePrincipals/oid-xyz/memberOf",
            params={"$select": "displayName"},
        ).mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-fabric-automation"}]}
            )
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", principal_id="oid-xyz", client=client)
        assert result["status"] == "ok"

    def test_403_is_error(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(403)
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", client=client)
        assert result["status"] == "error"


class TestBuildReport:
    def test_exit_code_0_when_all_ok(self, make_jwt) -> None:
        tok = make_jwt({"aud": "fab", "tid": "t"})
        rpt = build_report(
            scope="x",
            credential_used="AzureCliCredential",
            token=tok,
            tenant_toggles={"status": "ok"},
            entra_groups={"status": "ok"},
        )
        assert rpt["exit_code"] == 0

    def test_exit_code_2_when_tenant_blocked(self, make_jwt) -> None:
        tok = make_jwt({"tid": "t"})
        rpt = build_report(
            scope="x",
            credential_used="EnvironmentCredential",
            token=tok,
            tenant_toggles={"status": "blocked"},
            entra_groups={"status": "ok"},
        )
        assert rpt["exit_code"] == 2

    def test_exit_code_3_when_no_token(self) -> None:
        rpt = build_report(
            scope="x",
            credential_used=None,
            token=None,
            tenant_toggles=None,
            entra_groups=None,
        )
        assert rpt["exit_code"] == 3

    def test_report_never_contains_raw_token(self, make_jwt) -> None:
        tok = make_jwt({"tid": "t"})
        rpt = build_report(
            scope="x",
            credential_used="X",
            token=tok,
            tenant_toggles={"status": "ok"},
            entra_groups={"status": "ok"},
        )
        import json as _json

        serialised = _json.dumps(rpt)
        assert tok not in serialised
        assert "token" not in rpt  # no raw token key
