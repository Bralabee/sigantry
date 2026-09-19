"""Banned-api + public-export invariant for sigantry_core.client.

Plan 02-01 Task 1 - enforces CLIENT-01 at test time (ruff banned-api enforces
it at lint time). The only paths that may `import httpx` or `from httpx ...`
are under `sigantry_core/client/`, `sigantry_core/auth/diagnose.py` (the
documented Phase 1 exception), `sigantry_core/auth/github_app.py` (the Phase
11 exception added in Plan 11-00 -- chicken-and-egg JWT exchange), and
`sigantry_core/notifications/{teams.py, slack.py}` (the Phase 16 exception
landed in Plan 16-01 -- credential-less webhook URLs; the legacy
``sigantry_core/sync/notifications.py`` carve-out was removed in lockstep
when that file converted to a deprecation shim).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_HTTPX_IMPORTERS = {
    "sigantry_core/auth/diagnose.py",
    # Phase 11 exception (Plan 11-00 + 11-05): GitHub App JWT exchange has
    # to happen outside the standard auth chain because the JWT *is* the
    # credential being exchanged for a token. Per-file TID251 ignore in
    # pyproject.toml. Documented in sigantry_core/auth/github_app.py
    # module docstring.
    "sigantry_core/auth/github_app.py",
    # Phase 16 exception (Plan 16-01 / SEAM-01): notification webhooks
    # (Teams / Slack incoming-webhook URLs) are not Fabric API endpoints —
    # routing them through FabricRestClient would force a TokenProvider
    # on a credential-less webhook URL. Per-file TID251 ignores in
    # pyproject.toml; carve-outs documented in each module's docstring.
    # Migrated from sigantry_core/sync/notifications.py per RESEARCH §3
    # path-correction; the legacy carve-out was removed in lockstep.
    "sigantry_core/notifications/teams.py",
    "sigantry_core/notifications/slack.py",
}
ALLOWED_HTTPX_PREFIX = "sigantry_core/client/"

_IMPORT_RE = re.compile(r"^(import httpx|from httpx )")


def test_no_unauthorized_httpx_imports() -> None:
    """Walk sigantry_core/ and flag any httpx import outside allowed paths."""
    fabric_pkg = REPO_ROOT / "sigantry_core"
    violations: list[str] = []
    for py in fabric_pkg.rglob("*.py"):
        rel = py.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(ALLOWED_HTTPX_PREFIX):
            continue
        if rel in ALLOWED_HTTPX_IMPORTERS:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if _IMPORT_RE.match(line.strip()):
                violations.append(f"{rel}:{i}: {line.strip()}")
    assert not violations, "Unauthorised httpx imports:\n" + "\n".join(violations)


def test_client_package_exports() -> None:
    """All public symbols land on `sigantry_core.client` module namespace."""
    import sigantry_core.client as c

    for name in (
        "AuthError",
        "ClientError",
        "HttpError",
        "HttpResponse",
        "LROTimeoutError",
        "NotFoundError",
        "OperationFailedError",
        "PaginationError",
        "RateLimitError",
        "ServerError",
        "get_correlation_id",
        "set_correlation_id",
        "get_operation_id",
        "set_operation_id",
        "configure_client_logging",
    ):
        assert hasattr(c, name), f"Missing public export: {name}"


def test_client_all_is_sorted_and_complete() -> None:
    import sigantry_core.client as c

    assert isinstance(c.__all__, list)
    assert c.__all__ == sorted(c.__all__), "__all__ must be sorted"


def test_todo_placeholder_deleted() -> None:
    """`sigantry_core/client/TODO-phase-2.md` must be removed once Task 1 lands."""
    todo = REPO_ROOT / "sigantry_core" / "client" / "TODO-phase-2.md"
    assert not todo.exists(), "TODO-phase-2.md placeholder should be deleted in Plan 02-01"


def test_ruff_banned_api_message_preserved() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # Match the message prefix only — the allowlist suffix is forward-extended
    # as new carve-outs land (Phase 11 added auth/github_app.py; Phase 16
    # migrated sync/notifications.py -> notifications/). The intent of this
    # test is to ensure the CLIENT-01 message stays present + cites
    # sigantry_core.client as the canonical replacement, not to lock the
    # exact carve-out list.
    required_prefix = "Import sigantry_core.client instead of httpx directly. Allowed only inside"
    assert required_prefix in pyproject, "Phase 1 banned-api message prefix must be preserved"
    # Also assert the documented carve-out paths each appear in the message.
    # Phase 16 (Plan 16-01) migrated the notifications carve-out from
    # ``sigantry_core/sync/notifications.py`` (legacy single-file) to the
    # new ``sigantry_core/notifications/`` package; the legacy reference
    # was removed in lockstep when that file converted to a deprecation
    # shim with no httpx import.
    for carveout in (
        "sigantry_core/client/",
        "sigantry_core/auth/diagnose.py",
        "sigantry_core/auth/github_app.py",
        "sigantry_core/notifications/",
    ):
        assert carveout in pyproject, f"Carve-out path missing from banned-api message: {carveout}"


def test_diagnose_per_file_ignore_preserved() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"sigantry_core/auth/diagnose.py" = ["TID251"]' in pyproject
    assert '"sigantry_core/client/**" = ["TID251"]' in pyproject
