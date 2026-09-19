"""Unit tests for sigantry_core.governance.principal_expansion (GOV-04 helper)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core.auth.audiences import GRAPH_AUDIENCE, GRAPH_SCOPE
from sigantry_core.client.base import BaseRestClient


def test_graph_client_subclasses_base_rest_client() -> None:
    from sigantry_core.governance.principal_expansion import _GraphClient

    assert issubclass(_GraphClient, BaseRestClient)


def test_graph_client_pins_default_scope_and_base_url(
    mock_token_provider: MagicMock,
) -> None:
    from sigantry_core.governance.principal_expansion import _GraphClient

    gc = _GraphClient(token_provider=mock_token_provider)
    # Internal attributes set by BaseRestClient.__init__
    assert gc._base_url == GRAPH_AUDIENCE
    assert gc._default_scope == GRAPH_SCOPE


def test_expand_group_members_yields_each_member() -> None:
    from sigantry_core.governance.principal_expansion import (
        _GraphClient,
        expand_group_members,
    )

    members = [
        {
            "id": "u1",
            "displayName": "Alice",
            "@odata.type": "#microsoft.graph.user",
        },
        {
            "id": "sp1",
            "displayName": "Service-Principal-1",
            "@odata.type": "#microsoft.graph.servicePrincipal",
        },
    ]
    gc = MagicMock(spec=_GraphClient)
    gc.list_paginated.return_value = iter(members)

    out = list(expand_group_members("group-1", graph_client=gc))

    assert out == members
    gc.list_paginated.assert_called_once_with("/v1.0/groups/group-1/transitiveMembers")


def test_expand_group_members_yields_nothing_for_empty_group() -> None:
    from sigantry_core.governance.principal_expansion import (
        _GraphClient,
        expand_group_members,
    )

    gc = MagicMock(spec=_GraphClient)
    gc.list_paginated.return_value = iter([])

    out = list(expand_group_members("empty-group", graph_client=gc))
    assert out == []


def test_expand_group_members_passes_through_nested_group_records() -> None:
    """Graph's transitiveMembers flattens nested groups but may still emit
    group records; we pass them through (rbac.py decides annotation)."""
    from sigantry_core.governance.principal_expansion import (
        _GraphClient,
        expand_group_members,
    )

    members = [
        {"id": "u1", "@odata.type": "#microsoft.graph.user"},
        {"id": "g2", "@odata.type": "#microsoft.graph.group"},
    ]
    gc = MagicMock(spec=_GraphClient)
    gc.list_paginated.return_value = iter(members)

    out = list(expand_group_members("g1", graph_client=gc))
    assert {m["id"] for m in out} == {"u1", "g2"}


def test_from_defaults_constructs_with_token_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sigantry_core.governance.principal_expansion import _GraphClient

    fake_tp = MagicMock()
    captured: dict = {}

    def fake_get_token_provider(tenant_id: str | None = None) -> MagicMock:
        captured["tenant_id"] = tenant_id
        return fake_tp

    monkeypatch.setattr(
        "sigantry_core.governance.principal_expansion.get_token_provider",
        fake_get_token_provider,
    )

    gc = _GraphClient.from_defaults(tenant_id="tenant-xyz")
    assert isinstance(gc, _GraphClient)
    assert captured["tenant_id"] == "tenant-xyz"
    assert gc._tp is fake_tp
    assert gc._default_scope == GRAPH_SCOPE
    assert gc._base_url == GRAPH_AUDIENCE
