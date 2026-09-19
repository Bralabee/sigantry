"""Tenant-settings baseline export (GOV-05) - Pattern 6 canonical JSON + SHA-256.

Primary endpoint: ``GET /v1/admin/tenantsettings`` (Fabric admin, GA,
SP-supported, RESEARCH A8). Rate-limit: 25 req/minute (Pitfall 8) - Plan 03-04
adds a bucket at 60% of that (``(15, 60)``) in
``sigantry_core.client.rate_limits``.

Canonical serialization (``sort_keys=True, indent=2``) makes the baseline file
byte-stable so a later diff (Phase 6 drift detection) flags any change as a
digest mismatch. The digest is computed over the canonical JSON of the
SETTINGS LIST ONLY (not the envelope) - the envelope carries ``capturedAt``
and ``digest`` which would otherwise make the digest self-referential.

Power BI admin side (fallback for toggles Fabric admin does not expose) is
covered by a PowerShell parity cmdlet in the Fabric PS module (GOV-06).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sigantry_core.client import FabricRestClient

_CANONICAL_KWARGS: dict[str, Any] = {
    "sort_keys": True,
    "indent": 2,
    "separators": (",", ": "),
}


@dataclass(frozen=True, slots=True)
class TenantSettingBaseline:
    """Immutable envelope for a captured tenant-settings baseline.

    The envelope is deliberately a frozen dataclass (not a TypedDict) so
    downstream consumers get attribute access and a stable ``.to_dict()``
    helper that emits the camelCase wire shape. ``settings`` is a tuple so
    the dataclass can remain frozen without giving callers a mutable handle
    on the underlying list.
    """

    captured_at: str
    tenant_id: str
    digest: str
    settings: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        """Emit the camelCase wire shape used for JSON serialization."""
        return {
            "capturedAt": self.captured_at,
            "tenantId": self.tenant_id,
            "digest": self.digest,
            "settings": list(self.settings),
        }


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization (Pattern 6)."""
    return json.dumps(obj, **_CANONICAL_KWARGS)


def compute_digest(settings: Iterable[dict[str, Any]]) -> str:
    """SHA-256 digest over the canonical JSON of the settings list.

    Order-sensitive: pass an already-sorted list. ``export_baseline`` sorts
    its input by ``settingName`` before calling this helper so identical
    tenant states produce identical digests regardless of upstream page
    ordering.

    Returns
    -------
    str
        ``"sha256:"`` prefix followed by the 64-char lowercase hex digest.
    """
    canonical = _canonical_json(list(settings))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def export_baseline(client: FabricRestClient, *, tenant_id: str = "") -> TenantSettingBaseline:
    """Capture a tenant-settings baseline from ``GET /v1/admin/tenantsettings``.

    Consumes the paginated response via
    :meth:`FabricRestClient.list_paginated`, sorts settings by ``settingName``
    (ascending; missing names sort first as empty string), computes the digest
    over the sorted list, and returns an immutable envelope.

    Args:
        client: Fabric REST client. Phase 2 pagination contract is expected
            (list-shaped pages unwrapped to per-item dicts).
        tenant_id: Optional AAD tenant id recorded on the envelope. Does NOT
            affect the endpoint call - authentication tenant is pinned via the
            ``TokenProvider`` the client was built with.

    Returns:
        :class:`TenantSettingBaseline` ready to write via :func:`write_baseline`.
    """
    raw = list(client.list_paginated("/v1/admin/tenantsettings"))
    settings_sorted = sorted(raw, key=lambda s: s.get("settingName") or "")
    digest = compute_digest(settings_sorted)
    return TenantSettingBaseline(
        captured_at=datetime.now(tz=UTC).isoformat(),
        tenant_id=tenant_id,
        digest=digest,
        settings=tuple(settings_sorted),
    )


def write_baseline(envelope: TenantSettingBaseline | dict[str, Any], path: Path) -> None:
    """Write an envelope to ``path`` as canonical JSON UTF-8 with trailing newline.

    - Creates parent directories (``parents=True, exist_ok=True``) so callers
      can persist into ``docs/baselines/YYYY-MM-DD/`` without pre-creating the
      tree.
    - Idempotent: two calls with identical envelope content produce identical
      file bytes.
    - Accepts either the dataclass OR its ``.to_dict()`` form so recorded
      baselines loaded from disk can be re-written without conversion.
    """
    payload = envelope.to_dict() if isinstance(envelope, TenantSettingBaseline) else envelope
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(payload) + "\n", encoding="utf-8")
