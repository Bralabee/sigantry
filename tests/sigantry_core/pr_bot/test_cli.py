"""CliRunner tests for ``sigantry pr-bot run`` (Plan 14-06 Task 1 / STARTER-07 CLI).

The pr-bot CLI is the 17th top-level Typer subapp on ``sigantry``. ``run``
orchestrates: (1) provider auto-detect from CI env vars, (2) path-traversal
validation on ``--base-dir`` / ``--head-dir``, (3) diff_tmdl + diff_lakehouse,
(4) PrCommentPayload + render_markdown + provider.post_comment, with D-07
exit codes (0 posted, 1 validation, 2 REST/auth, 3 unknown).

8 tests cover the full success + failure surface:

1. test_pr_bot_run_dry_run_exits_zero                          -- dry-run: render to stdout, no POST.
2. test_pr_bot_auto_detect_falls_back_to_github_when_GITHUB_ACTIONS_set
                                                              -- auto -> github via env.
3. test_pr_bot_auto_detect_falls_back_to_ado_when_TF_BUILD_set -- auto -> ado via env.
4. test_pr_bot_auto_detect_unset_exits_one                     -- both env vars unset -> exit 1.
5. test_pr_bot_explicit_provider_github_no_token_exits_one     -- missing token -> exit 1.
6. test_pr_bot_path_traversal_rejected                         -- ``..`` / absolute-outside -> exit 1.
7. test_pr_bot_no_changes_detected_still_posts                 -- empty diffs still post (D-07).
8. test_pr_bot_unknown_failure_exits_three                     -- KeyError catch-all -> exit 3.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.pr_bot.payload import (
    LakehouseDiffSection,
    TmdlDiffSection,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create empty base + head directory pair under tmp_path.

    The bot's diff functions accept any directory (empty trees produce
    empty diff sections). Empty fixtures keep the tests hermetic and
    portable -- they don't depend on the larger TMDL fixture trees.
    """
    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    return base, head


@pytest.fixture
def patched_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[MagicMock, MagicMock]]:
    """Replace GithubProvider + AdoProvider constructors with MagicMocks.

    Yields ``(github_factory, ado_factory)``; both factories return a
    MagicMock instance whose ``get_pr`` / ``get_changed_files`` /
    ``post_comment`` are pre-stubbed so the orchestration runs through
    cleanly without any HTTP. The CLI module imports providers via the
    package-level ``sigantry_core.pr_bot.providers`` re-exports, so we
    patch on that module's namespace.
    """
    fake_github_instance = MagicMock(name="GithubProviderInstance")
    fake_github_instance.name = "github"
    # PR + changed files have permissive shapes; the renderer doesn't
    # reach into the PullRequest fields beyond the summary.
    fake_pr = MagicMock(name="PullRequest")
    fake_pr.id = "42"
    fake_pr.title = "Test PR"
    fake_pr.base_sha = "aaaa1111"
    fake_pr.head_sha = "bbbb2222"
    fake_pr.base_ref = "main"
    fake_pr.head_ref = "feature/x"
    fake_github_instance.get_pr.return_value = fake_pr
    fake_github_instance.get_changed_files.return_value = []
    fake_github_instance.post_comment.return_value = "comment-id-1"

    fake_ado_instance = MagicMock(name="AdoProviderInstance")
    fake_ado_instance.name = "ado"
    fake_ado_instance.get_pr.return_value = fake_pr
    fake_ado_instance.get_changed_files.return_value = []
    fake_ado_instance.post_comment.return_value = "thread-id-1"

    fake_github_factory = MagicMock(name="GithubProvider", return_value=fake_github_instance)
    fake_ado_factory = MagicMock(name="AdoProvider", return_value=fake_ado_instance)

    import sigantry_core.pr_bot.cli as cli_mod

    monkeypatch.setattr(cli_mod, "GithubProvider", fake_github_factory)
    monkeypatch.setattr(cli_mod, "AdoProvider", fake_ado_factory)
    yield fake_github_factory, fake_ado_factory


@pytest.fixture(autouse=True)
def _clear_ci_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the auto-detect env vars before each test.

    Tests that need a specific provider auto-detection set the relevant
    env var explicitly via ``monkeypatch.setenv``.
    """
    for key in (
        "GITHUB_ACTIONS",
        "TF_BUILD",
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI",
        "SYSTEM_TEAMPROJECT",
        "BUILD_REPOSITORY_ID",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Test 1 -- dry-run path
# ---------------------------------------------------------------------------


def test_pr_bot_run_dry_run_exits_zero(
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--dry-run`` prints the rendered Markdown to stdout, exits 0, never POSTs."""
    base_dir, head_dir = fixture_dirs
    monkeypatch.setenv("GITHUB_REPOSITORY", "sigantry/test-repo")

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--pr-id",
            "42",
            "--token",
            "fake-pat-x",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    # Empty diffs -> no-changes line in rendered body.
    assert "No semantic-model or schema changes detected" in result.stdout
    # post_comment must NOT be called in dry-run mode.
    fake_github_factory, _ = patched_providers
    fake_github_instance = fake_github_factory.return_value
    fake_github_instance.post_comment.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 -- auto-detect github
# ---------------------------------------------------------------------------


