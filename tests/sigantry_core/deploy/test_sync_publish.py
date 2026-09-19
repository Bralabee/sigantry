"""Unit tests for sigantry_core.deploy.sync_publish (Phase 17 / SYNC-PUBLISH).

Wave 0 RED scaffolding for the engine helper that closes ADR-0012 Option C.
The helper composes folder-reconcile + first-time fabric-cicd publish into a
single ``apply_sync`` invocation by handing the staging tree to
``fabric_cicd.publish_all_items`` with an ``items_to_include`` filter.

Tests cover the locked behavioural contract from 17-CONTEXT.md decisions
D-17-04 / D-17-05 / D-17-06 / D-17-07 / D-17-09:

1. Both feature flags MUST be appended (D-17-09).
2. items_to_include filter is built from absent_items (D-17-06).
3. Succeeded outcome on clean publish.
4. Partial-failure outcome on mid-publish exception (best-effort failed_item
   extraction per D-17-05 / runbook §1.3).
5. Failed outcome on full failure (no items published).
6. Wrapped exception messages do NOT leak parameters.yml content (T-17-05).
7. FabricWorkspace receives the substituted parameters_path (PR #54 path).
8. FabricWorkspace receives the staging_dir as repository_directory.
9. Missing staging_dir raises a clearly-attributed SyncPublishError (D-17-07).
10. Empty absent_items short-circuits without calling publish_all_items.

Mock pattern mirrors tests/sigantry_core/deploy/test_core.py:23-29 — patch
``sigantry_core.deploy.sync_publish.FabricWorkspace`` and
``sigantry_core.deploy.sync_publish.publish_all_items``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_feature_flags() -> None:
    """Snapshot+restore ``fabric_cicd.constants.FEATURE_FLAG`` between tests.

    Pattern adapted from tests/deploy/test_rollback.py:54-60. Note:
    ``FEATURE_FLAG`` is a ``set`` in fabric-cicd 1.0.x (not a list as the
    earlier rollback test pattern assumes), so we copy via ``set(...)`` and
    restore via ``clear()`` + ``update(...)``.
    """
    from fabric_cicd.constants import FEATURE_FLAG

    snapshot = set(FEATURE_FLAG)
    yield
    FEATURE_FLAG.clear()
    FEATURE_FLAG.update(snapshot)


@pytest.fixture
def mock_token_provider() -> MagicMock:
    """Minimal TokenProvider double — only ``get_credential()`` is called."""
    tp = MagicMock(name="TokenProvider")
    tp.get_credential.return_value = MagicMock(name="MockCredential")
    return tp


def _make_sync_item(*, display_name: str, type_: str, target_folder: str = "/"):
    """Build a SyncItem fixture with a real source file under tmp_path.

    The packager / staging tree are NOT exercised here — these unit tests
    only verify the publish helper's argument handling. ``local_path`` is
    a stub Path; ``publish_absent_items`` does not read it.
    """
    from sigantry_core.sync.manifest import SyncItem

    return SyncItem(
        local_path=Path("/tmp/stub.ipynb"),
        type=type_,
        target_folder=target_folder,
        display_name=display_name,
    )


def _patch_fabric_cicd(monkeypatch, *, publish_return=None, publish_side_effect=None):
    """Patch FabricWorkspace + publish_all_items on the sync_publish module.

    Mirrors the canonical pattern at tests/sigantry_core/deploy/test_core.py:23-29
    but re-pointed at the new module path per D-17-06.
    """
    fake_instance = MagicMock(name="FabricWorkspace_instance")
    # repository_items provides the fallback denominator used by
    # _count_succeeded when the publish return shape is None / dict-without-summary.
    fake_instance.repository_items = {}
    fake_ws_class = MagicMock(name="FabricWorkspace", return_value=fake_instance)

    fake_publish = MagicMock(name="publish_all_items")
    if publish_side_effect is not None:
        fake_publish.side_effect = publish_side_effect
    else:
        fake_publish.return_value = publish_return

    monkeypatch.setattr("sigantry_core.deploy.sync_publish.FabricWorkspace", fake_ws_class)
    monkeypatch.setattr("sigantry_core.deploy.sync_publish.publish_all_items", fake_publish)
    return fake_ws_class, fake_instance, fake_publish


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_publish_absent_items_appends_both_feature_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """D-17-09: both ``enable_experimental_features`` AND
    ``enable_items_to_include`` must be appended to FEATURE_FLAG before
    ``publish_all_items`` is invoked. Without these two flags the
    ``items_to_include`` kwarg is silently ignored by fabric-cicd 1.0.x
    and the WHOLE staging tree is published instead of the requested subset.
    """
    from fabric_cicd.constants import FEATURE_FLAG

    from sigantry_core.deploy.sync_publish import publish_absent_items

    _patch_fabric_cicd(monkeypatch, publish_return={"summary": {"Succeeded": 1, "Failed": 0}})

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    publish_absent_items(
        workspace_id="ws-1",
        environment="DEV",
        staging_dir=staging,
        absent_items=[_make_sync_item(display_name="A", type_="Notebook")],
        item_type_in_scope=["Notebook"],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    assert "enable_experimental_features" in FEATURE_FLAG
    assert "enable_items_to_include" in FEATURE_FLAG


def test_publish_absent_items_calls_publish_all_items_with_items_to_include(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """The ``items_to_include`` kwarg must be ``["<name>.<type>", ...]`` for
    every absent item. The format is locked by fabric-cicd's experimental
    selective-deploy filter (probed against publish.py:38-148).
    """
    from sigantry_core.deploy.sync_publish import publish_absent_items

    _, _, fake_publish = _patch_fabric_cicd(
        monkeypatch, publish_return={"summary": {"Succeeded": 2, "Failed": 0}}
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    absent = [
        _make_sync_item(display_name="A", type_="Notebook"),
        _make_sync_item(display_name="B", type_="DataPipeline"),
    ]
    publish_absent_items(
        workspace_id="ws-2",
        environment="DEV",
        staging_dir=staging,
        absent_items=absent,
        item_type_in_scope=["Notebook", "DataPipeline"],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    fake_publish.assert_called_once()
    kwargs = fake_publish.call_args.kwargs
    assert kwargs["items_to_include"] == ["A.Notebook", "B.DataPipeline"]


def test_publish_absent_items_returns_succeeded_outcome_on_clean_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """Clean return from publish_all_items -> outcome='succeeded'.

    ``published_items`` carries the full ``<name>.<type>`` list; ``failed_item``
    is None.
    """
    from sigantry_core.deploy.sync_publish import publish_absent_items

    _patch_fabric_cicd(monkeypatch, publish_return={"summary": {"Succeeded": 2, "Failed": 0}})

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    result = publish_absent_items(
        workspace_id="ws-3",
        environment="DEV",
        staging_dir=staging,
        absent_items=[
            _make_sync_item(display_name="A", type_="Notebook"),
            _make_sync_item(display_name="B", type_="DataPipeline"),
        ],
        item_type_in_scope=["Notebook", "DataPipeline"],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    assert result.outcome == "succeeded"
    assert result.published_items == ["A.Notebook", "B.DataPipeline"]
    assert result.failed_item is None


def test_publish_absent_items_returns_partial_failure_on_mixed_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """Mid-publish exception with one item succeeded -> outcome='partial-failure'.

    ``failed_item`` extraction is best-effort (Pitfall 3 / D-17-05): the
    helper scans the exception text for any of the candidate
    ``<name>.<type>`` strings. Per the plan's tolerance note, the test
    accepts either ``"B.DataPipeline"`` (extracted) or ``None`` (extraction
    failed gracefully).
    """
    from sigantry_core.deploy.sync_publish import publish_absent_items

    _patch_fabric_cicd(
        monkeypatch,
        publish_side_effect=RuntimeError("item B.DataPipeline: deploy failed"),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    result = publish_absent_items(
        workspace_id="ws-4",
        environment="DEV",
        staging_dir=staging,
        absent_items=[
            _make_sync_item(display_name="A", type_="Notebook"),
            _make_sync_item(display_name="B", type_="DataPipeline"),
        ],
        item_type_in_scope=["Notebook", "DataPipeline"],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    # Either partial-failure (one succeeded somehow recorded) OR failed (no
    # explicit succeeded count derivable from the exception path). The
    # contract locks: outcome is NOT "succeeded" on exception path.
    assert result.outcome in {"partial-failure", "failed"}
    # failed_item is best-effort: either extracted or None.
    assert result.failed_item in {"B.DataPipeline", None}


def test_publish_absent_items_returns_failed_on_full_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """Exception before any item published -> outcome='failed' + published_items=[]."""
    from sigantry_core.deploy.sync_publish import publish_absent_items

    _patch_fabric_cicd(
        monkeypatch,
        publish_side_effect=RuntimeError("upstream auth failure"),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    result = publish_absent_items(
        workspace_id="ws-5",
        environment="DEV",
        staging_dir=staging,
        absent_items=[
            _make_sync_item(display_name="A", type_="Notebook"),
            _make_sync_item(display_name="B", type_="DataPipeline"),
        ],
        item_type_in_scope=["Notebook", "DataPipeline"],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    assert result.outcome == "failed"
    assert result.published_items == []


def test_publish_absent_items_emits_warning_log_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Audit-2026-05-07 W1.5: failed publish emits a structured WARNING.

    Falsifiability: this test FAILS against the pre-fix implementation
    that constructed SyncPublishError and discarded it (``_ = ...``).
    Operators reading the audit ledger had no log record explaining why
    publish failed; with the fix in place the log line carries
    ``outcome=...`` and ``failed_item=...`` and ``exc_type=...`` for
    triage.
    """
    import logging as _logging

    from sigantry_core.deploy.sync_publish import publish_absent_items

    _patch_fabric_cicd(
        monkeypatch,
        publish_side_effect=RuntimeError("upstream auth failure"),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    with caplog.at_level(_logging.WARNING, logger="sigantry_core.deploy.sync_publish"):
        result = publish_absent_items(
            workspace_id="ws-warn-1",
            environment="DEV",
            staging_dir=staging,
            absent_items=[_make_sync_item(display_name="A", type_="Notebook")],
            item_type_in_scope=["Notebook"],
            parameters_path=params,
            token_provider=mock_token_provider,
        )

    assert result.outcome == "failed"
    # Exactly one warning record from the sync_publish logger:
    sync_publish_warnings = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.deploy.sync_publish" and r.levelno == _logging.WARNING
    ]
    assert len(sync_publish_warnings) == 1, (
        f"expected exactly one sync_publish_failed warning; got {len(sync_publish_warnings)}: "
        f"{[r.getMessage() for r in sync_publish_warnings]}"
    )
    record = sync_publish_warnings[0]
    msg = record.getMessage()
    # Bounded fields the operator needs for triage:
    assert "sync_publish_failed" in msg
    assert "outcome=failed" in msg
    assert "exc_type=RuntimeError" in msg
    # T-17-05: bounded message must not echo str(exc) verbatim. The
    # warning's ``message=...`` field is the stable bounded string; the
    # raw RuntimeError("upstream auth failure") payload must not appear.
    assert "upstream auth failure" not in msg, (
        "BOUNDARY VIOLATION: warning leaked raw exception text"
    )


