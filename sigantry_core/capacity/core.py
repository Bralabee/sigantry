"""Capacity list - Fabric Core /v1/capacities (Plan 03-02, WKSP-04).

Per RESEARCH §6 + A11 the Fabric Core list endpoint returns id, displayName,
sku (name + tier), region and state inline, so no admin endpoint is needed
for the toolkit's use case. ARM is reserved for the pause/resume LRO in
:mod:`sigantry_core.capacity.lifecycle` (Pitfall 2: wrong audience).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sigantry_core.client import FabricRestClient


@dataclass(frozen=True, slots=True)
class Capacity:
    """Frozen DTO for a Fabric capacity.

    Optional fields default to ``None`` so Fabric schema additions do not
    break callers.
    """

    id: str
    display_name: str
    sku_name: str | None
    sku_tier: str | None
    region: str | None
    state: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Capacity:
        sku_raw = payload.get("sku")
        sku: dict[str, Any] = sku_raw if isinstance(sku_raw, dict) else {}
        return cls(
            id=payload["id"],
            display_name=payload["displayName"],
            sku_name=sku.get("name"),
            sku_tier=sku.get("tier"),
            region=payload.get("region"),
            state=payload.get("state"),
        )


def list_capacities(client: FabricRestClient) -> Iterator[Capacity]:
    """Yield every ``Capacity`` visible to the caller.

    GET ``/v1/capacities`` via :meth:`FabricRestClient.list_paginated`, which
    inherits the full Phase 2 retry + rate-limit + correlated-logging
    pipeline. The rate-limit bucket is the ``("GET", "/v1/capacities")``
    entry added by this plan (60/60s).
    """
    for payload in client.list_paginated("/v1/capacities"):
        yield Capacity.from_api(payload)
