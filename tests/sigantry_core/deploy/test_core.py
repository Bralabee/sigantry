"""Unit tests for sigantry_core.deploy.core (DEPLOY-01, Pitfall 4A/4B)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sigantry_core.deploy.core import DeployResult, deploy_workspace
from sigantry_core.governance.audit import DestructiveOpError


def _patch_upstream(monkeypatch, *, publish_return=None, items_count=2):
    """Replace fabric-cicd entry points + deploy validators with mocks.

    Returns (fake_ws_class, fake_instance, fake_publish, fake_unpublish).
    """
    fake_instance = MagicMock(name="fabric_workspace_instance")
    # repository_items is the canonical fabric-cicd 1.x item dict (probed).
    fake_instance.repository_items = {f"item_{i}": object() for i in range(items_count)}
    fake_ws_class = MagicMock(name="FabricWorkspace", return_value=fake_instance)

    fake_publish = MagicMock(name="publish_all_items", return_value=publish_return)
    fake_unpublish = MagicMock(name="unpublish_all_orphan_items", return_value=None)

    monkeypatch.setattr("sigantry_core.deploy.core.FabricWorkspace", fake_ws_class)
    monkeypatch.setattr("sigantry_core.deploy.core.publish_all_items", fake_publish)
    monkeypatch.setattr("sigantry_core.deploy.core.unpublish_all_orphan_items", fake_unpublish)
    monkeypatch.setattr(
        "sigantry_core.deploy.core.load_and_validate",
        MagicMock(name="load_and_validate", return_value=MagicMock(raw={})),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.core.validate_order",
        MagicMock(name="validate_order", return_value="./deploy-artefacts/dep-graph.dot"),
    )
    return fake_ws_class, fake_instance, fake_publish, fake_unpublish


def test_deploy_result_shape() -> None:
    r = DeployResult(
        workspace_id="w",
        environment="DEV",
        items_published=1,
        items_failed=0,
        orphans_unpublished=0,
        dot_graph_path=None,
    )
    assert r.workspace_id == "w"
    assert r.environment == "DEV"
    assert r.items_published == 1
    assert r.items_failed == 0
    assert r.orphans_unpublished == 0
    assert r.dot_graph_path is None
    # Frozen dataclass must reject attribute mutation.
    with pytest.raises((AttributeError, TypeError)):
        r.workspace_id = "x"  # type: ignore[misc]


def test_deploy_workspace_instantiates_with_token_credential(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """T-4-02 mitigation: fabric_cicd 1.0.0 REQUIRES the token_credential kwarg."""
    fake_ws, fake_inst, fake_pub, _ = _patch_upstream(
        monkeypatch, publish_return=None, items_count=2
    )
    result = deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse", "Notebook"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    fake_ws.assert_called_once()
    kwargs = fake_ws.call_args.kwargs
    assert kwargs["workspace_id"] == "w1"
    assert kwargs["environment"] == "DEV"
    assert kwargs["repository_directory"] == str(tmp_item_tree)
    assert kwargs["item_type_in_scope"] == ["Lakehouse", "Notebook"]
    assert "token_credential" in kwargs  # T-4-02 assertion
    assert kwargs["token_credential"] is mock_token_provider.get_credential.return_value
    fake_pub.assert_called_once()
    assert fake_pub.call_args.args == (fake_inst,)
    mock_token_provider.get_credential.assert_called_once_with()
    assert isinstance(result, DeployResult)
    assert result.items_published == 2
    assert result.items_failed == 0
    assert result.orphans_unpublished == 0
    assert result.dot_graph_path == "./deploy-artefacts/dep-graph.dot"


def test_deploy_workspace_calls_publish_all_items(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _, fake_inst, fake_pub, _ = _patch_upstream(monkeypatch, publish_return=None)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    fake_pub.assert_called_once()
    assert fake_pub.call_args.args == (fake_inst,)


def test_deploy_workspace_raises_on_failed_items(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """When publish_all_items returns a dict with failure counters, raise."""
    failure_dict = {
        "summary": {"Succeeded": 1, "Failed": 2},
    }
    _patch_upstream(monkeypatch, publish_return=failure_dict)
    with pytest.raises(RuntimeError, match="failed"):
        deploy_workspace(
            workspace_id="w1",
            repository_directory=str(tmp_item_tree),
            environment="DEV",
            item_type_in_scope=["Lakehouse", "Notebook"],
            parameters_path=str(tmp_item_tree / "parameters.yml"),
            token_provider=mock_token_provider,
        )


def test_deploy_workspace_does_not_unpublish_by_default(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _, _, _, fake_unpub = _patch_upstream(monkeypatch, publish_return=None)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    fake_unpub.assert_not_called()


def test_deploy_workspace_unpublish_requires_force(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """Pitfall 4B / T-4-02: orphan cleanup gated by @destructive_op(force=True)."""
    _, _, _, fake_unpub = _patch_upstream(monkeypatch, publish_return=None)
    with pytest.raises(DestructiveOpError, match="force=True"):
        deploy_workspace(
            workspace_id="w1",
            repository_directory=str(tmp_item_tree),
            environment="DEV",
            item_type_in_scope=["Lakehouse"],
            parameters_path=str(tmp_item_tree / "parameters.yml"),
            token_provider=mock_token_provider,
            unpublish_orphans=True,
            unpublish_force=False,
        )
    fake_unpub.assert_not_called()


def test_deploy_workspace_unpublish_with_force(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _, fake_inst, _, fake_unpub = _patch_upstream(monkeypatch, publish_return=None)
    result = deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
        unpublish_orphans=True,
        unpublish_force=True,
    )
    # Orphan cleanup forwards the two filters upstream supports (even when
    # the caller didn't set them, we forward the sane defaults).
    fake_unpub.assert_called_once_with(
        fake_inst,
        item_name_exclude_regex="^$",
        items_to_include=None,
    )
    assert isinstance(result, DeployResult)


def test_deploy_workspace_forwards_filters_to_publish(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """Folder / item filters are passed through to fabric-cicd.publish_all_items."""
    _, fake_inst, fake_pub, _ = _patch_upstream(monkeypatch, publish_return=None)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse", "Notebook"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
        item_name_exclude_regex=r"^_wip_",
        folder_path_exclude_regex=r"^archive/",
        folder_path_to_include=["bronze", "silver"],
        items_to_include=["nb_one", "lh_one"],
        shortcut_exclude_regex=r"^temp_",
    )
    fake_pub.assert_called_once_with(
        fake_inst,
        item_name_exclude_regex=r"^_wip_",
        folder_path_exclude_regex=r"^archive/",
        folder_path_to_include=["bronze", "silver"],
        items_to_include=["nb_one", "lh_one"],
        shortcut_exclude_regex=r"^temp_",
    )


def test_deploy_workspace_publish_filters_default_to_none(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """When no filters are specified, every filter kwarg is None — preserves
    pre-change behaviour for fabric-cicd which defaults each to None itself."""
    _, fake_inst, fake_pub, _ = _patch_upstream(monkeypatch, publish_return=None)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    fake_pub.assert_called_once_with(
        fake_inst,
        item_name_exclude_regex=None,
        folder_path_exclude_regex=None,
        folder_path_to_include=None,
        items_to_include=None,
        shortcut_exclude_regex=None,
    )


def test_deploy_workspace_unpublish_forwards_item_filters(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """Orphan-unpublish receives item_name_exclude_regex + items_to_include
    (the two filters fabric-cicd 1.0.0's unpublish_all_orphan_items accepts)."""
    _, fake_inst, _, fake_unpub = _patch_upstream(monkeypatch, publish_return=None)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
        unpublish_orphans=True,
        unpublish_force=True,
        item_name_exclude_regex=r"^_keep_",
        items_to_include=["orphan_one"],
    )
    fake_unpub.assert_called_once_with(
        fake_inst,
        item_name_exclude_regex=r"^_keep_",
        items_to_include=["orphan_one"],
    )


