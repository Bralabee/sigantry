"""Audit-2026-05-07 W2.3 -- meta-gate: every module-scope vendor import in
``sigantry_core/`` lives at an explicitly-allowlisted path.

Path B chosen at audit synthesis: **the base ships reference impls**.
The base package is allowed to carry concrete reference implementations
of seam plugins (the in-house HTTP wrapper at ``client/``, the
``DefaultAzureCredential``-backed token provider at ``auth/``, the
Key Vault and GitHub Secrets ``SecretStore`` reference impls, etc.) --
but only at paths that own that dependency surface. Random business-
logic modules must not start importing ``azure.identity`` or
``fabric_cicd`` directly; if they need those facilities they go through
``sigantry_core.client`` / ``sigantry_core.auth`` / ``sigantry_core.deploy``.

The allowlist is the fence. Adding an entry should require reviewer
attention -- the rationale string documents *why* the exemption is
justified, the same shape as the phase8 file-allowlist.

Vendor roots scanned (top-level dotted prefix):

- ``azure``  -- azure-identity, azure-keyvault, azure-core, etc.
- ``httpx``
- ``fabric_cicd``
- ``msfabricpysdkcore``
- ``semantic_link_labs``
- ``nacl``  -- libsodium SealedBox (used by the GitHub Secrets impl)

Falsifiability contract: this test FAILS against the pre-W2.3 tree if
any new module under ``sigantry_core/`` imports a vendor package at
module scope -- the audit synthesis flagged that the existing
``flake8-tidy-imports.banned-api`` rule in ``pyproject.toml`` only
covered ``httpx``, leaving ``azure.*`` / ``fabric_cicd`` / ``nacl``
silently permissible anywhere.

The companion ``test_vendor_allowlist_has_no_dead_entries`` ratchets
the allowlist downward over time: remove a vendor import from a file
and the allowlist entry must come out at the same commit.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGANTRY_CORE = REPO_ROOT / "sigantry_core"

# Top-level dotted prefixes scanned by this gate. Each name is the
# leftmost segment of the dotted import path -- ``azure.identity``
# matches ``azure``, ``fabric_cicd.constants`` matches ``fabric_cicd``.
_VENDOR_ROOTS: frozenset[str] = frozenset(
    {
        "azure",
        "httpx",
        "fabric_cicd",
        "msfabricpysdkcore",
        "semantic_link_labs",
        "nacl",
    }
)


# Paths (relative to ``sigantry_core/``) where a given vendor import is
# allowed at module scope. Each entry comes with a one-line rationale
# documenting *why* the exemption is justified.
#
# Editing rules:
#   - Adding an entry: reviewer must justify why this module legitimately
#     owns the dependency surface, not why it's "convenient" to import.
#   - Removing an import from a module: drop the matching allowlist entry
#     in the same commit. ``test_vendor_allowlist_has_no_dead_entries``
#     enforces this.
#   - Adding a new vendor root: also extend ``_VENDOR_ROOTS`` above and
#     ``[tool.ruff.lint.flake8-tidy-imports.banned-api]`` in
#     ``pyproject.toml`` if the rule should also surface in the editor.
_VENDOR_IMPORT_ALLOWLIST: dict[tuple[str, str], str] = {
    # ---- HTTP wrapper ----------------------------------------------------
    # The in-house ``sigantry_core.client`` package is the only place
    # ``httpx`` should be imported. Every other module routes through
    # ``BaseRestClient`` / ``FabricRestClient`` so a single retry +
    # timeout + token-refresh policy applies.
    ("client/arm.py", "httpx"): "in-house ARM REST client",
    ("client/base.py", "httpx"): "in-house BaseRestClient -- the toolkit's only httpx-aware module",
    ("client/fabric.py", "httpx"): "in-house FabricRestClient",
    ("client/powerbi.py", "httpx"): "in-house Power BI REST client",
    ("client/purview.py", "httpx"): "in-house Purview REST client",
    (
        "client/retry.py",
        "httpx",
    ): "method-aware tenacity retry policy -- needs httpx exception types",
    # ---- Auth subpackage ------------------------------------------------
    # DefaultAzureCredential resolution + Key Vault token broker live
    # here so the credential-acquisition surface has one owner.
    ("auth/token_provider.py", "azure"): (
        "DefaultAzureCredential resolution -- the canonical token provider"
    ),
    ("auth/keyvault.py", "azure"): "Key Vault SecretClient for credential fetch",
    ("auth/diagnose.py", "httpx"): (
        "Phase 1 diagnose-auth probe -- one-shot raw HTTP call before "
        "the cached client exists; documented exception in pyproject "
        "per-file-ignores"
    ),
    ("auth/github_app.py", "httpx"): (
        "Phase 11 GitHub App JWT exchange -- per-installation token "
        "mint cannot route through FabricRestClient (different tenant + "
        "auth scheme); documented exception in pyproject per-file-ignores"
    ),
    # ---- Secrets subpackage ---------------------------------------------
    # Reference implementations for the SecretStore protocol. Each impl
    # owns its own vendor surface and tests opt-in via importorskip.
    ("secrets/key_vault.py", "azure"): (
        "KeyVaultSecretStore reference impl -- azure.identity + "
        "azure.keyvault.secrets + azure.core.exceptions"
    ),
    ("secrets/github_secrets.py", "nacl"): (
        "GithubSecretsSecretStore reference impl -- libsodium SealedBox "
        "encryption for GitHub Actions secrets API"
    ),
    # ---- Notifications subpackage ---------------------------------------
    # Phase 16 webhook adapters. Routing through FabricRestClient would
    # force a TokenProvider on credential-less Teams/Slack URLs, so the
    # webhook impls own httpx directly. Same exception in pyproject
    # per-file-ignores.
    ("notifications/teams.py", "httpx"): ("Phase 16 Teams webhook -- credential-less URL"),
    ("notifications/slack.py", "httpx"): ("Phase 16 Slack webhook -- credential-less URL"),
    # ---- Deploy + sync --------------------------------------------------
    # Thin fabric-cicd wrapper. The CLAUDE.md "wrap upstream, don't DIY"
    # rule says fabric-cicd is the deploy backend; these modules own
    # that integration.
    ("deploy/core.py", "fabric_cicd"): "fabric-cicd wrapper for deploy --plan/--apply",
    ("deploy/rollback.py", "fabric_cicd"): (
        "rollback uses fabric-cicd's append_feature_flag for the rollback-bypass-publish flag"
    ),
    ("deploy/sync_publish.py", "fabric_cicd"): (
        "Phase 17 SYNC-PUBLISH thin wrapper around fabric-cicd's publisher"
    ),
    ("sync/manifest.py", "fabric_cicd"): (
        "ItemType enum sourced from fabric_cicd.constants for byte-identical manifests"
    ),
}


def _module_scope_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, top_level_module)`` pairs for module-scope imports."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in tree.body:  # only module-scope, not nested in functions
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                # Relative imports never reach a vendor package.
                continue
            if node.module:
                out.append((node.lineno, node.module))
    return out


def _vendor_root(dotted: str) -> str | None:
    """Return the top-level vendor root for ``dotted``, or ``None``."""
    head = dotted.split(".", 1)[0]
    return head if head in _VENDOR_ROOTS else None


def _scan_vendor_imports() -> list[tuple[str, int, str]]:
    """Return ``(rel_path, lineno, vendor_root)`` for every module-scope vendor import."""
    findings: list[tuple[str, int, str]] = []
    for path in sorted(SIGANTRY_CORE.rglob("*.py")):
        rel = path.relative_to(SIGANTRY_CORE).as_posix()
        for lineno, dotted in _module_scope_imports(path):
            root = _vendor_root(dotted)
            if root is not None:
                findings.append((rel, lineno, root))
    return findings


def test_no_unallowed_vendor_imports() -> None:
    """Module-scope vendor imports must each have an explicit allowlist entry.

    Path B from the audit synthesis: base ships reference impls, but
    only at paths that own the dependency surface. Random business-logic
    modules must route through ``sigantry_core.client`` / ``.auth`` / etc.
    """
    offenders: list[str] = []
    for rel, lineno, vendor in _scan_vendor_imports():
        if (rel, vendor) not in _VENDOR_IMPORT_ALLOWLIST:
            offenders.append(
                f"sigantry_core/{rel}:{lineno}  module-scope `{vendor}.*` "
                "import is not allowlisted in "
                "tests/prereqs/test_no_vendor_imports.py::"
                "_VENDOR_IMPORT_ALLOWLIST"
            )
    assert offenders == [], (
        "Vendor-import gate (Audit-2026-05-07 W2.3) tripped. The base "
        "package may ship reference impls, but only at paths that own "
        "the dependency surface. Either:\n"
        "  1. Route the new code through sigantry_core.client / .auth / "
        "etc. (preferred -- shared retry + token-refresh policy), or\n"
        "  2. If this module legitimately owns the dependency, add it "
        "to _VENDOR_IMPORT_ALLOWLIST with a one-line rationale.\n"
        "  - " + "\n  - ".join(offenders)
    )


def test_vendor_allowlist_has_no_dead_entries() -> None:
    """Every allowlist entry must correspond to an actual import.

    Removing a vendor import from a module without removing the matching
    allowlist entry leaves dead noise in the gate. This test fails the
    moment that happens, so cleanup commits stay honest.
    """
    actual: set[tuple[str, str]] = {(rel, vendor) for rel, _, vendor in _scan_vendor_imports()}
    dead = set(_VENDOR_IMPORT_ALLOWLIST.keys()) - actual
    assert dead == set(), (
        "Stale entries in _VENDOR_IMPORT_ALLOWLIST -- the underlying "
        "imports no longer exist. Remove these allowlist entries:\n"
        "  - " + "\n  - ".join(f"({path!r}, {vendor!r})" for path, vendor in sorted(dead))
    )


def test_module_scope_extractor_detects_known_imports(tmp_path: Path) -> None:
    """Falsifiability of the AST extractor: known shapes are detected.

    Pins the contract behind ``_module_scope_imports`` so a future
    refactor that breaks the AST walk surfaces here, not via false
    negatives in the offender scan.
    """
    src = (
        "import httpx\n"
        "import azure.identity\n"
        "from fabric_cicd import core\n"
        "from fabric_cicd.constants import ItemType\n"
        "from . import sibling  # relative -- must be ignored\n"
        "from typing import Any  # stdlib -- not a vendor root\n"
        "\n"
        "def f():\n"
        "    import nacl  # nested -- must be ignored\n"
    )
    p = tmp_path / "probe.py"
    p.write_text(src, encoding="utf-8")
    found = _module_scope_imports(p)
    names = {dotted for _, dotted in found}
    assert "httpx" in names
    assert "azure.identity" in names
    assert "fabric_cicd" in names
    assert "fabric_cicd.constants" in names
    assert "typing" in names  # captured but not a vendor root
    # Relative + function-scoped imports must NOT appear.
    assert all(d != "sibling" for d in names), names
    assert all(d != "nacl" for d in names), names

    # And the vendor-root mapper rejects stdlib while accepting vendors.
    assert _vendor_root("httpx") == "httpx"
    assert _vendor_root("azure.identity") == "azure"
    assert _vendor_root("fabric_cicd.constants") == "fabric_cicd"
    assert _vendor_root("typing") is None
    assert _vendor_root("os.path") is None


def test_vendor_roots_match_pyproject_banned_api() -> None:
    """The vendor-root set must include every package the ruff TID251 rule bans.

    Keeps the AST gate (``test_no_unallowed_vendor_imports``) and the
    editor-time ruff rule in ``pyproject.toml`` aligned -- a name that
    ruff bans should also fail this stronger AST-based gate, not be
    silently absent from it.
    """
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ruff_banned = (
        pyproject.get("tool", {})
        .get("ruff", {})
        .get("lint", {})
        .get("flake8-tidy-imports", {})
        .get("banned-api", {})
    )
    missing = set(ruff_banned.keys()) - _VENDOR_ROOTS
    assert missing == set(), (
        "ruff banned-api lists packages that the W2.3 AST gate does NOT "
        f"scan: {sorted(missing)}. Add them to _VENDOR_ROOTS so the "
        "stronger AST gate also flags them, or drop the ruff rule if "
        "it's redundant."
    )


# Vendor roots that the AST gate covers but the editor-time ruff rule
# in ``pyproject.toml`` deliberately does NOT (yet) ban. Each one needs
# a justification: ruff per-file-ignores would have to grow in lockstep
# (see Wave 2 packaging re-audit F-3, deferred). Removing an entry from
# this set means the corresponding ruff banned-api rule is now in place
# so the editor surfaces violations at edit time.
_VENDOR_ROOTS_NOT_YET_RUFF_BANNED: frozenset[str] = frozenset(
    {
        "azure",
        "fabric_cicd",
        "msfabricpysdkcore",
        "semantic_link_labs",
        "nacl",
    }
)


def test_ast_gate_is_a_proper_superset_of_ruff_banned_api() -> None:
    """Every ``_VENDOR_ROOTS`` member is either ruff-banned OR explicitly deferred.

    Wave 2 packaging re-audit F-3 (deferred to Wave 3) flagged the
    asymmetry: the AST gate covers 6 vendor roots while ruff bans
    only ``httpx``. This test pins the deliberate gap -- if we *want*
    asymmetry, we declare the deferred names; if a vendor is added to
    ruff later, this test fails until it is also removed from
    ``_VENDOR_ROOTS_NOT_YET_RUFF_BANNED``.
    """
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ruff_banned = set(
        pyproject.get("tool", {})
        .get("ruff", {})
        .get("lint", {})
        .get("flake8-tidy-imports", {})
        .get("banned-api", {})
        .keys()
    )
    expected_deferred = _VENDOR_ROOTS - ruff_banned
    drift = expected_deferred ^ _VENDOR_ROOTS_NOT_YET_RUFF_BANNED
    assert drift == set(), (
        "Drift between actual ruff coverage and the documented deferred-set:\n"
        f"  AST scans, NOT ruff-banned: {sorted(expected_deferred)}\n"
        f"  Documented as deferred:     {sorted(_VENDOR_ROOTS_NOT_YET_RUFF_BANNED)}\n"
        f"  Diff:                        {sorted(drift)}\n"
        "Either the ruff rule needs widening, or this test's "
        "_VENDOR_ROOTS_NOT_YET_RUFF_BANNED set needs updating."
    )
