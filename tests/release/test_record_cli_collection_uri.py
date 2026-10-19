"""``release record --provider ado`` accepts what the CD template passes.

``templates/stages/sigantry-cd.yml`` runs ``--ado-organization "$(System.CollectionUri)"``,
which is a full URL. The other tests in this directory replace
``AdoWorkItemProvider.from_defaults`` with a mock, so none of them ever built the base
URL; here the real provider is constructed and the URL it will call is read back.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.auth import TokenProvider
from sigantry_core.cli import app
from sigantry_core.workitems import ado as ado_mod

runner = CliRunner()


def _record_args(organization: str, audit_dir: Path) -> list[str]:
    return [
        "release",
        "record",
        "--provider",
        "ado",
        "--ado-organization",
        organization,
        "--ado-project",
        "proj",
        "--audit-dir",
        str(audit_dir),
        "--release-id",
        "R-1",
        "--workspace",
        "ws-prod",
        "--work-items",
        "1234",
        "--approver",
        "alice@example.invalid",
        "--fabric-items",
        "nb.Notebook",
    ]


@pytest.fixture
def seen_base_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Build the real provider; record the base URL it holds when it is asked to link."""
    tp = MagicMock(spec=TokenProvider)
    tp.last_credential_class.return_value = "MockCredential"
    monkeypatch.setattr(ado_mod, "get_token_provider", lambda **_k: tp)

    seen: list[str] = []

    def _link_release(self: ado_mod.AdoWorkItemProvider, *_a: object, **_k: object) -> None:
        seen.append(self._client._base_url)

    monkeypatch.setattr(ado_mod.AdoWorkItemProvider, "link_release", _link_release)
    return seen


@pytest.mark.parametrize(
    "organization",
    ["contoso", "https://dev.azure.com/contoso/", "https://contoso.visualstudio.com/"],
)
def test_the_provider_calls_one_organization_whatever_form_it_was_given(
    organization: str, tmp_path: Path, seen_base_urls: list[str]
) -> None:
    result = runner.invoke(app, _record_args(organization, tmp_path))

    assert result.exit_code == 0, f"{result.output!r} {result.exception!r}"
    assert seen_base_urls == ["https://dev.azure.com/contoso"]


def test_a_url_that_is_not_an_azure_devops_services_collection_is_a_usage_error(
    tmp_path: Path, seen_base_urls: list[str]
) -> None:
    result = runner.invoke(
        app, _record_args("https://tfs.example.com/tfs/DefaultCollection/", tmp_path)
    )

    assert result.exit_code == 2, f"{result.output!r} {result.exception!r}"
    assert "Azure DevOps" in result.output
    assert seen_base_urls == []
    assert not (tmp_path / "deploys.jsonl").exists(), "a usage error writes no audit record"
