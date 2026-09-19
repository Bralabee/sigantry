"""Unit tests for FabricArmRestClient (Plan 03-02 Task 1, WKSP-05 prerequisite).

Covers:
- Default base_url (https://management.azure.com) and default_scope (AZURE_RM_SCOPE)
- Subclass relationship to BaseRestClient
- from_defaults() wires get_token_provider and threads tenant_id
- send_arm_lro() api-version default 2023-11-01 + caller override
- send_arm_lro() 200/201 inline returns body without polling
- send_arm_lro() 202 prefers Azure-AsyncOperation over Location (Pitfall 7)
- send_arm_lro() 202 falls back to Location when Azure-AsyncOperation absent
- send_arm_lro() 202 without either raises LROTimeoutError (last_status=missing_polling_url)
- send_arm_lro() parses Retry-After from initial 202 and threads into poll_operation
- send_arm_lro() retry_after absent -> defaults to 3s
- send_arm_lro() propagates LROTimeoutError / OperationFailedError from poll_operation
- send_arm_lro() BaseRestClient.send calls TokenProvider.get_token(AZURE_RM_SCOPE)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from sigantry_core.auth.audiences import AZURE_RM_SCOPE
from sigantry_core.client import FabricArmRestClient
from sigantry_core.client.arm import (
    ARM_CAPACITIES_API_VERSION,
    ARM_DEFAULT_BASE_URL,
)
from sigantry_core.client.base import BaseRestClient
from sigantry_core.client.errors import LROTimeoutError, OperationFailedError


def _make_client(mock_token_provider: MagicMock) -> FabricArmRestClient:
    return FabricArmRestClient(token_provider=mock_token_provider)


def _suspend_path() -> str:
    return "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Fabric/capacities/cap/suspend"


def _suspend_url() -> str:
    return f"{ARM_DEFAULT_BASE_URL}{_suspend_path()}"


class TestConstruction:
    def test_is_subclass_of_base_rest_client(self) -> None:
        assert issubclass(FabricArmRestClient, BaseRestClient)

    def test_defaults_base_url_and_scope(self, mock_token_provider: MagicMock) -> None:
        client = FabricArmRestClient(token_provider=mock_token_provider)
        assert client._base_url == ARM_DEFAULT_BASE_URL
        assert client._default_scope == AZURE_RM_SCOPE

    def test_constants(self) -> None:
        assert ARM_DEFAULT_BASE_URL == "https://management.azure.com"
        assert ARM_CAPACITIES_API_VERSION == "2023-11-01"

    def test_from_defaults_wires_token_provider(self) -> None:
        fake_tp = MagicMock()
        with patch(
            "sigantry_core.client.arm.get_token_provider",
            return_value=fake_tp,
        ) as gtp:
            client = FabricArmRestClient.from_defaults(tenant_id="t1")
            gtp.assert_called_once_with(tenant_id="t1")
            assert isinstance(client, FabricArmRestClient)
            assert client._tp is fake_tp
            assert client._base_url == ARM_DEFAULT_BASE_URL
            assert client._default_scope == AZURE_RM_SCOPE

    def test_from_defaults_no_tenant(self) -> None:
        fake_tp = MagicMock()
        with patch(
            "sigantry_core.client.arm.get_token_provider",
            return_value=fake_tp,
        ) as gtp:
            FabricArmRestClient.from_defaults()
            gtp.assert_called_once_with(tenant_id=None)


class TestApiVersion:
    def test_api_version_default_2023_11_01(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["api_version"] = request.url.params.get("api-version", "")
            return httpx.Response(200, json={"ok": True})

        respx_router.post(_suspend_url()).mock(side_effect=_capture)
        client = _make_client(mock_token_provider)
        client.send_arm_lro("POST", _suspend_path())
        assert captured["api_version"] == ARM_CAPACITIES_API_VERSION

    def test_api_version_override(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["api_version"] = request.url.params.get("api-version", "")
            return httpx.Response(200, json={"ok": True})

        respx_router.post(_suspend_url()).mock(side_effect=_capture)
        client = _make_client(mock_token_provider)
        client.send_arm_lro("POST", _suspend_path(), api_version="2024-01-01")
        assert captured["api_version"] == "2024-01-01"


class TestInlineResponses:
    def test_inline_200_returns_body_no_poll(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(200, json={"result": "already-paused"})
        )
        client = _make_client(mock_token_provider)
        with patch("sigantry_core.client.arm.poll_operation") as poll:
            out = client.send_arm_lro("POST", _suspend_path())
        assert out == {"result": "already-paused"}
        poll.assert_not_called()

    def test_inline_201_returns_body_no_poll(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        respx_router.post(_suspend_url()).mock(return_value=httpx.Response(201, json={"id": "c1"}))
        client = _make_client(mock_token_provider)
        with patch("sigantry_core.client.arm.poll_operation") as poll:
            out = client.send_arm_lro("POST", _suspend_path())
        assert out == {"id": "c1"}
        poll.assert_not_called()


class TestLROHeaderPreference:
    def test_prefers_azure_async_operation_over_location(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        async_url = (
            "https://management.azure.com/subscriptions/s"
            "/providers/Microsoft.Fabric/locations/westeurope/operations/op-abc"
        )
        loc_url = (
            "https://management.azure.com/subscriptions/s"
            "/providers/Microsoft.Fabric/locations/westeurope/opsResult/op-abc"
        )
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(
                202,
                headers={
                    "Azure-AsyncOperation": async_url,
                    "Location": loc_url,
                    "Retry-After": "5",
                },
                json={},
            )
        )
        client = _make_client(mock_token_provider)
        with patch(
            "sigantry_core.client.arm.poll_operation",
            return_value={"status": "Succeeded"},
        ) as poll:
            result = client.send_arm_lro("POST", _suspend_path())
        poll.assert_called_once()
        kwargs = poll.call_args.kwargs
        assert kwargs["state_url"] == async_url
        assert kwargs["operation_id"] == async_url
        assert result == {"status": "Succeeded"}

    def test_location_fallback_when_azure_async_missing(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        loc_url = (
            "https://management.azure.com/subscriptions/s"
            "/providers/Microsoft.Fabric/locations/westeurope/opsResult/op-abc"
        )
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(
                202,
                headers={"Location": loc_url, "Retry-After": "1"},
                json={},
            )
        )
        client = _make_client(mock_token_provider)
        with patch("sigantry_core.client.arm.poll_operation", return_value=None) as poll:
            client.send_arm_lro("POST", _suspend_path())
        assert poll.call_args.kwargs["state_url"] == loc_url
        assert poll.call_args.kwargs["operation_id"] == loc_url

    def test_no_polling_url_raises_lro_timeout(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(202, headers={}, json={})
        )
        client = _make_client(mock_token_provider)
        with pytest.raises(LROTimeoutError) as exc:
            client.send_arm_lro("POST", _suspend_path())
        assert exc.value.last_status == "missing_polling_url"
        assert exc.value.elapsed_seconds == 0.0
        assert exc.value.operation_id == "<arm-no-polling-url>"


class TestRetryAfter:
    def test_retry_after_parsed_from_initial_response(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        async_url = "https://management.azure.com/operations/op-abc"
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(
                202,
                headers={"Azure-AsyncOperation": async_url, "Retry-After": "7"},
                json={},
            )
        )
        client = _make_client(mock_token_provider)
        with patch("sigantry_core.client.arm.poll_operation", return_value=None) as poll:
            client.send_arm_lro("POST", _suspend_path())
        assert poll.call_args.kwargs["initial_retry_after"] == 7.0

    def test_retry_after_absent_defaults_to_3s(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        async_url = "https://management.azure.com/operations/op-abc"
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(202, headers={"Azure-AsyncOperation": async_url}, json={})
        )
        client = _make_client(mock_token_provider)
        with patch("sigantry_core.client.arm.poll_operation", return_value=None) as poll:
            client.send_arm_lro("POST", _suspend_path())
        assert poll.call_args.kwargs["initial_retry_after"] == 3.0


class TestPollPropagation:
    def test_poll_operation_lro_timeout_propagates(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        async_url = "https://management.azure.com/operations/op-abc"
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(202, headers={"Azure-AsyncOperation": async_url}, json={})
        )
        client = _make_client(mock_token_provider)
        err = LROTimeoutError(operation_id=async_url, elapsed_seconds=600.0, last_status="Running")
        with (
            patch(
                "sigantry_core.client.arm.poll_operation",
                side_effect=err,
            ),
            pytest.raises(LROTimeoutError) as exc,
        ):
            client.send_arm_lro("POST", _suspend_path())
        assert exc.value.last_status == "Running"

    def test_poll_operation_failure_propagates(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        async_url = "https://management.azure.com/operations/op-abc"
        respx_router.post(_suspend_url()).mock(
            return_value=httpx.Response(202, headers={"Azure-AsyncOperation": async_url}, json={})
        )
        client = _make_client(mock_token_provider)
        err = OperationFailedError(
            operation_id=async_url,
            error_code="CapacityAlreadySuspended",
            message="already suspended",
        )
        with (
            patch(
                "sigantry_core.client.arm.poll_operation",
                side_effect=err,
            ),
            pytest.raises(OperationFailedError) as exc,
        ):
            client.send_arm_lro("POST", _suspend_path())
        assert exc.value.error_code == "CapacityAlreadySuspended"


class TestAuth:
    def test_azure_rm_scope_used_for_auth(
        self,
        mock_token_provider: MagicMock,
        respx_router: respx.Router,
    ) -> None:
        respx_router.post(_suspend_url()).mock(return_value=httpx.Response(200, json={"ok": True}))
        client = _make_client(mock_token_provider)
        client.send_arm_lro("POST", _suspend_path())
        mock_token_provider.get_token.assert_called_with(AZURE_RM_SCOPE)
