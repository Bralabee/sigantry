"""Unit tests for ``sigantry_core.governance.tenant_settings`` (GOV-05).

Plan 03-04: canonical JSON + SHA-256 baseline exporter. Covers the digest
shape/stability contract, envelope shape, sort-by-settingName invariance,
and write_baseline round-trip + idempotency.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sigantry_core.client import FabricRestClient
from sigantry_core.governance.tenant_settings import (
    TenantSettingBaseline,
    compute_digest,
    export_baseline,
    write_baseline,
)

_SAMPLE_SETTINGS: list[dict] = [
    {"settingName": "ServicePrincipalAccess", "title": "SP access", "enabled": True},
    {
        "settingName": "AllowGuestAccess",
        "title": "Guest",
        "enabled": False,
        "enabledSecurityGroups": [{"graphId": "g1", "name": "admins"}],
    },
    {"settingName": "CertifyContent", "title": "Certify", "enabled": True},
]


@pytest.fixture
def fabric_mock() -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.list_paginated.return_value = iter(_SAMPLE_SETTINGS)
    return c


class TestDigest:
    def test_shape(self) -> None:
        d = compute_digest(_SAMPLE_SETTINGS)
        assert d.startswith("sha256:")
        assert len(d) == len("sha256:") + 64
        assert all(ch in "0123456789abcdef" for ch in d[len("sha256:") :])

    def test_stability(self) -> None:
        assert compute_digest(_SAMPLE_SETTINGS) == compute_digest(_SAMPLE_SETTINGS)

    def test_input_order_is_sensitive(self) -> None:
        # compute_digest is order-sensitive (that's why export_baseline sorts first).
        reordered = list(reversed(_SAMPLE_SETTINGS))
        assert compute_digest(_SAMPLE_SETTINGS) != compute_digest(reordered)


class TestExport:
    def test_envelope_shape(self, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="tenant-1")
        assert isinstance(envelope, TenantSettingBaseline)
        d = envelope.to_dict()
        assert set(d) >= {"capturedAt", "tenantId", "digest", "settings"}
        assert d["tenantId"] == "tenant-1"
        assert d["digest"].startswith("sha256:")

    def test_sorts_by_settingname(self, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="t")
        names = [s["settingName"] for s in envelope.settings]
        assert names == sorted(names)
        # And specifically: AllowGuestAccess, CertifyContent, ServicePrincipalAccess
        assert names == ["AllowGuestAccess", "CertifyContent", "ServicePrincipalAccess"]

    def test_empty_tenant(self) -> None:
        fabric = MagicMock(spec=FabricRestClient)
        fabric.list_paginated.return_value = iter([])
        envelope = export_baseline(fabric, tenant_id="empty")
        assert envelope.settings == ()
        assert envelope.digest.startswith("sha256:")

    def test_input_order_invariance_via_sort(self) -> None:
        # Two tenants with identical sets in different upstream order
        # must produce identical digests after export_baseline's sort.
        fabric_a = MagicMock(spec=FabricRestClient)
        fabric_a.list_paginated.return_value = iter(_SAMPLE_SETTINGS)
        fabric_b = MagicMock(spec=FabricRestClient)
        fabric_b.list_paginated.return_value = iter(list(reversed(_SAMPLE_SETTINGS)))
        env_a = export_baseline(fabric_a, tenant_id="t")
        env_b = export_baseline(fabric_b, tenant_id="t")
        assert env_a.digest == env_b.digest

    def test_captured_at_is_iso8601_utc(self, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="t")
        # Must end with +00:00 (UTC offset) and parse as datetime-isoformat-compatible
        assert envelope.captured_at.endswith("+00:00")

    def test_calls_list_paginated_with_admin_tenantsettings(self, fabric_mock: MagicMock) -> None:
        export_baseline(fabric_mock, tenant_id="t")
        fabric_mock.list_paginated.assert_called_once_with("/v1/admin/tenantsettings")


class TestWrite:
    def test_round_trip(self, tmp_path: Path, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="t")
        out = tmp_path / "subdir" / "tenant-settings-2026-04-20.json"
        write_baseline(envelope, out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["digest"] == envelope.digest
        assert data["tenantId"] == "t"
        assert len(data["settings"]) == len(envelope.settings)

    def test_creates_parent_dirs(self, tmp_path: Path, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="t")
        out = tmp_path / "deeply" / "nested" / "path" / "baseline.json"
        assert not out.parent.exists()
        write_baseline(envelope, out)
        assert out.exists()

    def test_is_idempotent(self, tmp_path: Path, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="t")
        out = tmp_path / "baseline.json"
        write_baseline(envelope, out)
        first = out.read_bytes()
        write_baseline(envelope, out)
        second = out.read_bytes()
        assert first == second

    def test_accepts_dict_envelope(self, tmp_path: Path) -> None:
        # write_baseline must accept both the dataclass and its dict form
        envelope_dict = {
            "capturedAt": "2026-04-20T00:00:00+00:00",
            "tenantId": "t",
            "digest": "sha256:abc",
            "settings": [],
        }
        out = tmp_path / "baseline.json"
        write_baseline(envelope_dict, out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data == envelope_dict

    def test_writes_trailing_newline(self, tmp_path: Path, fabric_mock: MagicMock) -> None:
        envelope = export_baseline(fabric_mock, tenant_id="t")
        out = tmp_path / "baseline.json"
        write_baseline(envelope, out)
        text = out.read_text(encoding="utf-8")
        assert text.endswith("\n")
