"""Error hierarchy: types and (credential_used, scope, remediation) contract."""

from __future__ import annotations

import pytest

from sigantry_core.auth.errors import (
    FabricAuthError,
    GroupMembershipError,
    KeyVaultResolutionError,
    TenantSettingError,
    TokenAcquisitionError,
)


@pytest.mark.parametrize(
    "cls",
    [
        TokenAcquisitionError,
        TenantSettingError,
        GroupMembershipError,
        KeyVaultResolutionError,
    ],
)
def test_subclasses_inherit_from_base(cls: type) -> None:
    assert issubclass(cls, FabricAuthError)
    assert issubclass(cls, Exception)


def test_error_carries_remediation_triple() -> None:
    e = TokenAcquisitionError(
        "chain returned no token",
        credential_used="EnvironmentCredential",
        scope="https://api.fabric.microsoft.com/.default",
        remediation="az login --tenant <tid>",
    )
    assert e.credential_used == "EnvironmentCredential"
    assert e.scope == "https://api.fabric.microsoft.com/.default"
    assert e.remediation == "az login --tenant <tid>"
    assert "chain returned no token" in str(e)


def test_error_fields_default_to_none() -> None:
    e = FabricAuthError("base")
    assert e.credential_used is None
    assert e.scope is None
    assert e.remediation is None
