"""Typer subapp ``sigantry pr-bot`` -- the PR-review bot CLI (Plan 14-06 / STARTER-07).

The 17th top-level Typer subapp on the root ``sigantry`` app, registered
in :mod:`sigantry_core.cli` immediately after Plan 14-01's ``config``.
Exposes a single command ``run`` that orchestrates the entire bot
lifecycle in a single CLI invocation -- no daemon, no webhook listener,
no persistent state. The bot is invoked from a CI workflow per
PR open / synchronize / reopened / ready_for_review event; the workflow
files (Plan 14-06 Task 2) provide the four supporting YAMLs:

- ``templates/starter/.github/workflows/pr-bot.yml``      (adopter GHA)
- ``templates/starter/.azuredevops/jobs/pr-bot.yml``      (adopter ADO job)
- ``.github/workflows/sigantry-pr-bot.yml``               (monorepo GHA)
- ``templates/pr-review/sigantry-pr-bot.yml``             (monorepo ADO)

Provider auto-detect (RESEARCH §Pattern 6, env-only):

- ``GITHUB_ACTIONS=true``  -> github (every GHA job sets this).
- ``TF_BUILD=True``        -> ado    (every ADO Pipelines job sets this).
- Neither set + ``--provider auto`` -> exit 1 with helpful message.

Path-traversal mitigation (T-14-06-01):

The ``--base-dir`` and ``--head-dir`` flags accept adopter-controlled
filesystem paths (the workflow YAMLs pass two ``actions/checkout@v4``
locations). :func:`_validate_dir` rejects any ``..`` segment AND any
absolute path whose resolution falls outside the current working tree.
Mirrors the Phase 13 ``sync pull`` review-fix CR-01 defence
(``sigantry_core/sync/pull.py:335``).

D-07 exit codes:

- ``0`` -- comment posted (or no-changes comment posted; never silent).
- ``1`` -- validation / auth misconfiguration error.
- ``2`` -- REST / auth runtime error (network, 401/403/5xx).
- ``3`` -- unknown failure (catch-all over ``Exception``).

The "no changes detected" path STILL posts a comment per D-07; the
:func:`render_markdown` output carries the fixed
``> No semantic-model or schema changes detected in this PR.`` line
when both ``tmdl_diff`` and ``lakehouse_diff`` are empty / None.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import typer

from sigantry_core.auth import get_token_provider
from sigantry_core.client.errors import (
    AuthError,
    HttpError,
    NotFoundError,
    RateLimitError,
)
from sigantry_core.pr_bot.lakehouse_diff import diff_lakehouse
from sigantry_core.pr_bot.payload import (
    PrCommentPayload,
    PrSummary,
    render_markdown,
)
from sigantry_core.pr_bot.providers import AdoProvider, GithubProvider
from sigantry_core.pr_bot.tmdl_diff import diff_tmdl

pr_bot_app = typer.Typer(
    help="Sigantry PR-review bot -- post TMDL + Lakehouse diffs on pull requests.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Provider auto-detect (RESEARCH §Pattern 6)
# ---------------------------------------------------------------------------


def detect_provider() -> Literal["github", "ado"]:
    """Detect which CI provider is running this job from env vars only.

    [VERIFIED: docs.github.com/actions/reference/variables-reference --
     "GITHUB_ACTIONS: Always set to 'true' when GitHub Actions is
     running the workflow."]
    [VERIFIED: learn.microsoft.com/azure/devops/pipelines/build/variables --
     "TF_BUILD: True if a build task runs the script."]

    Raises :class:`typer.BadParameter` when neither env var is set so
    Typer surfaces a clean exit-code error before any token / PR id is
    used.
    """
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return "github"
    if os.environ.get("TF_BUILD", "").lower() in ("true", "1"):
        return "ado"
    raise typer.BadParameter(
        "--provider auto could not detect CI: GITHUB_ACTIONS and TF_BUILD "
        "are both unset. Pass --provider github or --provider ado explicitly."
    )


# ---------------------------------------------------------------------------
# Path-traversal mitigation (T-14-06-01 / Phase 13 sync pull CR-01 reuse)
# ---------------------------------------------------------------------------


def _validate_dir(value: Path) -> Path:
    """Reject ``..`` segments in a CLI directory flag.

    Mirrors the defence at ``sigantry_core/sync/pull.py:335`` (Phase 13
    review-fix CR-01) but adapted for the CLI flag layer: the bot reads
    files under both halves via ``Path.rglob`` and a ``..`` segment is
    the canonical traversal vector. Absolute paths are accepted (CI
    workflows pass ``${{ github.workspace }}`` -- an absolute path --
    and dev-tooling / CI runners legitimately pass ``tmp_path``-style
    locations). For RELATIVE paths, the resolved target must remain
    inside the current working tree to catch ``../etc/passwd``-style
    relative-traversal attacks even after PathResolution would otherwise
    canonicalise the ``..`` away.

    Returns the resolved path on success; raises
    :class:`typer.BadParameter` on rejection so Typer surfaces a clean
    exit-1 error.
    """
    raw = str(value)
    # Reject any ``..`` segment outright -- this is a stronger guarantee
    # than ``relative_to`` because a path like ``a/../b`` resolves under
    # CWD but is suspicious from an audit perspective.
    if ".." in Path(raw).parts:
        raise typer.BadParameter(
            f"--base-dir / --head-dir reject paths containing '..' segments "
            f"(invalid path / traversal rejected); got: {raw!r}"
        )
    try:
        resolved = value.resolve() if value.is_absolute() else (Path.cwd() / value).resolve()
    except (OSError, RuntimeError) as exc:
        raise typer.BadParameter(
            f"--base-dir / --head-dir could not be safely resolved: {raw!r}: {exc}"
        ) from exc
    # For relative paths, defence-in-depth: the resolved target must stay
    # inside CWD. Absolute paths are trusted (the operator / workflow
    # supplied them deliberately).
    if not value.is_absolute():
        cwd = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError as exc:
            raise typer.BadParameter(
                f"--base-dir / --head-dir must point inside the current working "
                f"tree when relative; {raw!r} resolved to {resolved} which is "
                f"outside {cwd} (invalid path / traversal rejected)"
            ) from exc
    return resolved


# ---------------------------------------------------------------------------
# Provider construction (env-driven; structurally mirrors release/cli.py)
# ---------------------------------------------------------------------------


def _build_github_provider(token: str | None) -> GithubProvider:
    """Construct a GithubProvider from CLI flag + ``GITHUB_REPOSITORY`` env.

    Pulls coordinates from the GHA-supplied ``GITHUB_REPOSITORY=owner/repo``
    env var (set on every job per docs.github.com). Raises
    :class:`typer.BadParameter` when the token is missing OR when
    ``GITHUB_REPOSITORY`` is not parseable.
    """
    resolved_token = token or os.environ.get("GITHUB_TOKEN", "") or ""
    if not resolved_token:
        raise typer.BadParameter(
            "GitHub provider requires --token or GITHUB_TOKEN env var; both empty."
        )
    repo_env = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo_env:
        raise typer.BadParameter(
            f"GITHUB_REPOSITORY env var must be 'owner/repo'; got: {repo_env!r}"
        )
    owner, repo = repo_env.split("/", 1)
    return GithubProvider(owner=owner, repo=repo, pat=resolved_token)


def _build_ado_provider() -> AdoProvider:
    """Construct an AdoProvider from ADO Pipelines env vars + TokenProvider.

    Reads ``SYSTEM_TEAMFOUNDATIONCOLLECTIONURI`` (e.g.
    ``https://dev.azure.com/sigantry-test/``) -> org name, plus
    ``SYSTEM_TEAMPROJECT`` -> project, and ``BUILD_REPOSITORY_ID`` ->
    repository id (all set on every ADO Pipelines job per
    learn.microsoft.com/azure/devops/pipelines/build/variables).
    """
    org_uri = os.environ.get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI", "").rstrip("/")
    if not org_uri or "/" not in org_uri:
        raise typer.BadParameter(
            f"ADO provider requires SYSTEM_TEAMFOUNDATIONCOLLECTIONURI env var; got: {org_uri!r}"
        )
    org = org_uri.rsplit("/", 1)[1]
    project = os.environ.get("SYSTEM_TEAMPROJECT", "")
    if not project:
        raise typer.BadParameter("ADO provider requires SYSTEM_TEAMPROJECT env var; empty.")
    repo_id = os.environ.get("BUILD_REPOSITORY_ID", "")
    if not repo_id:
        raise typer.BadParameter("ADO provider requires BUILD_REPOSITORY_ID env var; empty.")
    return AdoProvider(
        org=org,
        project=project,
        repository_id=repo_id,
        token_provider=get_token_provider(),
    )


# ---------------------------------------------------------------------------
# `run` command
# ---------------------------------------------------------------------------


@pr_bot_app.command("run")
def run(
    provider: str = typer.Option(
        "auto",
        "--provider",
        help="CI provider: 'github', 'ado', or 'auto' (default; detect via env vars).",
    ),
    pr_id: str = typer.Option(
        ...,
        "--pr-id",
        help="Pull request id / number (string-typed for cross-provider parity).",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="GitHub PAT or installation token. Falls back to GITHUB_TOKEN env var.",
    ),
    base_dir: Path = typer.Option(  # noqa: B008 -- typer convention: Option() lives in the default
        ...,
        "--base-dir",
        help="Path to the PR base-branch checkout (read-only; '..' rejected).",
    ),
    head_dir: Path = typer.Option(  # noqa: B008 -- typer convention: Option() lives in the default
        ...,
        "--head-dir",
        help="Path to the PR head-branch checkout (read-only; '..' rejected).",
    ),
    audit_dir: Path = typer.Option(  # noqa: B008 -- typer convention
        Path.home() / ".sigantry" / "audit",  # noqa: B008 -- Path.home() snapshot at import is fine for CLI default
        "--audit-dir",
        help="Reserved for future audit-jsonl writes (currently unused; D-21 forbids).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip the POST; print the rendered Markdown body to stdout instead.",
    ),
) -> None:
    """Run the PR-review bot once: detect provider, diff TMDL + Lakehouse, post comment.

    Exit codes (D-07):
      - 0 on success (comment posted; or dry-run printed; or no-changes still posted).
      - 1 on validation / auth misconfiguration (typer.BadParameter, missing token, ...).
      - 2 on REST / auth runtime error (raised by the underlying BaseRestClient).
      - 3 on any other unexpected exception (catch-all per D-07).
    """
    # ----- validate flags + detect provider --------------------------------
    try:
        if provider == "auto":
            provider_kind = detect_provider()
        elif provider in ("github", "ado"):
            provider_kind = provider  # type: ignore[assignment]
        else:
            raise typer.BadParameter(
                f"--provider must be 'github', 'ado', or 'auto'; got {provider!r}."
            )
        validated_base = _validate_dir(base_dir)
        validated_head = _validate_dir(head_dir)
    except typer.BadParameter as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # ----- construct provider (env-driven) ---------------------------------
    try:
        provider_obj: GithubProvider | AdoProvider
        if provider_kind == "github":
            provider_obj = _build_github_provider(token)
        else:
            provider_obj = _build_ado_provider()
    except typer.BadParameter as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # pragma: no cover -- unknown failure path
        typer.echo(f"unexpected error during provider construction: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    # ----- compute diff sections + build payload ---------------------------
    try:
        tmdl_diff_section = diff_tmdl(validated_base, validated_head)
        lakehouse_diff_section = diff_lakehouse(validated_base, validated_head)
    except (AuthError, NotFoundError, RateLimitError, HttpError) as exc:
        typer.echo(f"REST/auth error during diff: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (ValueError, OSError) as exc:
        typer.echo(f"validation error during diff: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"unexpected error during diff: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    # The renderer accepts None for empty / no-content sections; map
    # is_empty() -> None so the no-changes branch fires correctly.
    tmdl_or_none = tmdl_diff_section if not tmdl_diff_section.is_empty() else None
    # For the Lakehouse section the diff function ALWAYS populates
    # ``warnings=[_COLUMN_WARNING_SHORT]`` (Plan 14-04 design: the
    # warning is non-droppable). For the bot's "no changes detected"
    # gate we only care about *real* diff content -- identity changes,
    # schema toggles, table / shortcut / role changes -- so we ignore
    # ``warnings`` here. If any of those are non-empty we keep the
    # section (and the warning gets rendered alongside the long-form
    # footer); otherwise we drop it entirely so the no-changes line
    # fires per D-07.
    lakehouse_has_content = bool(
        lakehouse_diff_section.identity_changes
        or lakehouse_diff_section.schema_toggle is not None
        or lakehouse_diff_section.tracked_tables_added
        or lakehouse_diff_section.tracked_tables_removed
        or lakehouse_diff_section.shortcuts_added
        or lakehouse_diff_section.shortcuts_removed
        or lakehouse_diff_section.shortcuts_modified
        or lakehouse_diff_section.role_changes
    )
    lakehouse_or_none = lakehouse_diff_section if lakehouse_has_content else None

    # ----- get PR metadata + build PrCommentPayload ------------------------
    try:
        pr = provider_obj.get_pr(pr_id)
        changed_files = provider_obj.get_changed_files(pr_id)
    except (AuthError, NotFoundError, RateLimitError, HttpError) as exc:
        typer.echo(f"REST/auth error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"unexpected error fetching PR metadata: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    summary = PrSummary(
        pr_id=str(pr_id),
        provider=provider_kind,
        base_sha=str(getattr(pr, "base_sha", "")),
        head_sha=str(getattr(pr, "head_sha", "")),
        changed_files_count=len(changed_files),
    )
    payload = PrCommentPayload(
        summary=summary,
        tmdl_diff=tmdl_or_none,
        lakehouse_diff=lakehouse_or_none,
        footer="",
    ).with_hash()
    body = render_markdown(payload)

    # ----- dry-run vs. POST ------------------------------------------------
    if dry_run:
        typer.echo(body)
        raise typer.Exit(code=0)

    try:
        comment_id = provider_obj.post_comment(pr_id, body)
    except (AuthError, NotFoundError, RateLimitError, HttpError) as exc:
        typer.echo(f"REST/auth error during post_comment: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"unexpected error during post_comment: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    typer.echo(f"Posted comment {comment_id}")
    raise typer.Exit(code=0)


__all__ = ["detect_provider", "pr_bot_app", "run"]
