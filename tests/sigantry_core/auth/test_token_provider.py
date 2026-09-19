"""TokenProvider: cache + chain + wrong-tenant mitigation + P1-1 logging."""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from azure.core.credentials import AccessToken

from sigantry_core.auth.audiences import FABRIC_SCOPE, GRAPH_SCOPE
from sigantry_core.auth.errors import TokenAcquisitionError
from sigantry_core.auth.token_provider import (
    TokenProvider,
    get_fabric_token,
    get_token_provider,
    reset_token_provider,
)


@pytest.fixture(autouse=True)
def _clean_singleton():
    reset_token_provider()
    yield
    reset_token_provider()


class TestCaching:
    def test_caches_by_scope(self, fake_credential: MagicMock) -> None:
        tp = TokenProvider(credential=fake_credential)
        t1 = tp.get_token(FABRIC_SCOPE)
        t2 = tp.get_token(FABRIC_SCOPE)
        assert t1 == t2 == "fake-access-token-xyz"
        fake_credential.get_token.assert_called_once_with(FABRIC_SCOPE)

    def test_different_scopes_trigger_separate_calls(self, fake_credential: MagicMock) -> None:
        tp = TokenProvider(credential=fake_credential)
        tp.get_token(FABRIC_SCOPE)
        tp.get_token(GRAPH_SCOPE)
        assert fake_credential.get_token.call_count == 2

    def test_refreshes_when_lifetime_below_skew(self) -> None:
        now = int(time.time())
        cred = MagicMock()
        cred.get_token.side_effect = [
            AccessToken("first", expires_on=now + 100),  # <5min -> refresh
            AccessToken("second", expires_on=now + 3600),
        ]
        tp = TokenProvider(credential=cred)
        assert tp.get_token(FABRIC_SCOPE) == "first"
        assert tp.get_token(FABRIC_SCOPE) == "second"
        assert cred.get_token.call_count == 2

    def test_does_not_refresh_when_lifetime_above_skew(self) -> None:
        now = int(time.time())
        cred = MagicMock()
        cred.get_token.return_value = AccessToken("long-lived", expires_on=now + 3600)
        tp = TokenProvider(credential=cred)
        tp.get_token(FABRIC_SCOPE)
        tp.get_token(FABRIC_SCOPE)
        cred.get_token.assert_called_once()


class TestChainConfiguration:
    def test_default_excludes_interactive_browser_and_vscode(self) -> None:
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential") as mock_dac:
            TokenProvider()
            mock_dac.assert_called_once()
            _, kwargs = mock_dac.call_args
            assert kwargs.get("exclude_interactive_browser_credential") is True
            assert kwargs.get("exclude_visual_studio_code_credential") is True

    def test_tenant_id_passed_to_credential(self) -> None:
        """Pitfall P1-6 mitigation: wrong-tenant silent success."""
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential") as mock_dac:
            TokenProvider(tenant_id="tid-explicit")
            _, kwargs = mock_dac.call_args
            assert kwargs.get("additionally_allowed_tenants") == ["tid-explicit"]

    def test_injected_credential_skips_default_construction(
        self, fake_credential: MagicMock
    ) -> None:
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential") as mock_dac:
            tp = TokenProvider(credential=fake_credential)
            mock_dac.assert_not_called()
            assert tp.credential is fake_credential


