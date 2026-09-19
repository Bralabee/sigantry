"""CliRunner tests for ``sigantry release record`` (Plan 11-06, TRACE-05).

The release-record subapp is the 13th top-level ``sigantry`` subapp.
``sigantry release record`` builds a :class:`DeployRecord`, calls
:func:`emit_deploy_record` (audit-jsonl write-through), and asks the
chosen :class:`WorkItemProvider` to post structured comments on every
linked work-item / issue.

Test coverage (8 tests across success + failure + help paths):

- ``release`` shows up in the root ``sigantry --help`` output.
- ``record`` shows up in ``sigantry release --help`` output.
- ADO-provider success path: jsonl line written + provider.link_release
  called with the right args; exit 0.
- GitHub-provider PAT-mode success path: provider __init__ receives
  ``pat=...`` + ``link_release`` is called; exit 0.
- Invalid ``--provider`` exits non-zero (Click ``BadParameter``).
- Missing ``--ado-organization`` for ``--provider=ado`` exits non-zero.
- Bad JSON in ``--test-evidence`` exits non-zero.
- Empty ``--work-items`` exits non-zero.

Tests do NOT hit the network: the ADO branch monkey-patches
``AdoWorkItemProvider.from_defaults`` and the GitHub branch
monkey-patches ``GithubWorkItemProvider.__init__`` so neither path
constructs a real :class:`BaseRestClient`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _common_args(work_items: str = "1234,5678", **overrides: str) -> list[str]:
    """Return the always-required ``record`` flag-set as a flattened arg list."""
    defaults: dict[str, str] = {
        "--release-id": "R-2026-04-26-1",
        "--workspace": "ws-prod",
        "--work-items": work_items,
        "--approver": "alice@example.invalid",
        "--fabric-items": "nb.Notebook",
        "--test-evidence": '{"smoke": "passed"}',
    }
    defaults.update(overrides)
    args: list[str] = []
    for key, value in defaults.items():
        args.extend([key, value])
    return args


@pytest.fixture
def patched_ado_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[MagicMock, MagicMock]]:
    """Replace ``AdoWorkItemProvider.from_defaults`` with a MagicMock factory.

    Yields ``(factory, provider)`` so the caller can assert on both the
    class-method call args and the resulting instance's ``link_release``
    invocation.
    """
    fake_provider = MagicMock(name="AdoWorkItemProviderInstance")
    fake_factory = MagicMock(name="from_defaults", return_value=fake_provider)

    import sigantry_core.workitems.ado as ado_mod

    monkeypatch.setattr(ado_mod.AdoWorkItemProvider, "from_defaults", fake_factory)
    yield fake_factory, fake_provider


@pytest.fixture
def patched_github_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[dict[str, Any], MagicMock]]:
    """Replace ``GithubWorkItemProvider.__init__`` so we can capture kwargs.

    The full ``__init__`` builds a :class:`BaseRestClient` which would in
    turn try to resolve a TokenProvider; replacing the constructor wholesale
    keeps the test hermetic. Yields ``(captured_kwargs, link_release_mock)``.
    """
    captured_kwargs: dict[str, Any] = {}
    fake_link_release = MagicMock(name="link_release")

    def _fake_init(self: Any, **kwargs: Any) -> None:
        captured_kwargs.update(kwargs)
        for key, value in kwargs.items():
            setattr(self, f"_{key}", value)
        # Stub the public method so the CLI's downstream call is a no-op.
        self.link_release = fake_link_release
        self.name = "github"

    import sigantry_core.workitems.github as gh_mod

    monkeypatch.setattr(gh_mod.GithubWorkItemProvider, "__init__", _fake_init)
    yield captured_kwargs, fake_link_release


# ---------------------------------------------------------------------------
# Help-output tests
# ---------------------------------------------------------------------------


def test_release_subapp_listed_in_root_help() -> None:
    """``sigantry --help`` lists ``release`` as a top-level subapp."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    assert "release" in result.stdout


def test_release_record_subcommand_listed_in_release_help() -> None:
    """``sigantry release --help`` lists ``record``."""
    result = runner.invoke(app, ["release", "--help"])
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    assert "record" in result.stdout


# ---------------------------------------------------------------------------
# Success-path tests
# ---------------------------------------------------------------------------


