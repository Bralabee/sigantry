"""Contract canaries against the INSTALLED fabric-cicd (1.1.0 delta audit, 2026-06-11;
re-audited green against 1.3.0, 2026-08-24 — all three findings unchanged).

These tests pin the upstream behaviours that Sigantry's deploy/sync layers
were designed around. They run against the *installed* ``fabric_cicd``
distribution (no mocks) so a future dependency bump that changes any of
these contracts fails loudly here instead of surfacing as a silent
behaviour shift in production.

Audit findings encoded below (fabric-cicd 1.0.0 -> 1.1.0):

1. ``$ENV:`` substitution remains the TOOLKIT'S responsibility.
   Upstream 1.1.0 ships ``replace_variables_in_parameter_file`` gated
   behind the ``enable_environment_variable_replacement`` feature flag,
   and its implementation only reads environment variables whose NAMES
   literally start with ``$ENV:`` (e.g. ``os.environ["$ENV:FOO"]``).
   That does not satisfy Sigantry's documented operator contract
   (plain ``FOO=bar`` env vars referenced as ``$ENV:FOO`` in
   parameters.yml), so ``sigantry_core.deploy.parameters`` keeps doing
   the substitution itself (PR #54).

2. ``items_to_include`` is still double-flag-gated (D-17-09):
   ``enable_experimental_features`` + ``enable_items_to_include``.
   Without both, the filter is silently ignored and the WHOLE staging
   tree publishes.

3. Hard delete is opt-in upstream via ``enable_hard_delete`` (added in
   1.0.0). ``sigantry_core`` deliberately never sets it, so orphan
   unpublish remains a soft delete (recycle-bin recoverable) --
   consistent with the read-only-by-default posture. A source-tree grep
   pins that no ``sigantry_core`` module opts in (plugin packages are
   outside this fence; a plugin opting in must add its own gate).
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import fabric_cicd
from fabric_cicd import constants
from fabric_cicd._parameter._utils import replace_variables_in_parameter_file

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FLAG = "enable_environment_variable_replacement"


def _without_flag(flag: str):
    """Context helper: ensure ``flag`` absent from the global FEATURE_FLAG set."""
    return _FlagGuard(flag, present=False)


def _with_flag(flag: str):
    """Context helper: ensure ``flag`` present in the global FEATURE_FLAG set."""
    return _FlagGuard(flag, present=True)


class _FlagGuard:
    """Snapshot/restore guard for fabric-cicd's mutable global flag set.

    ``fabric_cicd.constants.FEATURE_FLAG`` is module-level mutable state;
    tests that poke it MUST restore the prior contents or they poison
    sibling tests (the same reason ``publish_absent_items`` appending
    flags is itself pinned by an existing test).
    """

    def __init__(self, flag: str, *, present: bool) -> None:
        self._flag = flag
        self._present = present
        self._snapshot: set[str] | None = None

    def __enter__(self) -> None:
        self._snapshot = set(constants.FEATURE_FLAG)
        if self._present:
            constants.FEATURE_FLAG.add(self._flag)
        else:
            constants.FEATURE_FLAG.discard(self._flag)

    def __exit__(self, *exc_info: object) -> None:
        assert self._snapshot is not None
        constants.FEATURE_FLAG.clear()
        constants.FEATURE_FLAG.update(self._snapshot)


def test_upstream_leaves_env_tokens_untouched_without_feature_flag(monkeypatch) -> None:
    """Finding 1a: without the flag, upstream passes ``$ENV:`` tokens through verbatim.

    This is the load-bearing fact behind PR #54's toolkit-side
    substitution: if this test ever fails (upstream starts replacing
    tokens by default), revisit whether
    ``sigantry_core.deploy.parameters.substitute_env_references`` can be
    simplified or retired.
    """
    monkeypatch.setenv("SIGANTRY_CONTRACT_PROBE", "real-value")
    raw = "find_replace:\n  replace_value:\n    DEV: $ENV:SIGANTRY_CONTRACT_PROBE\n"
    with _without_flag(_ENV_FLAG):
        result = replace_variables_in_parameter_file(raw)
    assert "$ENV:SIGANTRY_CONTRACT_PROBE" in result
    assert "real-value" not in result


def test_upstream_native_env_replacement_ignores_plain_env_var_names(monkeypatch) -> None:
    """Finding 1b: even WITH the flag, upstream ignores normally-named env vars.

    Upstream filters ``os.environ`` for keys that literally start with
    ``$ENV:`` -- a plain ``FOO=bar`` export is invisible to it. This is
    why Sigantry does not delegate substitution upstream even on 1.1.0:
    the documented operator contract is plain env-var names.
    """
    monkeypatch.setenv("SIGANTRY_CONTRACT_PROBE", "real-value")
    # Hermetic guard: a host env var literally named "$ENV:..." (leaked
    # from a CI step or another test) would invalidate the assertion below.
    monkeypatch.delitem(os.environ, "$ENV:SIGANTRY_CONTRACT_PROBE", raising=False)
    raw = "DEV: $ENV:SIGANTRY_CONTRACT_PROBE\n"
    with _with_flag(_ENV_FLAG):
        result = replace_variables_in_parameter_file(raw)
    # Plain-named env var is NOT picked up by the upstream implementation.
    assert "$ENV:SIGANTRY_CONTRACT_PROBE" in result


def test_upstream_native_env_replacement_requires_dollar_prefixed_names(monkeypatch) -> None:
    """Finding 1c: upstream's flag-gated path only sees ``$ENV:``-NAMED env vars.

    Documents the exact upstream mechanism so a future reader
    understands why it is unusable for Sigantry's contract: operators
    would have to export environment variables literally named
    ``$ENV:FOO``, which most CI variable stores cannot even express.
    """
    monkeypatch.setitem(os.environ, "$ENV:SIGANTRY_CONTRACT_PROBE", "dollar-named-value")
    raw = "DEV: $ENV:SIGANTRY_CONTRACT_PROBE\n"
    with _with_flag(_ENV_FLAG):
        result = replace_variables_in_parameter_file(raw)
    assert "dollar-named-value" in result
    assert "$ENV:SIGANTRY_CONTRACT_PROBE" not in result


def test_items_to_include_double_flag_gate_constants_still_exist() -> None:
    """Finding 2 / D-17-09 canary: both gating flags exist with the exact names we append.

    ``publish_absent_items`` appends these two strings before calling
    ``publish_all_items``. If upstream renames or graduates the flags,
    this canary fails BEFORE the silent-ignore failure mode (whole
    staging tree published) can reach a live workspace.
    """
    flag_values = {f.value for f in constants.FeatureFlag}
    assert "enable_experimental_features" in flag_values
    assert "enable_items_to_include" in flag_values


def test_publish_and_unpublish_signatures_accept_kwargs_sigantry_passes() -> None:
    """Signature canary for every fabric-cicd kwarg Sigantry forwards.

    ``deploy_workspace`` and ``publish_absent_items`` pass these by
    keyword; a removed/renamed parameter would otherwise surface as a
    TypeError at deploy time.
    """
    publish_params = set(inspect.signature(fabric_cicd.publish_all_items).parameters)
    assert {
        "item_name_exclude_regex",
        "folder_path_exclude_regex",
        "folder_path_to_include",
        "items_to_include",
        "shortcut_exclude_regex",
    } <= publish_params

    unpublish_params = set(inspect.signature(fabric_cicd.unpublish_all_orphan_items).parameters)
    assert {"item_name_exclude_regex", "items_to_include"} <= unpublish_params


def test_hard_delete_flag_exists_upstream_and_sigantry_never_opts_in() -> None:
    """Finding 3: soft-delete posture pinned from both sides.

    Upstream side: ``enable_hard_delete`` exists as an opt-in flag
    (deletes bypass the recycle bin only when set). Sigantry side: no
    module under ``sigantry_core/`` references the flag, so every orphan
    unpublish remains recycle-bin recoverable. If a future phase decides
    to opt in, it must do so through a @destructive_op-gated, audited
    surface -- and update this test deliberately.
    """
    flag_values = {f.value for f in constants.FeatureFlag}
    assert "enable_hard_delete" in flag_values

    # Vacuous-pass guard: rglob over a nonexistent directory silently
    # yields nothing, so a stale repo-root computation would leave this
    # canary green while scanning zero files. Fail loudly instead.
    package_root = _REPO_ROOT / "sigantry_core"
    assert (package_root / "__init__.py").is_file(), (
        f"repo-root resolution stale: {package_root} is not the sigantry_core "
        "package; fix the _REPO_ROOT parents[...] depth in this test module."
    )

    offenders: list[str] = []
    scanned = 0
    for path in package_root.rglob("*.py"):
        scanned += 1
        if "enable_hard_delete" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert scanned > 0, "hard-delete canary scanned zero files; the scan is broken."
    assert offenders == [], (
        f"sigantry_core must not opt into fabric-cicd hard delete: {offenders}. "
        "Orphan unpublish is contractually a soft (recycle-bin) delete."
    )