class TestChainSlotDetection:
    """Pitfall P1-1 mitigation: TokenProvider must report which slot won."""

    def test_logs_winning_credential_class(
        self, fake_credential: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_credential.__class__.__name__ = "AzureCliCredential"
        tp = TokenProvider(credential=fake_credential)
        with caplog.at_level(logging.INFO, logger="sigantry_core.auth.token_provider"):
            tp.get_token(FABRIC_SCOPE)
        assert any("token_acquired" in r.message for r in caplog.records)

    def test_last_credential_class_returns_cached_name(self, fake_credential: MagicMock) -> None:
        tp = TokenProvider(credential=fake_credential)
        assert tp.last_credential_class(FABRIC_SCOPE) is None  # nothing cached yet
        tp.get_token(FABRIC_SCOPE)
        assert tp.last_credential_class(FABRIC_SCOPE) == type(fake_credential).__name__

    @pytest.mark.parametrize(
        "slot_name",
        [
            "ManagedIdentityCredential",  # Fabric notebook
            "EnvironmentCredential",  # ADO pipeline (WIF)
            "WorkloadIdentityCredential",  # ADO pipeline (alt)
            "AzureCliCredential",  # Laptop
        ],
    )
    def test_each_runtime_slot_reports_its_name(self, slot_name: str) -> None:
        cred = MagicMock()
        cred.__class__.__name__ = slot_name
        cred.get_token.return_value = AccessToken("t", expires_on=int(time.time()) + 3600)
        tp = TokenProvider(credential=cred)
        tp.get_token(FABRIC_SCOPE)
        assert tp.last_credential_class(FABRIC_SCOPE) == slot_name


class TestErrorWrapping:
    def test_chain_failure_raises_token_acquisition_error(self) -> None:
        cred = MagicMock()
        cred.get_token.side_effect = RuntimeError("no credential configured")
        tp = TokenProvider(credential=cred)
        with pytest.raises(TokenAcquisitionError) as exc_info:
            tp.get_token(FABRIC_SCOPE)
        e = exc_info.value
        assert e.scope == FABRIC_SCOPE
        assert "diagnose-auth" in (e.remediation or "")
        assert isinstance(e.__cause__, RuntimeError)


class TestThreadSafety:
    def test_concurrent_callers_share_cache(self) -> None:
        now = int(time.time())
        cred = MagicMock()
        cred.get_token.return_value = AccessToken("shared", expires_on=now + 3600)
        tp = TokenProvider(credential=cred)

        errors: list[Exception] = []

        def _call() -> None:
            try:
                assert tp.get_token(FABRIC_SCOPE) == "shared"
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=_call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Cache prevents N calls; exactly one underlying call.
        assert cred.get_token.call_count == 1


class TestProcessSingleton:
    def test_get_token_provider_returns_same_instance(self) -> None:
        tp1 = get_token_provider()
        tp2 = get_token_provider()
        assert tp1 is tp2

    def test_reset_recreates_singleton(self) -> None:
        tp1 = get_token_provider()
        reset_token_provider()
        tp2 = get_token_provider()
        assert tp1 is not tp2

    def test_get_fabric_token_uses_singleton(self) -> None:
        with patch("sigantry_core.auth.token_provider.DefaultAzureCredential") as mock_dac:
            mock_dac.return_value.get_token.return_value = AccessToken(
                "fab", expires_on=int(time.time()) + 3600
            )
            assert get_fabric_token() == "fab"


class TestPerTenantSingleton:
    """Audit-2026-05-07 W1.4: per-tenant TokenProvider pool.

    Falsifiability contract: this class FAILS against the pre-fix
    implementation that latched a single ``_default_provider`` to the
    first caller's ``tenant_id``, silently mis-routing every subsequent
    different-tenant call.
    """

    def test_distinct_tenant_ids_yield_distinct_providers(self) -> None:
        tp_a = get_token_provider(tenant_id="tenant-a-uuid")
        tp_b = get_token_provider(tenant_id="tenant-b-uuid")
        assert tp_a is not tp_b
        assert tp_a._tenant_id == "tenant-a-uuid"
        assert tp_b._tenant_id == "tenant-b-uuid"

    def test_same_tenant_id_returns_same_instance(self) -> None:
        tp_a1 = get_token_provider(tenant_id="tenant-a-uuid")
        tp_a2 = get_token_provider(tenant_id="tenant-a-uuid")
        assert tp_a1 is tp_a2

    def test_none_tenant_is_distinct_from_named_tenant(self) -> None:
        # The default-credential-chain provider (None) must not collide
        # with a named-tenant provider.
        tp_default = get_token_provider()
        tp_named = get_token_provider(tenant_id="tenant-a-uuid")
        assert tp_default is not tp_named
        assert tp_default._tenant_id is None
        assert tp_named._tenant_id == "tenant-a-uuid"

    def test_first_call_wins_regression_does_not_recur(self) -> None:
        # The bug: first call latched the singleton's tenant_id; second
        # call with a different tenant_id silently returned the first.
        # After the fix, the second call constructs a fresh provider
        # bound to the requested tenant.
        tp_a = get_token_provider(tenant_id="tenant-a-uuid")
        tp_b = get_token_provider(tenant_id="tenant-b-uuid")
        assert tp_a._tenant_id != tp_b._tenant_id
        assert tp_a is not tp_b

    def test_reset_clears_all_tenants(self) -> None:
        tp_a_pre = get_token_provider(tenant_id="tenant-a-uuid")
        get_token_provider(tenant_id="tenant-b-uuid")
        reset_token_provider()
        tp_a_post = get_token_provider(tenant_id="tenant-a-uuid")
        assert tp_a_pre is not tp_a_post

    def test_token_caches_are_isolated_per_tenant(self, fake_credential: MagicMock) -> None:
        # Each provider has its own credential + cache, so a token cached
        # against tenant A does not leak into tenant B's resolution path.
        tp_a = get_token_provider(tenant_id="tenant-a-uuid")
        tp_b = get_token_provider(tenant_id="tenant-b-uuid")
        # Different cache dict instances:
        assert tp_a._cache is not tp_b._cache
        # Different credential instances bound to different tenants:
        assert tp_a.credential is not tp_b.credential


class TestPublicReexports:
    """Assert the __init__.py public surface is stable - every downstream phase imports from here."""

    def test_public_surface(self) -> None:
        from sigantry_core import auth

        expected = {
            "TokenProvider",
            "TokenProviderProtocol",
            "get_token",
            "get_fabric_token",
            "get_powerbi_token",
            "get_graph_token",
            "get_purview_token",
            "get_azure_rm_token",
            "get_default_credential",
            "get_token_provider",
            "reset_token_provider",
            "Secret",
            "resolve_secret",
            "FabricAuthError",
            "TokenAcquisitionError",
            "TenantSettingError",
            "GroupMembershipError",
            "KeyVaultResolutionError",
            "FABRIC_SCOPE",
            "POWERBI_SCOPE",
            "GRAPH_SCOPE",
            "PURVIEW_SCOPE",
            "AZURE_RM_SCOPE",
            "ALL_SCOPES",
            "FABRIC_AUDIENCE",
            "GRAPH_AUDIENCE",
        }
        missing = expected - set(auth.__all__)
        assert not missing, f"auth/__init__ missing: {missing}"
        for name in expected:
            assert hasattr(auth, name), name


class TestTokenProviderProtocolContract:
    """Review-fix MD-02: lock the structural Protocol contract.

    ``BaseRestClient`` accepts any ``TokenProviderProtocol``-shaped value.
    Both the concrete ``TokenProvider`` and the GitHub carve-out's
    ``_NoopTokenProvider`` MUST satisfy the Protocol -- the latter via
    duck-typing, the former structurally. If a future ``BaseRestClient``
    change extends the Protocol with a new method, these tests fail and
    the breakage surfaces at type-check + runtime, not on first send().
    """

    def test_protocol_is_runtime_checkable(self) -> None:
        from sigantry_core.auth import TokenProviderProtocol

        # ``@runtime_checkable`` makes ``isinstance`` legal against the Protocol.
        # The check is purely structural (presence of methods); subclassing is
        # NOT required.
        assert hasattr(TokenProviderProtocol, "get_token")
        assert hasattr(TokenProviderProtocol, "last_credential_class")

    def test_concrete_token_provider_satisfies_protocol(self, fake_credential: MagicMock) -> None:
        from sigantry_core.auth import TokenProviderProtocol

        tp = TokenProvider(credential=fake_credential)
        assert isinstance(tp, TokenProviderProtocol)

    def test_noop_token_provider_satisfies_protocol(self) -> None:
        from sigantry_core.auth import TokenProviderProtocol
        from sigantry_core.workitems.github import _NoopTokenProvider

        # The GitHub carve-out's no-op provider MUST satisfy the Protocol
        # without subclassing -- structural typing only.
        assert isinstance(_NoopTokenProvider(), TokenProviderProtocol)

    def test_object_without_methods_does_not_satisfy_protocol(self) -> None:
        from sigantry_core.auth import TokenProviderProtocol

        class _Empty:
            pass

        assert not isinstance(_Empty(), TokenProviderProtocol)

    def test_object_missing_one_method_does_not_satisfy_protocol(self) -> None:
        """A class with ``get_token`` but no ``last_credential_class`` fails."""
        from sigantry_core.auth import TokenProviderProtocol

        class _PartialProvider:
            def get_token(self, scope: str) -> str:
                return "x"

        # @runtime_checkable Protocols verify presence (structural) -- a
        # class missing a required method must NOT be considered
        # conformant.
        assert not isinstance(_PartialProvider(), TokenProviderProtocol)

    def test_base_rest_client_accepts_protocol_typed_provider(self) -> None:
        """``BaseRestClient.__init__`` annotation lists the Protocol, not the concrete class.

        If a future change reverts to ``token_provider: TokenProvider`` the
        ``# type: ignore[arg-type]`` carve-out for ``_NoopTokenProvider``
        comes back. Lock the Protocol-typed signature here.
        """
        import inspect

        from sigantry_core.auth import TokenProviderProtocol
        from sigantry_core.client.base import BaseRestClient

        sig = inspect.signature(BaseRestClient.__init__)
        annotation = sig.parameters["token_provider"].annotation
        # ``annotation`` may be the Protocol class itself or the string form
        # ("TokenProviderProtocol") under ``from __future__ import annotations``.
        if isinstance(annotation, str):
            assert annotation == "TokenProviderProtocol"
        else:
            assert annotation is TokenProviderProtocol