def test_record_invocation_with_ado_provider_writes_jsonl_and_calls_link_release(
    tmp_path: Path,
    patched_ado_provider: tuple[MagicMock, MagicMock],
) -> None:
    """``--provider ado`` builds a DeployRecord, writes audit jsonl, calls link_release."""
    fake_factory, fake_provider = patched_ado_provider

    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "ado",
            "--ado-organization",
            "myorg",
            "--ado-project",
            "myproj",
            "--audit-dir",
            str(tmp_path),
            *_common_args(),
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, exception={result.exception!r}"

    # Provider factory called once with the right kwargs.
    fake_factory.assert_called_once()
    factory_kwargs = fake_factory.call_args.kwargs
    assert factory_kwargs["organization"] == "myorg"
    assert factory_kwargs["project"] == "myproj"
    assert factory_kwargs["tenant_id"] is None

    # link_release called once with (release_id, ids, deploy_record).
    fake_provider.link_release.assert_called_once()
    lr_args = fake_provider.link_release.call_args.args
    assert lr_args[0] == "R-2026-04-26-1"
    assert lr_args[1] == ["1234", "5678"]
    # The third positional arg is the DeployRecord with its hash populated.
    deploy_record = lr_args[2]
    assert deploy_record.release_id == "R-2026-04-26-1"
    assert len(deploy_record.audit_hash) == 64  # SHA-256 hex

    # jsonl file appended (one line).
    jsonl = tmp_path / "deploys.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text("utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["release_id"] == "R-2026-04-26-1"
    assert parsed["work_items"] == ["1234", "5678"]
    assert parsed["fabric_items_changed"] == ["nb.Notebook"]
    assert parsed["test_evidence"] == {"smoke": "passed"}
    assert parsed["approver"] == "alice@example.invalid"
    assert parsed["workspace"] == "ws-prod"
    assert len(parsed["audit_hash"]) == 64


def test_record_invocation_with_github_pat_calls_link_release(
    tmp_path: Path,
    patched_github_provider: tuple[dict[str, Any], MagicMock],
) -> None:
    """``--provider github --github-pat`` constructs the provider with the PAT."""
    captured_kwargs, fake_link_release = patched_github_provider

    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "github",
            "--github-owner",
            "myorg",
            "--github-repo",
            "myrepo",
            "--github-pat",
            "fake-pat-test",
            "--audit-dir",
            str(tmp_path),
            *_common_args(work_items="42"),
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, exception={result.exception!r}"
    assert captured_kwargs["owner"] == "myorg"
    assert captured_kwargs["repo"] == "myrepo"
    assert captured_kwargs["pat"] == "fake-pat-test"
    fake_link_release.assert_called_once()

    # Audit jsonl was still written even though we patched the provider's
    # __init__ -- emit_deploy_record runs before link_release.
    jsonl = tmp_path / "deploys.jsonl"
    assert jsonl.exists()
    parsed = json.loads(jsonl.read_text("utf-8").splitlines()[0])
    assert parsed["work_items"] == ["42"]


def test_record_invocation_with_github_token_envvar_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_github_provider: tuple[dict[str, Any], MagicMock],
) -> None:
    """``--github-pat`` resolves from the ``GITHUB_TOKEN`` env var when omitted.

    Operators (and the Phase 16 jtoye live e2e) set ``GITHUB_TOKEN`` in the
    process environment instead of threading a flag through every call.
    Mirrors the gh CLI / sigantry pr-bot convention.
    """
    captured_kwargs, fake_link_release = patched_github_provider
    monkeypatch.setenv("GITHUB_TOKEN", "envvar-pat-from-fallback")

    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "github",
            "--github-owner",
            "envowner",
            "--github-repo",
            "envrepo",
            # NO --github-pat -- the envvar must supply it.
            "--audit-dir",
            str(tmp_path),
            *_common_args(work_items="99"),
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}, exception={result.exception!r}"
    assert captured_kwargs["pat"] == "envvar-pat-from-fallback"
    fake_link_release.assert_called_once()


# ---------------------------------------------------------------------------
# Failure-path tests
# ---------------------------------------------------------------------------


def test_invalid_provider_exits_nonzero(tmp_path: Path) -> None:
    """``--provider bitbucket`` fails the typer.BadParameter gate."""
    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "bitbucket",
            "--audit-dir",
            str(tmp_path),
            *_common_args(),
        ],
    )
    assert result.exit_code != 0


def test_missing_ado_organization_for_ado_provider_exits_nonzero(
    tmp_path: Path,
) -> None:
    """``--provider=ado`` without ``--ado-organization`` raises BadParameter."""
    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "ado",
            # NO --ado-organization
            "--ado-project",
            "myproj",
            "--audit-dir",
            str(tmp_path),
            *_common_args(),
        ],
    )
    assert result.exit_code != 0


def test_invalid_test_evidence_json_exits_nonzero(
    tmp_path: Path,
    patched_ado_provider: tuple[MagicMock, MagicMock],
) -> None:
    """``--test-evidence not-json`` fails JSON parse and exits non-zero.

    Patch the provider factory so the test isolates the JSON-validation
    failure from the (separately-tested) provider construction path.
    """
    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "ado",
            "--ado-organization",
            "myorg",
            "--ado-project",
            "myproj",
            "--audit-dir",
            str(tmp_path),
            "--release-id",
            "R7",
            "--workspace",
            "ws",
            "--work-items",
            "1",
            "--approver",
            "a",
            "--test-evidence",
            "not-json",
        ],
    )
    assert result.exit_code != 0


def test_empty_work_items_exits_nonzero(
    tmp_path: Path,
    patched_ado_provider: tuple[MagicMock, MagicMock],
) -> None:
    """``--work-items ''`` fails the explicit empty-list guard."""
    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "ado",
            "--ado-organization",
            "myorg",
            "--ado-project",
            "myproj",
            "--audit-dir",
            str(tmp_path),
            "--release-id",
            "R7",
            "--workspace",
            "ws",
            "--work-items",
            "",
            "--approver",
            "a",
        ],
    )
    assert result.exit_code != 0
