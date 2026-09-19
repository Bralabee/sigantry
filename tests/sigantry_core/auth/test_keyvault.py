"""Secret redaction contract (T-1-02) and kv:// URI resolution."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from sigantry_core.auth.errors import KeyVaultResolutionError
from sigantry_core.auth.keyvault import Secret, resolve_secret


class TestSecretRedaction:
    def test_repr_is_redacted(self) -> None:
        s = Secret(value="hunter2")
        assert repr(s) == "<redacted>"

    def test_str_is_redacted(self) -> None:
        s = Secret(value="hunter2")
        assert str(s) == "<redacted>"

    def test_fstring_is_redacted(self) -> None:
        s = Secret(value="hunter2")
        assert f"{s}" == "<redacted>"
        assert f"{s!r}" == "<redacted>"

    def test_format_spec_is_redacted(self) -> None:
        s = Secret(value="hunter2")
        assert f"{s:>20}" == "<redacted>"

    def test_logging_is_redacted(self, caplog: pytest.LogCaptureFixture) -> None:
        s = Secret(value="hunter2-leak")
        with caplog.at_level(logging.INFO):
            logging.getLogger(__name__).info("secret value: %s", s)
            logging.getLogger(__name__).info("secret repr: %r", s)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "hunter2-leak" not in joined
        assert joined.count("<redacted>") >= 2

    def test_value_accessor_returns_cleartext(self) -> None:
        s = Secret(value="hunter2")
        assert s.value == "hunter2"

    def test_secret_is_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        s = Secret(value="hunter2")
        with pytest.raises(FrozenInstanceError):
            s.value = "mutated"  # type: ignore[misc]


class TestResolveSecretPassthrough:
    def test_plain_string_is_wrapped_without_kv_call(self, fake_credential) -> None:
        with patch("sigantry_core.auth.keyvault.SecretClient") as mock_client:
            s = resolve_secret("plain-pat-string", credential=fake_credential)
            assert isinstance(s, Secret)
            assert s.value == "plain-pat-string"
            mock_client.assert_not_called()


class TestResolveSecretKvUri:
    def test_kv_uri_invokes_secret_client(self, fake_credential, fake_secret_client) -> None:
        with patch(
            "sigantry_core.auth.keyvault.SecretClient",
            return_value=fake_secret_client,
        ) as mock_ctor:
            s = resolve_secret("kv://example-kv-prod/fabric-pat", credential=fake_credential)

        mock_ctor.assert_called_once_with(
            vault_url="https://example-kv-prod.vault.azure.net",
            credential=fake_credential,
        )
        fake_secret_client.get_secret.assert_called_once_with("fabric-pat")
        assert s.value == "resolved-secret-value"

    def test_malformed_kv_uri_raises(self, fake_credential) -> None:
        with pytest.raises(KeyVaultResolutionError, match="malformed"):
            resolve_secret("kv://just-host-no-name/", credential=fake_credential)

    def test_kv_uri_with_no_host_raises(self, fake_credential) -> None:
        with pytest.raises(KeyVaultResolutionError):
            resolve_secret("kv:///just-path", credential=fake_credential)

    def test_secret_client_failure_is_wrapped(self, fake_credential) -> None:
        class _FailClient:
            def get_secret(self, _name: str):
                raise RuntimeError("access denied")

        with (
            patch("sigantry_core.auth.keyvault.SecretClient", return_value=_FailClient()),
            pytest.raises(KeyVaultResolutionError, match="access denied"),
        ):
            resolve_secret("kv://example-kv-prod/fabric-pat", credential=fake_credential)

    def test_null_secret_value_raises(self, fake_credential) -> None:
        class _NullClient:
            def get_secret(self, _name: str):
                from unittest.mock import MagicMock

                m = MagicMock()
                m.value = None
                return m

        with (
            patch("sigantry_core.auth.keyvault.SecretClient", return_value=_NullClient()),
            pytest.raises(KeyVaultResolutionError, match="null value"),
        ):
            resolve_secret("kv://example-kv-prod/empty-secret", credential=fake_credential)
