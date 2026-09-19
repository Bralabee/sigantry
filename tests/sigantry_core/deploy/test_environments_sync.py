"""Behaviour tests for the env sync-all fan-out orchestrator.

The single-target ``sync_wheel`` and the published-library check are injected so
these run with no tenant: we assert the risk controls (gating, idempotency,
fail-isolation, fail-fast, dry-run, resolution errors).
"""

from __future__ import annotations

from pathlib import Path

from sigantry_core.deploy.environment import WheelUploadResult
from sigantry_core.deploy.environments_manifest import EnvironmentsManifest
from sigantry_core.deploy.environments_sync import (
    TARGET_FAILED,
    TARGET_GATED,
    TARGET_PROCESSED,
    WHEEL_DRY_RUN,
    WHEEL_FAILED,
    WHEEL_SKIPPED,
    WHEEL_SYNCED,
    sync_environments,
)

_CLIENT = object()  # opaque; injected fns ignore it


def _wheel(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"wheel")
    return p


def _manifest(targets: list[dict]) -> EnvironmentsManifest:
    return EnvironmentsManifest.model_validate({"schema_version": "1.0", "targets": targets})


def _ok_sync(calls: list[tuple]):
    def _fn(client, ws, env, wheel) -> WheelUploadResult:
        calls.append((ws, env, Path(wheel).name))
        return WheelUploadResult(
            workspace_id=ws,
            environment_id=env,
            wheel_name=Path(wheel).name,
            staging_upload_status="ok",
            publish_lro_status="Success",
            installed_library_name=Path(wheel).name,
        )

    return _fn


def test_dry_run_does_no_work(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "aims_data_platform-1.6.0-py3-none-any.whl")
    m = _manifest([{"name": "dev", "workspace_id": "w", "environment_id": "e", "wheels": [str(w)]}])

    def _boom(*_a, **_k):  # must never be called in dry-run
        raise AssertionError("no tenant work in dry-run")

    report = sync_environments(None, m, dry_run=True, sync_wheel_fn=_boom, is_published_fn=_boom)
    assert report.ok
    assert report.targets[0].wheels[0].action == WHEEL_DRY_RUN


