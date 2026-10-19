"""An Azure DevOps organization is given as a name or as a collection URL.

``$(System.CollectionUri)``, which ``templates/stages/sigantry-cd.yml`` hands to
``sigantry release record --ado-organization``, is ``https://dev.azure.com/<org>/``
(``https://<org>.visualstudio.com/`` on an older organization). Each ADO REST client
built ``https://dev.azure.com/{organization}`` from the string it was given, so the
template produced ``https://dev.azure.com/https://dev.azure.com/<org>/`` and the
first request failed. ``ado_organization_name`` is the one place that reads either
form; every client calls it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core._ado_organization import AdoOrganizationError, ado_organization_name
from sigantry_core.approval_gates.ado_environments import AdoEnvironmentsApprovalGate
from sigantry_core.auth import TokenProvider
from sigantry_core.pr_bot.providers.ado import AdoProvider
from sigantry_core.secrets.ado_variable_group import AdoVariableGroupSecretStore
from sigantry_core.workitems.ado import AdoWorkItemProvider


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("contoso", "contoso"),
        ("contoso-dataops", "contoso-dataops"),
        ("https://dev.azure.com/contoso/", "contoso"),
        ("https://dev.azure.com/contoso", "contoso"),
        ("https://DEV.Azure.com/contoso/", "contoso"),
        ("https://dev.azure.com/contoso/proj/_git/repo", "contoso"),
        ("https://contoso@dev.azure.com/contoso/proj/_git/repo", "contoso"),
        ("https://contoso.visualstudio.com/", "contoso"),
        ("https://contoso.visualstudio.com/DefaultCollection", "contoso"),
        ("  https://dev.azure.com/contoso/  ", "contoso"),
    ],
)
def test_reads_a_name_or_a_collection_url(given: str, expected: str) -> None:
    assert ado_organization_name(given) == expected


@pytest.mark.parametrize(
    "given",
    [
        "https://dev.azure.com/",
        "https://dev.azure.com",
        "https://tfs.example.com/tfs/DefaultCollection/",
        "https://app.vssps.visualstudio.com/",
        "https://visualstudio.com/",
        "https://github.com/contoso/",
    ],
)
def test_refuses_a_url_that_names_no_services_organization(given: str) -> None:
    with pytest.raises(AdoOrganizationError):
        ado_organization_name(given)


def test_the_refusal_does_not_echo_credentials_in_the_url() -> None:
    with pytest.raises(AdoOrganizationError) as excinfo:
        ado_organization_name("https://user:hunter2-pat@tfs.example.com/tfs/DefaultCollection/")
    assert "hunter2-pat" not in str(excinfo.value)


def test_a_name_with_surrounding_whitespace_is_trimmed() -> None:
    assert ado_organization_name("contoso\n") == "contoso"
    assert ado_organization_name("  contoso ") == "contoso"


@pytest.mark.parametrize(
    "given",
    [
        "dev.azure.com/contoso",  # a URL without its scheme
        "dev.azure.com",  # a host without its scheme
        "contoso.visualstudio.com",
        "contoso/",
        "/contoso",
        "con toso",
        "contoso?x=1",
        "user:hunter2-pat@contoso",  # credentials, no scheme
        "",
        "   ",
    ],
)
def test_refuses_a_value_that_cannot_be_a_name_and_is_not_a_url(given: str) -> None:
    """These used to pass through and build https://dev.azure.com/dev.azure.com/contoso."""
    with pytest.raises(AdoOrganizationError) as excinfo:
        ado_organization_name(given)
    assert "hunter2-pat" not in str(excinfo.value)


@pytest.mark.parametrize(
    "given",
    [
        "https://[bad/",
        # urlsplit's own ValueError quotes this netloc, userinfo included (NFKC check).
        "https://user:hunter2-pat@dev.azure.com\uff0fcontoso/",
    ],
)
def test_an_unparseable_url_is_an_organization_error_that_keeps_no_trace_of_the_parser(
    given: str,
) -> None:
    with pytest.raises(AdoOrganizationError) as excinfo:
        ado_organization_name(given)
    assert "hunter2-pat" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None, "the parser's ValueError quotes the URL"


def _token_provider() -> MagicMock:
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token"
    tp.last_credential_class.return_value = "MockCredential"
    return tp


def _work_item_provider(organization: str) -> object:
    return AdoWorkItemProvider(
        organization=organization, project="proj", token_provider=_token_provider()
    )


def _pr_provider(organization: str) -> object:
    return AdoProvider(
        org=organization, project="proj", repository_id="repo", token_provider=_token_provider()
    )


def _approval_gate(organization: str) -> object:
    return AdoEnvironmentsApprovalGate(
        organization=organization, project="proj", token_provider=_token_provider()
    )


def _variable_group(organization: str) -> object:
    return AdoVariableGroupSecretStore(
        organization=organization,
        project="proj",
        group_id=1,
        token_provider=_token_provider(),
        actor="tester",
    )


@pytest.mark.parametrize(
    ("collection_uri", "expected_base"),
    [
        ("https://dev.azure.com/contoso/", "https://dev.azure.com/contoso"),
        # The old parse read the last path segment, so this gave the "organization"
        # contoso.visualstudio.com.
        ("https://contoso.visualstudio.com/", "https://dev.azure.com/contoso"),
    ],
)
def test_pr_bot_reads_the_organization_from_the_pipeline_variable(
    monkeypatch: pytest.MonkeyPatch, collection_uri: str, expected_base: str
) -> None:
    from sigantry_core.pr_bot import cli as pr_cli

    monkeypatch.setenv("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", collection_uri)
    monkeypatch.setenv("SYSTEM_TEAMPROJECT", "proj")
    monkeypatch.setenv("BUILD_REPOSITORY_ID", "repo")
    monkeypatch.setattr(pr_cli, "get_token_provider", lambda *_a, **_k: _token_provider())

    assert pr_cli._build_ado_provider()._client._base_url == expected_base


def test_pr_bot_refuses_a_collection_url_that_is_not_azure_devops_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import typer

    from sigantry_core.pr_bot import cli as pr_cli

    monkeypatch.setenv(
        "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "https://tfs.example.com/tfs/DefaultCollection/"
    )
    monkeypatch.setenv("SYSTEM_TEAMPROJECT", "proj")
    monkeypatch.setenv("BUILD_REPOSITORY_ID", "repo")

    with pytest.raises(typer.BadParameter, match="Azure DevOps"):
        pr_cli._build_ado_provider()


@pytest.mark.parametrize(
    "build",
    [_work_item_provider, _pr_provider, _approval_gate, _variable_group],
    ids=["workitems", "pr_bot", "approval_gate", "variable_group"],
)
@pytest.mark.parametrize(
    "given",
    ["contoso", "https://dev.azure.com/contoso/", "https://contoso.visualstudio.com/"],
)
def test_every_client_talks_to_one_organization_base_url(build, given: str) -> None:  # type: ignore[no-untyped-def]
    client = build(given)._client  # type: ignore[attr-defined]
    assert client._base_url == "https://dev.azure.com/contoso"
