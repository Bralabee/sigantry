"""sigantry_core.client - the single HTTP surface every module must use.

CLIENT-01 invariant: no module outside ``sigantry_core/client/**`` or
``sigantry_core/auth/diagnose.py`` (documented Phase 1 exception) may import
``httpx`` directly. Enforced by ruff banned-api at lint time and by
``tests/sigantry_core/client/test_package_structure.py`` at test time.

Plan 02-01 Task 1 ships errors, logging, models, and the package scaffold.
``BaseRestClient`` lands in Task 3.
"""

from __future__ import annotations

from sigantry_core.client.arm import FabricArmRestClient
from sigantry_core.client.base import BaseRestClient
from sigantry_core.client.errors import (
    AuthError,
    ClientError,
    HttpError,
    LROTimeoutError,
    NotFoundError,
    OperationFailedError,
    PaginationError,
    RateLimitError,
    ServerError,
)
from sigantry_core.client.fabric import FabricRestClient
from sigantry_core.client.logging import (
    configure_client_logging,
    get_correlation_id,
    get_operation_id,
    set_correlation_id,
    set_operation_id,
)
from sigantry_core.client.lro import (
    extract_operation_id,
    poll_operation,
)
from sigantry_core.client.models import HttpResponse
from sigantry_core.client.pagination import paginate
from sigantry_core.client.powerbi import PowerBIRestClient
from sigantry_core.client.purview import PurviewRestClient

__all__ = [
    "AuthError",
    "BaseRestClient",
    "ClientError",
    "FabricArmRestClient",
    "FabricRestClient",
    "HttpError",
    "HttpResponse",
    "LROTimeoutError",
    "NotFoundError",
    "OperationFailedError",
    "PaginationError",
    "PowerBIRestClient",
    "PurviewRestClient",
    "RateLimitError",
    "ServerError",
    "configure_client_logging",
    "extract_operation_id",
    "get_correlation_id",
    "get_operation_id",
    "paginate",
    "poll_operation",
    "set_correlation_id",
    "set_operation_id",
]