def test_deploy_workspace_calls_parameter_validator(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _patch_upstream(monkeypatch, publish_return=None)
    params_mock = MagicMock(name="load_and_validate", return_value=MagicMock(raw={}))
    monkeypatch.setattr("sigantry_core.deploy.core.load_and_validate", params_mock)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    params_mock.assert_called_once_with(str(tmp_item_tree / "parameters.yml"))


def test_deploy_workspace_calls_dependency_validator(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _patch_upstream(monkeypatch, publish_return=None)
    dep_mock = MagicMock(name="validate_order", return_value="/tmp/g.dot")
    monkeypatch.setattr("sigantry_core.deploy.core.validate_order", dep_mock)
    result = deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse", "Notebook"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    dep_mock.assert_called_once()
    assert dep_mock.call_args.kwargs["repository_directory"] == str(tmp_item_tree)
    assert dep_mock.call_args.kwargs["item_type_in_scope"] == [
        "Lakehouse",
        "Notebook",
    ]
    assert result.dot_graph_path == "/tmp/g.dot"


def test_deploy_workspace_uses_token_provider_from_defaults_when_none(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _patch_upstream(monkeypatch, publish_return=None)
    fake_class = MagicMock(name="TokenProvider")
    fake_class.from_defaults.return_value = mock_token_provider
    monkeypatch.setattr("sigantry_core.deploy.core.TokenProvider", fake_class)
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=None,
    )
    fake_class.from_defaults.assert_called_once()


def test_deploy_workspace_propagates_publish_exception(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    _patch_upstream(monkeypatch, publish_return=None)
    fake_pub = MagicMock(side_effect=RuntimeError("upstream boom"))
    monkeypatch.setattr("sigantry_core.deploy.core.publish_all_items", fake_pub)
    with pytest.raises(RuntimeError, match="upstream boom"):
        deploy_workspace(
            workspace_id="w1",
            repository_directory=str(tmp_item_tree),
            environment="DEV",
            item_type_in_scope=["Lakehouse"],
            parameters_path=str(tmp_item_tree / "parameters.yml"),
            token_provider=mock_token_provider,
        )


def test_deploy_workspace_forwards_parameter_file_path_kwarg(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """Rule 2 deviation: 1.0.0 FabricWorkspace accepts ``parameter_file_path``
    via ``**kwargs``. After the $ENV: substitution refactor, the path passed
    to fabric-cicd is a tempfile copy of the operator's parameters.yml --
    never the source path. The substituted tempfile is cleaned up when
    deploy_workspace returns, so we capture the path + readability via a
    side_effect on the FabricWorkspace mock.
    """
    fake_ws, _, _, _ = _patch_upstream(monkeypatch, publish_return=None)
    captured: dict[str, object] = {}

    def _capture_param_file(*, parameter_file_path: str, **_: object) -> MagicMock:
        captured["path"] = parameter_file_path
        captured["exists_at_call_time"] = Path(parameter_file_path).is_file()
        return fake_ws.return_value

    fake_ws.side_effect = _capture_param_file

    params_path = str(tmp_item_tree / "parameters.yml")
    deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Lakehouse"],
        parameters_path=params_path,
        token_provider=mock_token_provider,
    )
    # The wrapper hands fabric-cicd a tempfile path (not the source path).
    forwarded = captured["path"]
    assert isinstance(forwarded, str)
    assert forwarded != params_path
    # The tempfile must exist at the moment FabricWorkspace is constructed
    # -- fabric-cicd reads parameter_file_path lazily inside publish_all_items.
    assert captured["exists_at_call_time"] is True


def test_no_direct_httpx_import_in_deploy_core() -> None:
    """T-4-10 / CLAUDE.md: deploy never imports httpx directly."""
    source = Path("sigantry_core/deploy/core.py").read_text(encoding="utf-8")
    assert "import httpx" not in source
    assert "from httpx" not in source


def test_fabric_cicd_only_from_deploy_package() -> None:
    """T-4-10: fabric_cicd runtime is quarantined to sigantry_core/deploy/.

    This architecture drift guard uses grep to fail if any non-deploy path
    imports fabric_cicd. Prevents a silent re-instantiation of
    FabricWorkspace elsewhere bypassing the @destructive_op gate.

    Phase 13 carve-out (SYNC-01 / Plan 13-01):
    sigantry_core/sync/manifest.py imports `from fabric_cicd.constants import
    ItemType` — a static type registry, NOT the FabricWorkspace runtime.
    SyncItem.type validator constrains item types to the canonical fabric-cicd
    enum so the sync engine and deploy engine share one source of truth for
    the 26 supported item types. No FabricWorkspace instantiation occurs in
    sigantry_core/sync/, so the @destructive_op gate discipline is preserved.
    """
    allowed_paths = (
        "sigantry_core/deploy/",
        "sigantry_core/sync/manifest.py",  # Phase 13 SYNC-01: static ItemType enum import only
    )
    r = subprocess.run(
        [
            "grep",
            "-rE",
            r"(^|[^#])(import fabric_cicd|from fabric_cicd)",
            "sigantry_core/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (r.stdout or "").splitlines():
        path, _, _ = line.partition(":")
        assert any(path.startswith(allowed) or path == allowed for allowed in allowed_paths), (
            f"fabric_cicd imported outside allowed paths: {line}"
        )


def test_deploy_workspace_counts_nested_repository_items(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """items_published must count ITEMS, not item-TYPE buckets.

    fabric-cicd 1.0.x exposes ``repository_items`` as a nested dict
    ``{item_type: {item_name: Item}}``. publish_all_items returns None (the
    production path -- sigantry never enables response collection), so the
    count falls back to ``_count_items``. A 14-item / 2-type publish must
    report 14, not 2. Pre-fix ``len(repository_items)`` returned 2.
    """
    fake_instance = MagicMock(name="fabric_workspace_instance")
    fake_instance.repository_items = {
        "Notebook": {f"nb_{i}": object() for i in range(13)},
        "DataPipeline": {"orchestrator": object()},
    }
    fake_ws_class = MagicMock(name="FabricWorkspace", return_value=fake_instance)
    monkeypatch.setattr("sigantry_core.deploy.core.FabricWorkspace", fake_ws_class)
    monkeypatch.setattr(
        "sigantry_core.deploy.core.publish_all_items",
        MagicMock(name="publish_all_items", return_value=None),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.core.unpublish_all_orphan_items",
        MagicMock(name="unpublish_all_orphan_items", return_value=None),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.core.load_and_validate",
        MagicMock(name="load_and_validate", return_value=MagicMock(raw={})),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.core.validate_order",
        MagicMock(name="validate_order", return_value=None),
    )

    result = deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Notebook", "DataPipeline"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
    )
    assert result.items_published == 14
    assert result.items_failed == 0


def test_deploy_workspace_bulk_parallel_success(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """deploy_workspace with bulk=True executes item publish concurrently across workers."""
    fake_instance = MagicMock(name="fabric_workspace_instance")
    fake_instance.repository_items = {
        "Notebook": {"nb1": object(), "nb2": object()},
        "DataPipeline": {"pipe1": object()},
    }
    fake_ws_class = MagicMock(name="FabricWorkspace", return_value=fake_instance)
    fake_pub = MagicMock(name="publish_all_items", return_value=None)
    fake_unpub = MagicMock(name="unpublish_all_orphan_items", return_value=None)

    monkeypatch.setattr("sigantry_core.deploy.core.FabricWorkspace", fake_ws_class)
    monkeypatch.setattr("sigantry_core.deploy.core.publish_all_items", fake_pub)
    monkeypatch.setattr("sigantry_core.deploy.core.unpublish_all_orphan_items", fake_unpub)
    monkeypatch.setattr(
        "sigantry_core.deploy.core.load_and_validate",
        MagicMock(name="load_and_validate", return_value=MagicMock(raw={})),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.core.validate_order",
        MagicMock(name="validate_order", return_value=None),
    )

    result = deploy_workspace(
        workspace_id="w1",
        repository_directory=str(tmp_item_tree),
        environment="DEV",
        item_type_in_scope=["Notebook", "DataPipeline"],
        parameters_path=str(tmp_item_tree / "parameters.yml"),
        token_provider=mock_token_provider,
        bulk=True,
        max_workers=3,
    )
    assert result.items_published == 3
    assert result.items_failed == 0
    # Each item was dispatched via worker pool
    assert fake_pub.call_count == 3


def test_deploy_workspace_bulk_parallel_failure_raises(
    monkeypatch, mock_token_provider, tmp_item_tree: Path
) -> None:
    """deploy_workspace with bulk=True raises RuntimeError when any worker publish fails."""
    fake_instance = MagicMock(name="fabric_workspace_instance")
    fake_instance.repository_items = {
        "Notebook": {"nb1": object(), "nb2": object()},
    }
    fake_ws_class = MagicMock(name="FabricWorkspace", return_value=fake_instance)

    def _pub_side_effect(ws, items_to_include=None, **kwargs):
        if items_to_include and "Notebook.nb2" in items_to_include:
            raise RuntimeError("upstream error")
        return None

    fake_pub = MagicMock(name="publish_all_items", side_effect=_pub_side_effect)
    fake_unpub = MagicMock(name="unpublish_all_orphan_items", return_value=None)

    monkeypatch.setattr("sigantry_core.deploy.core.FabricWorkspace", fake_ws_class)
    monkeypatch.setattr("sigantry_core.deploy.core.publish_all_items", fake_pub)
    monkeypatch.setattr("sigantry_core.deploy.core.unpublish_all_orphan_items", fake_unpub)
    monkeypatch.setattr(
        "sigantry_core.deploy.core.load_and_validate",
        MagicMock(name="load_and_validate", return_value=MagicMock(raw={})),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.core.validate_order",
        MagicMock(name="validate_order", return_value=None),
    )

    with pytest.raises(RuntimeError) as exc_info:
        deploy_workspace(
            workspace_id="w1",
            repository_directory=str(tmp_item_tree),
            environment="DEV",
            item_type_in_scope=["Notebook"],
            parameters_path=str(tmp_item_tree / "parameters.yml"),
            token_provider=mock_token_provider,
            bulk=True,
        )
    assert "1 item(s) failed to publish" in str(exc_info.value)

