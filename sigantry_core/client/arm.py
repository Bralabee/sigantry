"""FabricArmRestClient - Azure Resource Manager surface for Microsoft.Fabric.

Plan 03-02 Task 1. Consumed by :mod:`sigantry_core.capacity.lifecycle` for
suspend / resume (WKSP-05).

Scope: :data:`sigantry_core.auth.AZURE_RM_SCOPE`
    (``https://management.azure.com/.default``).
Base URL: ``https://management.azure.com``.

LRO header preference (Pitfall 7):
    ``Azure-AsyncOperation`` > ``Location``. ARM does NOT emit
    ``x-ms-operation-id``; we use the polling URL itself as the operation
    identity for logging/tracing (the URL uniquely identifies the
    operation).

Terminal status set (Phase 2 :func:`sigantry_core.client.lro.poll_operation`):
    ``Succeeded``, ``Failed``. ARM also defines ``Canceled`` which is not in
    the Phase 2 terminal set - it will spin until ``timeout``/``max_polls``
    and surface as :class:`LROTimeoutError` with ``last_status="Canceled"``.
    Rare and genuinely exceptional for WKSP-05 pause/resume per RESEARCH
    §14.4.

api-version default: ``2023-11-01`` (GA for Microsoft.Fabric/capacities).
Passed as a query parameter, not part of the path. Caller may override per
call via the ``api_version`` kwarg of :meth:`send_arm_lro`.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from sigantry_core.auth import TokenProvider, get_token_provider
from sigantry_core.auth.audiences import AZURE_RM_SCOPE
from sigantry_core.client.base import BaseRestClient
from sigantry_core.client.errors import LROTimeoutError
from sigantry_core.client.lro import poll_operation
from sigantry_core.client.retry import _parse_retry_after

ARM_DEFAULT_BASE_URL: Final[str] = "https://management.azure.com"
ARM_CAPACITIES_API_VERSION: Final[str] = "2023-11-01"

# ARM LRO wall-clock + iteration caps. Distinct from the Phase 2 Fabric LRO
# defaults: ARM capacity pause/resume is documented in the 60-120s range, but
# we keep a generous ceiling for the rare slow tenant.
_ARM_LRO_TIMEOUT: Final[float] = 600.0
_ARM_LRO_MAX_POLLS: Final[int] = 100
_ARM_LRO_DEFAULT_RETRY_AFTER: Final[float] = 3.0


class FabricArmRestClient(BaseRestClient):
    """ARM surface for ``Microsoft.Fabric/capacities/*`` operations.

    Instantiate via :meth:`from_defaults` (threads the Phase 1
    :func:`sigantry_core.auth.get_token_provider` singleton) or by
    constructing directly with an explicit ``token_provider``.

    Use :meth:`send_arm_lro` for suspend / resume (the only ARM operations
    v1 requires). Use inherited :meth:`BaseRestClient.send` for any
    synchronous ARM call (none in v1).
    """

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        base_url: str = ARM_DEFAULT_BASE_URL,
        default_scope: str = AZURE_RM_SCOPE,
        default_timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            token_provider=token_provider,
            base_url=base_url,
            default_scope=default_scope,
            default_timeout=default_timeout,
            http_client=http_client,
        )

    @classmethod
    def from_defaults(
        cls,
        *,
        tenant_id: str | None = None,
        base_url: str = ARM_DEFAULT_BASE_URL,
        default_timeout: float = 30.0,
    ) -> FabricArmRestClient:
        """Construct with the process-wide :class:`TokenProvider` singleton.

        Mirrors :meth:`sigantry_core.client.FabricRestClient.from_defaults`.
        The scope is always :data:`AZURE_RM_SCOPE` - ARM rejects Fabric
        tokens (Pitfall 2).
        """
        return cls(
            token_provider=get_token_provider(tenant_id=tenant_id),
            base_url=base_url,
            default_scope=AZURE_RM_SCOPE,
            default_timeout=default_timeout,
        )

    def send_arm_lro(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        api_version: str = ARM_CAPACITIES_API_VERSION,
        timeout: float = _ARM_LRO_TIMEOUT,
        max_polls: int = _ARM_LRO_MAX_POLLS,
    ) -> Any:
        """Issue an ARM request that may be a long-running operation.

        Flow:
        - Attach ``api-version`` (default 2023-11-01) as a query parameter.
        - Send the initial request. ``BaseRestClient.send`` handles retry +
          rate-limit + auth + correlated logging.
        - ``200`` / ``201`` -> body IS the result, return it (no poll).
        - Any other non-202 2xx -> return body (unusual but accept).
        - ``202`` -> resolve polling URL from headers:
            1. ``Azure-AsyncOperation`` (ARM canonical, Pitfall 7),
            2. ``Location`` (fallback).
          Neither present -> raise :class:`LROTimeoutError` with
          ``last_status="missing_polling_url"``.
        - Parse ``Retry-After`` (fall back to 3s) and delegate to
          :func:`sigantry_core.client.lro.poll_operation`.

        Args:
            method: HTTP verb for the initial call (typically ``POST``).
            path: absolute URL or path relative to ``self._base_url``.
            params: initial request query parameters (``api-version`` is
                attached automatically; caller-supplied params are merged).
            json: initial request JSON body.
            api_version: ARM api-version (default ``2023-11-01`` - GA for
                ``Microsoft.Fabric/capacities``).
            timeout: LRO wall-clock cap in seconds (default 600).
            max_polls: hard cap on poll iterations (default 100).

        Returns:
            The terminal-state body (or ``None`` when ``Succeeded`` emits no
            payload).

        Raises:
            LROTimeoutError: missing polling URL, or Phase 2 poller hit the
                timeout / max-polls / disappearing-state limit.
            OperationFailedError: terminal ``Failed`` from the state service.
            HttpError: the initial call returned non-2xx (via
                :func:`sigantry_core.client.retry.classify_response`).
        """
        merged_params: dict[str, Any] = {"api-version": api_version}
        if params:
            merged_params.update(params)

        initial = self.send(method, path, params=merged_params, json=json)

        if initial.status_code in (200, 201):
            return initial.json_body
        if initial.status_code != 202:
            return initial.json_body

        headers = initial.headers
        state_url = _get_header(headers, "Azure-AsyncOperation") or _get_header(headers, "Location")
        if not state_url:
            raise LROTimeoutError(
                operation_id="<arm-no-polling-url>",
                elapsed_seconds=0.0,
                last_status="missing_polling_url",
            )

        retry_after = (
            _parse_retry_after(_get_header(headers, "Retry-After")) or _ARM_LRO_DEFAULT_RETRY_AFTER
        )

        return poll_operation(
            self,
            operation_id=state_url,
            state_url=state_url,
            scope=None,
            timeout=timeout,
            max_polls=max_polls,
            initial_retry_after=retry_after,
        )


def _get_header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup.

    :class:`sigantry_core.client.models.HttpResponse.headers` stores keys as
    httpx emits them (lowercased), so we normalise both sides to match the
    canonical mixed-case names ARM documents.
    """
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None