def test_gated_skipped_by_default(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest(
        [
            {"name": "dev", "workspace_id": "w", "environment_id": "e", "wheels": [str(w)]},
            {
                "name": "prod",
                "workspace_id": "wp",
                "environment_id": "ep",
                "wheels": [str(w)],
                "gated": True,
            },
        ]
    )
    calls: list[tuple] = []
    report = sync_environments(
        _CLIENT, m, sync_wheel_fn=_ok_sync(calls), is_published_fn=lambda *_: False
    )
    dev, prod = report.targets
    assert dev.action == TARGET_PROCESSED and dev.wheels[0].action == WHEEL_SYNCED
    assert prod.action == TARGET_GATED and prod.wheels == ()
    # PROD wheel never touched
    assert all(ws != "wp" for ws, _e, _n in calls)


def test_include_gated_acts_on_prod(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest(
        [
            {
                "name": "prod",
                "workspace_id": "wp",
                "environment_id": "ep",
                "wheels": [str(w)],
                "gated": True,
            }
        ]
    )
    calls: list[tuple] = []
    report = sync_environments(
        _CLIENT,
        m,
        include_gated=True,
        sync_wheel_fn=_ok_sync(calls),
        is_published_fn=lambda *_: False,
    )
    assert report.targets[0].action == TARGET_PROCESSED
    assert ("wp", "ep", "x-1.0.0-py3-none-any.whl") in calls


def test_idempotent_skip_when_already_published(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest([{"name": "dev", "workspace_id": "w", "environment_id": "e", "wheels": [str(w)]}])
    calls: list[tuple] = []
    report = sync_environments(
        _CLIENT, m, sync_wheel_fn=_ok_sync(calls), is_published_fn=lambda *_: True
    )
    assert report.targets[0].wheels[0].action == WHEEL_SKIPPED
    assert calls == []  # no costly republish


def test_force_republishes_even_if_present(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest([{"name": "dev", "workspace_id": "w", "environment_id": "e", "wheels": [str(w)]}])
    calls: list[tuple] = []
    report = sync_environments(
        _CLIENT, m, force=True, sync_wheel_fn=_ok_sync(calls), is_published_fn=lambda *_: True
    )
    assert report.targets[0].wheels[0].action == WHEEL_SYNCED
    assert len(calls) == 1


def test_fail_isolation_continues_other_targets(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest(
        [
            {"name": "bad", "workspace_id": "wb", "environment_id": "eb", "wheels": [str(w)]},
            {"name": "good", "workspace_id": "wg", "environment_id": "eg", "wheels": [str(w)]},
        ]
    )

    def _sync(client, ws, env, wheel):
        if ws == "wb":
            raise RuntimeError("publish failed")
        return WheelUploadResult(ws, env, Path(wheel).name, "ok", "Success", Path(wheel).name)

    report = sync_environments(_CLIENT, m, sync_wheel_fn=_sync, is_published_fn=lambda *_: False)
    bad, good = report.targets
    assert bad.action == TARGET_FAILED and bad.wheels[0].action == WHEEL_FAILED
    assert good.action == TARGET_PROCESSED and good.wheels[0].action == WHEEL_SYNCED
    assert report.ok is False  # overall non-zero


def test_fail_fast_stops_after_first_failure(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest(
        [
            {"name": "bad", "workspace_id": "wb", "environment_id": "eb", "wheels": [str(w)]},
            {"name": "good", "workspace_id": "wg", "environment_id": "eg", "wheels": [str(w)]},
        ]
    )

    def _sync(client, ws, env, wheel):
        raise RuntimeError("boom")

    report = sync_environments(
        _CLIENT, m, fail_fast=True, sync_wheel_fn=_sync, is_published_fn=lambda *_: False
    )
    # second target never processed
    assert len(report.targets) == 1
    assert report.targets[0].name == "bad"


def test_missing_wheel_fails_target_only(tmp_path: Path) -> None:
    good = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest(
        [
            {
                "name": "missing",
                "workspace_id": "wm",
                "environment_id": "em",
                "wheels": [str(tmp_path / "does-not-exist-*.whl")],
            },
            {"name": "ok", "workspace_id": "wo", "environment_id": "eo", "wheels": [str(good)]},
        ]
    )
    report = sync_environments(
        _CLIENT, m, sync_wheel_fn=_ok_sync([]), is_published_fn=lambda *_: False
    )
    miss, ok = report.targets
    assert miss.action == TARGET_FAILED and "no wheel matched" in (miss.error or "")
    assert ok.action == TARGET_PROCESSED


def test_pin_missing_literal_wheel_fails(tmp_path: Path) -> None:
    m = _manifest(
        [
            {
                "name": "prod",
                "workspace_id": "wp",
                "environment_id": "ep",
                "wheels": [str(tmp_path / "aims_data_platform-9.9.9-py3-none-any.whl")],
                "policy": "pin",
            }
        ]
    )
    report = sync_environments(
        _CLIENT, m, include_gated=True, sync_wheel_fn=_ok_sync([]), is_published_fn=lambda *_: False
    )
    assert report.targets[0].action == TARGET_FAILED
    assert report.ok is False


def test_report_summary_counts(tmp_path: Path) -> None:
    w = _wheel(tmp_path, "x-1.0.0-py3-none-any.whl")
    m = _manifest(
        [
            {"name": "dev", "workspace_id": "w", "environment_id": "e", "wheels": [str(w)]},
            {
                "name": "prod",
                "workspace_id": "wp",
                "environment_id": "ep",
                "wheels": [str(w)],
                "gated": True,
            },
        ]
    )
    report = sync_environments(
        _CLIENT, m, sync_wheel_fn=_ok_sync([]), is_published_fn=lambda *_: False
    )
    summary = report.to_dict()["summary"]
    assert summary["wheelsSynced"] == 1
    assert summary["targetsGatedSkipped"] == 1
    assert summary["targets"] == 2
