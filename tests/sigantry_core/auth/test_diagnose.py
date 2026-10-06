"""Probe functions - classify_http_error, decode_token_claims, check_*."""

from __future__ import annotations

import base64
import json
import warnings

import httpx
import pytest
import respx

from sigantry_core.auth import diagnose as diagnose_module
from sigantry_core.auth.audiences import FABRIC_AUDIENCE, GRAPH_AUDIENCE
from sigantry_core.auth.diagnose import (
    build_report,
    check_entra_group,
    check_tenant_toggles,
    classify_http_error,
    decode_token_claims,
)

_GROUP = "fabric-deployers"

#: JWT payloads that are valid JSON but not an object, one per JSON kind.
#: The string and the array contain a claim name, so a lookup by name reaches
#: them.
_NON_OBJECT_PAYLOADS = pytest.mark.parametrize(
    "payload",
    [None, 5, True, "aud", ["aud"]],
    ids=["null", "number", "boolean", "string", "array"],
)


def _token_with_payload(payload: object) -> str:
    """An unsigned JWT whose middle segment is ``payload`` as JSON."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"e30.{body}.s"


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

    @_NON_OBJECT_PAYLOADS
    def test_payload_that_is_not_an_object_has_no_claims(self, payload: object) -> None:
        assert decode_token_claims(_token_with_payload(payload)) == {}


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
                        {"displayName": _GROUP},
                        {"displayName": "sg-other"},
                    ]
                },
            )
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", expected_group=_GROUP, client=client)
        assert result["status"] == "ok"
        assert _GROUP in result["groups"]
        assert result["expected"] == _GROUP

    def test_missing_when_group_not_found(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": "sg-other"}]})
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", expected_group=_GROUP, client=client)
        assert result["status"] == "missing"

    def test_service_principal_path_with_principal_id(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(
            f"{GRAPH_AUDIENCE}/v1.0/servicePrincipals/oid-xyz/memberOf",
            params={"$select": "displayName"},
        ).mock(return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]}))
        with httpx.Client() as client:
            result = check_entra_group(
                "fake-token", principal_id="oid-xyz", expected_group=_GROUP, client=client
            )
        assert result["status"] == "ok"

    def test_403_is_error(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(f"{GRAPH_AUDIENCE}/v1.0/me/memberOf").mock(
            return_value=httpx.Response(403)
        )
        with httpx.Client() as client:
            result = check_entra_group("fake-token", expected_group=_GROUP, client=client)
        assert result["status"] == "error"

    @pytest.mark.parametrize(
        "kwargs", [{}, {"expected_group": None}, {"expected_group": ""}], ids=str
    )
    def test_check_entra_group_without_group_warns_and_sends_nothing(
        self, respx_router: respx.MockRouter, kwargs: dict[str, str | None]
    ) -> None:
        """sigantry 1.0.0 checked a built-in group here; the caller is told."""
        route = respx_router.get(url__startswith=f"{GRAPH_AUDIENCE}/").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        with (
            httpx.Client() as client,
            pytest.warns(FutureWarning, match=r"without expected_group") as caught,
        ):
            result = check_entra_group("fake-token", client=client, **kwargs)
        assert result["status"] == "skipped"
        assert result["classification"] == "skipped"
        assert result["expected"] is None
        assert route.call_count == 0
        # stacklevel: the warning points at the caller, not at sigantry.
        assert [w.filename for w in caught] == [__file__]

    def test_check_entra_group_with_group_probes_once(self, respx_router: respx.MockRouter) -> None:
        route = respx_router.get(url__startswith=f"{GRAPH_AUDIENCE}/").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        with httpx.Client() as client, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = check_entra_group("fake-token", expected_group=_GROUP, client=client)
        assert result["status"] == "ok"
        assert route.call_count == 1
        assert [str(w.message) for w in caught] == []


class TestExpectedEntraGroupAlias:
    """Importing ``ExpectedEntraGroup`` by name, as code written against 1.0.0 may do.

    Its 1.0.0 value named one deployment's group and does not come back: inside
    ``pytest.warns`` the import emits a ``FutureWarning`` and gives an empty
    string, which ``check_entra_group`` treats as no group.
    """

    def test_expected_entra_group_is_importable_and_warns(self) -> None:
        with pytest.warns(FutureWarning, match=r"ExpectedEntraGroup is deprecated") as caught:
            from sigantry_core.auth.diagnose import ExpectedEntraGroup
        assert ExpectedEntraGroup == ""
        assert isinstance(ExpectedEntraGroup, str)
        assert [w.filename for w in caught] == [__file__]

    def test_unknown_module_attribute_still_raises(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError, match="NoSuchName"):
                _ = diagnose_module.NoSuchName
            with pytest.raises(ImportError):
                from sigantry_core.auth.diagnose import NoSuchName  # noqa: F401
        assert caught == []


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

    def test_skipped_group_check_does_not_degrade(self, make_jwt) -> None:
        rpt = build_report(
            scope="x",
            credential_used="X",
            token=make_jwt({"tid": "t"}),
            tenant_toggles={"status": "ok"},
            entra_groups={"status": "skipped"},
        )
        assert rpt["exit_code"] == 0

    def test_missing_group_degrades(self, make_jwt) -> None:
        rpt = build_report(
            scope="x",
            credential_used="X",
            token=make_jwt({"tid": "t"}),
            tenant_toggles={"status": "ok"},
            entra_groups={"status": "missing"},
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


_ME = f"{GRAPH_AUDIENCE}/v1.0/me/memberOf"
_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"


def _check(token: str, **kwargs):
    with httpx.Client() as client:
        return check_entra_group(token, expected_group=_GROUP, client=client, **kwargs)


class TestCheckEntraGroupGraphToken:
    """Which token reaches Graph, and how a Graph refusal is reported."""

    def test_refuses_a_fabric_token_without_sending_it(
        self, respx_router: respx.MockRouter, make_jwt
    ) -> None:
        route = respx_router.get(url__startswith=f"{GRAPH_AUDIENCE}/").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        result = _check(make_jwt({"aud": FABRIC_AUDIENCE}))
        assert not route.called
        assert result["status"] == "error"
        assert result["classification"] == "wrong_audience"
        assert FABRIC_AUDIENCE in result["detail"]

    @pytest.mark.parametrize("aud", [GRAPH_AUDIENCE, f"{GRAPH_AUDIENCE}/", _GRAPH_APP_ID])
    def test_sends_a_graph_token(self, respx_router: respx.MockRouter, make_jwt, aud) -> None:
        token = make_jwt({"aud": aud})
        route = respx_router.get(_ME).mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        result = _check(token)
        assert route.call_count == 1
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {token}"
        assert result["status"] == "ok"
        assert result["classification"] == "ok"

    def test_refuses_a_list_audience_that_names_no_graph_resource(
        self, respx_router: respx.MockRouter, make_jwt
    ) -> None:
        # ``aud`` may be a list; it is decided like a string, never a crash.
        route = respx_router.get(url__startswith=f"{GRAPH_AUDIENCE}/").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        result = _check(make_jwt({"aud": [FABRIC_AUDIENCE, "api://other"]}))
        assert not route.called
        assert result["classification"] == "wrong_audience"

    def test_sends_a_list_audience_that_includes_graph(
        self, respx_router: respx.MockRouter, make_jwt
    ) -> None:
        route = respx_router.get(_ME).mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        result = _check(make_jwt({"aud": [FABRIC_AUDIENCE, GRAPH_AUDIENCE]}))
        assert route.call_count == 1
        assert result["classification"] == "ok"

    @_NON_OBJECT_PAYLOADS
    def test_sends_a_token_whose_payload_is_not_an_object(
        self, respx_router: respx.MockRouter, payload: object
    ) -> None:
        # Such a payload carries no ``aud``, which decides nothing.
        token = _token_with_payload(payload)
        route = respx_router.get(_ME).mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        result = _check(token)
        assert route.call_count == 1
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {token}"
        assert result["status"] == "ok"
        assert result["classification"] == "ok"

    def test_401_is_token_rejected(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(_ME).mock(return_value=httpx.Response(401))
        result = _check("fake-token")
        assert result["status"] == "error"
        assert result["classification"] == "token_rejected"
        assert "Microsoft Graph" in result["detail"]

    @pytest.mark.parametrize(
        ("principal_id", "permission"),
        [(None, "User.Read"), ("oid-xyz", "Application.Read.All")],
        ids=["me", "service-principal"],
    )
    def test_403_names_the_missing_graph_permission(
        self, respx_router: respx.MockRouter, principal_id, permission
    ) -> None:
        respx_router.get(url__startswith=f"{GRAPH_AUDIENCE}/").mock(
            return_value=httpx.Response(
                403,
                json={"error": {"code": "Authorization_RequestDenied", "message": "x"}},
            )
        )
        result = _check("fake-token", principal_id=principal_id)
        assert result["status"] == "error"
        assert result["classification"] == "permission_denied"
        assert permission in result["detail"]
        assert "not in" not in result["detail"]

    def test_app_only_token_on_me_points_at_principal_id(
        self, respx_router: respx.MockRouter
    ) -> None:
        respx_router.get(_ME).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "code": "Request_BadRequest",
                        "message": "/me request is only valid with delegated authentication flow.",
                    }
                },
            )
        )
        result = _check("fake-token")
        assert result["status"] == "error"
        assert result["classification"] == "delegated_only"
        assert "--principal-id" in result["detail"]

    def test_other_400_is_not_called_delegated_only(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(_ME).mock(return_value=httpx.Response(400, json={}))
        result = _check("fake-token")
        assert result["status"] == "error"
        assert result["classification"] == "other"

    def test_unnamed_memberships_are_not_reported_as_missing(
        self, respx_router: respx.MockRouter
    ) -> None:
        # Graph returns id and type only for objects the caller may not read.
        respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {"@odata.type": "#microsoft.graph.group", "id": "g1", "displayName": None},
                        {"displayName": "sg-other"},
                    ]
                },
            )
        )
        result = _check("fake-token")
        assert result["status"] == "error"
        assert result["classification"] == "names_hidden"
        assert "GroupMember.Read.All" in result["detail"]

    def test_a_named_match_wins_over_unnamed_entries(self, respx_router: respx.MockRouter) -> None:
        respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200, json={"value": [{"id": "g1", "displayName": None}, {"displayName": _GROUP}]}
            )
        )
        assert _check("fake-token")["status"] == "ok"

    def test_unnamed_entries_of_other_types_do_not_hide_the_answer(
        self, respx_router: respx.MockRouter
    ) -> None:
        # Only a group can be the expected group. A directory role or an
        # administrative unit the token may not read comes back without a name
        # and is dropped, as in 1.0.0.
        respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {"@odata.type": "#microsoft.graph.directoryRole", "id": "r1"},
                        {"@odata.type": "#microsoft.graph.administrativeUnit", "id": "a1"},
                        {"@odata.type": "#microsoft.graph.group", "displayName": "sg-other"},
                    ]
                },
            )
        )
        result = _check("fake-token")
        assert result["status"] == "missing"
        assert result["classification"] == "missing"

    def test_names_hidden_detail_states_no_count(self, respx_router: respx.MockRouter) -> None:
        # Unnamed entries that cannot be a group are dropped, so a count of the
        # ones kept would be smaller than the number Graph returned without a name.
        respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {"@odata.type": "#microsoft.graph.group", "id": "g1"},
                        {"@odata.type": "#microsoft.graph.directoryRole", "id": "r1"},
                        {"@odata.type": "#microsoft.graph.group", "displayName": "sg-other"},
                    ]
                },
            )
        )
        result = _check("fake-token")
        assert result["classification"] == "names_hidden"
        assert "membership(s) without a name" in result["detail"]
        assert not any(ch.isdigit() for ch in result["detail"])

    def test_follows_next_link_to_a_later_page(self, respx_router: respx.MockRouter) -> None:
        page2 = f"{GRAPH_AUDIENCE}/v1.0/me/memberOf?$skiptoken=abc"
        respx_router.get(page2).mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        respx_router.get(_ME, params={"$select": "displayName"}).mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-other"}], "@odata.nextLink": page2}
            )
        )
        result = _check("fake-token")
        assert result["status"] == "ok"
        assert result["groups"] == ["sg-other", _GROUP]

    def test_a_later_page_that_cannot_be_read_is_an_error(
        self, respx_router: respx.MockRouter
    ) -> None:
        page2 = f"{GRAPH_AUDIENCE}/v1.0/me/memberOf?$skiptoken=abc"
        respx_router.get(page2).mock(side_effect=httpx.ConnectError("refused"))
        respx_router.get(_ME, params={"$select": "displayName"}).mock(
            return_value=httpx.Response(
                200, json={"value": [{"displayName": "sg-other"}], "@odata.nextLink": page2}
            )
        )
        result = _check("fake-token")
        assert result["status"] == "error"
        assert result["classification"] == "other"
        assert result["groups"] == ["sg-other"]

    def test_a_first_page_that_cannot_be_read_raises_as_in_1_0_0(
        self, respx_router: respx.MockRouter
    ) -> None:
        respx_router.get(_ME).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(httpx.ConnectError):
            _check("fake-token")

    def test_never_follows_a_next_link_off_graph(self, respx_router: respx.MockRouter) -> None:
        foreign = respx_router.get(url__startswith="https://example.invalid/").mock(
            return_value=httpx.Response(200, json={"value": [{"displayName": _GROUP}]})
        )
        respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [{"displayName": "sg-other"}],
                    "@odata.nextLink": "https://example.invalid/v1.0/me/memberOf?page=2",
                },
            )
        )
        result = _check("fake-token")
        assert not foreign.called
        assert result["status"] == "error"
        assert result["classification"] == "other"

    def test_a_next_link_loop_ends_as_incomplete(self, respx_router: respx.MockRouter) -> None:
        route = respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [{"displayName": "sg-other"}],
                    "@odata.nextLink": f"{_ME}?$skiptoken=again",
                },
            )
        )
        result = _check("fake-token")
        assert result["status"] == "error"
        assert result["classification"] == "incomplete"
        assert 1 < route.call_count <= 100

    def test_incomplete_detail_speaks_only_of_named_memberships(
        self, respx_router: respx.MockRouter
    ) -> None:
        # An unnamed entry may be the group, so the detail must not say it is absent.
        pages = diagnose_module._MAX_MEMBER_OF_PAGES
        route = respx_router.get(_ME).mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {"@odata.type": "#microsoft.graph.group", "id": "g1", "displayName": None},
                        {"displayName": "sg-other"},
                    ],
                    "@odata.nextLink": f"{_ME}?$skiptoken=again",
                },
            )
        )
        result = _check("fake-token")
        assert result["classification"] == "incomplete"
        assert route.call_count == pages
        assert result["detail"] == (
            f"not decided: none of the named memberships on the first {pages} pages "
            f"is {_GROUP}, and Microsoft Graph returned a link to more pages"
        )