def test_pr_bot_auto_detect_falls_back_to_github_when_GITHUB_ACTIONS_set(  # noqa: N802 -- GITHUB_ACTIONS is an env-var name
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GITHUB_ACTIONS=true`` triggers GithubProvider construction under ``--provider auto``."""
    base_dir, head_dir = fixture_dirs
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "sigantry/test")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-pat-x")

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "auto",
            "--pr-id",
            "42",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "could not detect" not in result.stdout
    fake_github_factory, fake_ado_factory = patched_providers
    fake_github_factory.assert_called_once()
    fake_ado_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 -- auto-detect ado
# ---------------------------------------------------------------------------


def test_pr_bot_auto_detect_falls_back_to_ado_when_TF_BUILD_set(  # noqa: N802 -- TF_BUILD is an env-var name
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TF_BUILD=True`` triggers AdoProvider construction under ``--provider auto``."""
    base_dir, head_dir = fixture_dirs
    monkeypatch.setenv("TF_BUILD", "True")
    monkeypatch.setenv("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "https://dev.azure.com/sigantry-test/")
    monkeypatch.setenv("SYSTEM_TEAMPROJECT", "sigantry-starter-test")
    monkeypatch.setenv("BUILD_REPOSITORY_ID", "00000000-0000-0000-0000-000000000aaa")

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "auto",
            "--pr-id",
            "42",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    fake_github_factory, fake_ado_factory = patched_providers
    fake_ado_factory.assert_called_once()
    fake_github_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4 -- auto-detect unset -> exit 1
# ---------------------------------------------------------------------------


def test_pr_bot_auto_detect_unset_exits_one(
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
) -> None:
    """Both ``GITHUB_ACTIONS`` and ``TF_BUILD`` unset -> exit 1 with helpful message."""
    base_dir, head_dir = fixture_dirs

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "auto",
            "--pr-id",
            "42",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    combined = (result.stdout or "") + (result.stderr or "")
    assert "could not detect" in combined.lower() or "auto" in combined.lower()


# ---------------------------------------------------------------------------
# Test 5 -- explicit github + no token -> exit 1
# ---------------------------------------------------------------------------


def test_pr_bot_explicit_provider_github_no_token_exits_one(
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
) -> None:
    """``--provider github`` without ``--token`` and no ``GITHUB_TOKEN`` env -> exit 1."""
    base_dir, head_dir = fixture_dirs

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--pr-id",
            "42",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"


# ---------------------------------------------------------------------------
# Test 6 -- path-traversal rejected
# ---------------------------------------------------------------------------


def test_pr_bot_path_traversal_rejected(
    patched_providers: tuple[MagicMock, MagicMock],
    tmp_path: Path,
) -> None:
    """``--base-dir`` containing a ``..`` segment is rejected with exit 1.

    The ``_validate_dir`` helper rejects any path containing a literal
    ``..`` segment (even inside otherwise-CWD-relative paths) AS WELL as
    absolute paths whose resolution falls outside the CWD subtree. This
    test exercises the ``..`` rejection branch.
    """
    head = tmp_path / "head"
    head.mkdir()

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--token",
            "fake-pat-x",
            "--pr-id",
            "42",
            "--base-dir",
            "../etc/passwd",
            "--head-dir",
            str(head),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    combined = (result.stdout or "") + (result.stderr or "")
    lc = combined.lower()
    assert "invalid path" in lc or "outside" in lc or ".." in combined or "traversal" in lc


# ---------------------------------------------------------------------------
# Test 7 -- no changes still posts (D-07 invariant)
# ---------------------------------------------------------------------------


def test_pr_bot_no_changes_detected_still_posts(
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty diff sections still trigger ``post_comment`` (D-07: never silent)."""
    base_dir, head_dir = fixture_dirs
    monkeypatch.setenv("GITHUB_REPOSITORY", "sigantry/test-repo")

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--token",
            "fake-pat-x",
            "--pr-id",
            "42",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    fake_github_factory, _ = patched_providers
    fake_instance = fake_github_factory.return_value
    fake_instance.post_comment.assert_called_once()
    # The body argument MUST contain the no-changes line.
    posted_body = fake_instance.post_comment.call_args.args[1]
    assert "No semantic-model or schema changes detected" in posted_body


# ---------------------------------------------------------------------------
# Test 8 -- unknown failure -> exit 3
# ---------------------------------------------------------------------------


def test_pr_bot_unknown_failure_exits_three(
    fixture_dirs: tuple[Path, Path],
    patched_providers: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic ``KeyError`` from ``diff_tmdl`` -> exit 3 (catch-all per D-07)."""
    base_dir, head_dir = fixture_dirs
    monkeypatch.setenv("GITHUB_REPOSITORY", "sigantry/test-repo")

    def _raises_keyerror(*_args: Any, **_kwargs: Any) -> TmdlDiffSection:
        raise KeyError("synthetic")

    import sigantry_core.pr_bot.cli as cli_mod

    monkeypatch.setattr(cli_mod, "diff_tmdl", _raises_keyerror)

    result = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--token",
            "fake-pat-x",
            "--pr-id",
            "42",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert result.exit_code == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"


# ---------------------------------------------------------------------------
# Construction smoke -- pr-bot subapp registered + run subcommand exists
# ---------------------------------------------------------------------------


def test_pr_bot_subapp_listed_in_root_help() -> None:
    """``sigantry --help`` lists ``pr-bot`` (regression catcher for the 17th-subapp registration)."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "pr-bot" in result.stdout


def test_pr_bot_run_subcommand_listed_in_pr_bot_help() -> None:
    """``sigantry pr-bot --help`` lists ``run``."""
    result = runner.invoke(app, ["pr-bot", "--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout


# ---------------------------------------------------------------------------
# Helper: silence unused-import warnings on the type-only LakehouseDiffSection.
# ---------------------------------------------------------------------------


_ = LakehouseDiffSection  # kept for parity with future tests in this file.