def test_publish_absent_items_wraps_exception_does_not_leak_parameter_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """T-17-05 mitigation: the wrapped SyncPublishError message MUST NOT
    contain raw exception text from fabric-cicd, since that text may
    embed portions of parameters.yml (which can include $ENV-substituted
    secrets).

    The original exception is chained via ``raise ... from exc`` so the
    traceback lives in stderr; the wrapped ``str(SyncPublishError)`` is
    bounded.
    """
    from sigantry_core.deploy.sync_publish import (
        SyncPublishError,
        publish_absent_items,
    )

    # NOTE: this string is a deliberate non-secret placeholder used to verify
    # that secret-shaped values in parameters.yml do NOT leak into wrapped
    # exception messages or DeployRecord fields. It is uniformly cased and
    # carries no high-entropy material so GitGuardian / similar scanners do
    # not flag it as a real credential. Renamed 2026-05-01 from a previous
    # leetspeak value that tripped a Generic-High-Entropy false positive.
    fake_secret_marker = "PLACEHOLDER_TEST_FIXTURE_NOT_A_SECRET"
    _patch_fabric_cicd(
        monkeypatch,
        publish_side_effect=RuntimeError(
            f"upstream barfed with parameters.yml content: {fake_secret_marker}"
        ),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text(f"# placeholder={fake_secret_marker}\n", encoding="utf-8")

    # Implementation may either RAISE SyncPublishError on full-failure or
    # return PublishResult(outcome="failed"). Both are valid per the
    # 9-step contract. The leakage check applies to whichever surface
    # carries the user-facing message.
    try:
        result = publish_absent_items(
            workspace_id="ws-6",
            environment="DEV",
            staging_dir=staging,
            absent_items=[_make_sync_item(display_name="A", type_="Notebook")],
            item_type_in_scope=["Notebook"],
            parameters_path=params,
            token_provider=mock_token_provider,
        )
        # If no raise, the result/outcome path must not embed the secret
        # in any user-readable field.
        assert fake_secret_marker not in (result.failed_item or "")
        for item in result.published_items:
            assert fake_secret_marker not in item
    except SyncPublishError as exc:
        # Wrapped message must NOT contain the secret string from the
        # original exception's args.
        assert fake_secret_marker not in str(exc), (
            f"SyncPublishError leaked parameters.yml content: {exc!s}"
        )


def test_publish_absent_items_uses_substituted_parameters_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """FabricWorkspace.__init__ must receive parameter_file_path = str(parameters_path).

    The ``parameters_path`` argument is the SUBSTITUTED tempfile path written
    by the apply_sync caller (PR #54); the helper does not re-substitute.
    """
    from sigantry_core.deploy.sync_publish import publish_absent_items

    fake_ws_class, _, _ = _patch_fabric_cicd(
        monkeypatch, publish_return={"summary": {"Succeeded": 1, "Failed": 0}}
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    substituted = tmp_path / "substituted-params" / "parameters.yml"
    substituted.parent.mkdir()
    substituted.write_text("# substituted\n", encoding="utf-8")

    publish_absent_items(
        workspace_id="ws-7",
        environment="DEV",
        staging_dir=staging,
        absent_items=[_make_sync_item(display_name="A", type_="Notebook")],
        item_type_in_scope=["Notebook"],
        parameters_path=substituted,
        token_provider=mock_token_provider,
    )

    fake_ws_class.assert_called_once()
    kwargs = fake_ws_class.call_args.kwargs
    assert kwargs["parameter_file_path"] == str(substituted)


def test_publish_absent_items_passes_repository_directory_as_staging_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """FabricWorkspace.__init__ must receive repository_directory = str(staging_dir).

    fabric-cicd reads the directory lazily during publish; the staging tree
    apply_sync built MUST be the one fabric-cicd walks (D-17-07).
    """
    from sigantry_core.deploy.sync_publish import publish_absent_items

    fake_ws_class, _, _ = _patch_fabric_cicd(
        monkeypatch, publish_return={"summary": {"Succeeded": 1, "Failed": 0}}
    )

    staging = tmp_path / "my-staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    publish_absent_items(
        workspace_id="ws-8",
        environment="DEV",
        staging_dir=staging,
        absent_items=[_make_sync_item(display_name="A", type_="Notebook")],
        item_type_in_scope=["Notebook"],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    fake_ws_class.assert_called_once()
    kwargs = fake_ws_class.call_args.kwargs
    assert kwargs["repository_directory"] == str(staging)


def test_publish_absent_items_raises_clearly_when_staging_dir_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """D-17-07 / Pitfall 2: missing staging_dir surfaces a SyncPublishError
    BEFORE fabric-cicd is invoked, NOT a confusing FileNotFoundError from
    deep inside fabric-cicd's walker.
    """
    from sigantry_core.deploy.sync_publish import (
        SyncPublishError,
        publish_absent_items,
    )

    _patch_fabric_cicd(monkeypatch, publish_return={"summary": {"Succeeded": 0, "Failed": 0}})

    missing = tmp_path / "does-not-exist"
    # Do NOT create ``missing``.
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    with pytest.raises(SyncPublishError) as exc_info:
        publish_absent_items(
            workspace_id="ws-9",
            environment="DEV",
            staging_dir=missing,
            absent_items=[_make_sync_item(display_name="A", type_="Notebook")],
            item_type_in_scope=["Notebook"],
            parameters_path=params,
            token_provider=mock_token_provider,
        )

    assert "Staging directory" in str(exc_info.value) or "staging" in str(exc_info.value).lower()


def test_publish_absent_items_empty_absent_list_short_circuits_no_publish_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """Empty absent_items -> short-circuit return; publish_all_items NEVER called.

    This is the load-bearing optimisation that prevents fabric-cicd from
    running against an empty filter (which it would interpret as "publish
    nothing" but still walk the staging tree, wasting time).
    """
    from sigantry_core.deploy.sync_publish import (
        PublishResult,
        publish_absent_items,
    )

    _, _, fake_publish = _patch_fabric_cicd(
        monkeypatch, publish_return={"summary": {"Succeeded": 0, "Failed": 0}}
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    result = publish_absent_items(
        workspace_id="ws-10",
        environment="DEV",
        staging_dir=staging,
        absent_items=[],
        item_type_in_scope=[],
        parameters_path=params,
        token_provider=mock_token_provider,
    )

    fake_publish.assert_not_called()
    assert isinstance(result, PublishResult)
    assert result.outcome == "succeeded"
    assert result.published_items == []
    assert result.failed_item is None


def test_publish_absent_items_bulk_parallel_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """bulk=True uses ThreadPoolExecutor to publish multiple absent items concurrently."""
    from sigantry_core.deploy.sync_publish import (
        PublishResult,
        publish_absent_items,
    )

    _, _, fake_publish = _patch_fabric_cicd(
        monkeypatch, publish_return={"summary": {"Succeeded": 1, "Failed": 0}}
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    absent = [
        _make_sync_item(display_name="itemA", type_="Notebook"),
        _make_sync_item(display_name="itemB", type_="Lakehouse"),
        _make_sync_item(display_name="itemC", type_="DataPipeline"),
    ]

    result = publish_absent_items(
        workspace_id="ws-bulk-1",
        environment="DEV",
        staging_dir=staging,
        absent_items=absent,
        item_type_in_scope=["Notebook", "Lakehouse", "DataPipeline"],
        parameters_path=params,
        token_provider=mock_token_provider,
        bulk=True,
        max_workers=3,
    )

    assert isinstance(result, PublishResult)
    assert result.outcome == "succeeded"
    assert len(result.published_items) == 3
    assert set(result.published_items) == {
        "itemA.Notebook",
        "itemB.Lakehouse",
        "itemC.DataPipeline",
    }
    assert result.failed_item is None
    # Concurrency invoked publish_all_items once per item
    assert fake_publish.call_count == 3


def test_publish_absent_items_bulk_parallel_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reset_feature_flags,
    mock_token_provider: MagicMock,
) -> None:
    """bulk=True captures failed item and returns partial-failure when one worker fails."""
    from sigantry_core.deploy.sync_publish import (
        PublishResult,
        publish_absent_items,
    )

    def _side_effect(ws, items_to_include=None, **kwargs):
        if items_to_include and any("itemB" in it for it in items_to_include):
            raise RuntimeError("API 500 error publishing itemB")
        return {"summary": {"Succeeded": 1, "Failed": 0}}

    _, _, fake_publish = _patch_fabric_cicd(
        monkeypatch, publish_side_effect=_side_effect
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    params = tmp_path / "parameters.yml"
    params.write_text("# substituted\n", encoding="utf-8")

    absent = [
        _make_sync_item(display_name="itemA", type_="Notebook"),
        _make_sync_item(display_name="itemB", type_="Notebook"),
    ]

    result = publish_absent_items(
        workspace_id="ws-bulk-2",
        environment="DEV",
        staging_dir=staging,
        absent_items=absent,
        item_type_in_scope=["Notebook"],
        parameters_path=params,
        token_provider=mock_token_provider,
        bulk=True,
    )

    assert isinstance(result, PublishResult)
    assert result.outcome == "partial-failure"
    assert "itemA.Notebook" in result.published_items
    assert result.failed_item == "itemB.Notebook"

