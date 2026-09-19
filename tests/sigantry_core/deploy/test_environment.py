"""Unit tests for sigantry_core.deploy.environment (Pitfall 6).

Covers the four-step upload -> publish-trigger -> await-build -> verify
sequence, the 300 MB size cap, the optional SHA-256 check, the publish
failure/timeout paths, and the WheelUploadResult shape.

The publish step is NOT a pollable LRO: ``POST .../staging/publish`` returns
200 synchronously and the Spark image rebuilds asynchronously. ``sync_wheel``
therefore polls the environment item's ``publishDetails.state`` until terminal
(confirmed live 2026-06-13: ``Running -> Success`` over ~6 min). These tests
drive that loop with stubbed states and a patched ``time.sleep``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sigantry_core.client import FabricRestClient, HttpResponse
from sigantry_core.client.errors import NotFoundError
from sigantry_core.deploy import environment as env_mod
from sigantry_core.deploy.environment import (
    WheelHashMismatchError,
    WheelPublishFailedError,
    WheelPublishTimeoutError,
    WheelTooLargeError,
    WheelUploadResult,
    _wheel_pkg_name,
    reconcile_wheels,
    sync_wheel,
)


def _resp(json_body: dict | None = None, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        json_body=json_body if json_body is not None else {},
        headers={},
        request_id="req",
        operation_id=None,
        elapsed_ms=1.0,
    )


def _published_body(*wheels: str) -> dict:
    """Build a realistic published-libraries response (customLibraries is a dict)."""
    return {
        "customLibraries": {
            "wheelFiles": list(wheels),
            "pyFiles": [],
            "jarFiles": [],
            "rTarFiles": [],
        },
        "environmentYml": "",
    }


def _mock_client(
    *,
    multipart_body: dict | None = None,
    publish_states: list[str] | None = None,
    libraries_body: dict | None = None,
    libraries_404: bool = False,
) -> MagicMock:
    """A FabricRestClient mock that routes ``send`` by (method, path).

    - POST .../staging/publish     -> 200 trigger (body ignored)
    - GET  .../environments/<id>   -> publishDetails.state from ``publish_states``
                                       (consumed one per poll; last value sticks)
    - GET  .../environments/<id>/libraries -> ``libraries_body`` or NotFoundError
    """
    c = MagicMock(spec=FabricRestClient)
    c.send_multipart.return_value = _resp(multipart_body or {"status": "Uploaded"})

    states = list(publish_states or ["Success"])

    def _send(method: str, path: str, **kwargs):
        if method == "POST" and path.endswith("/staging/publish"):
            return _resp({})
        if method == "GET" and path.endswith("/libraries"):
            if libraries_404:
                raise NotFoundError(404)
            return _resp(libraries_body if libraries_body is not None else _published_body())
        if method == "GET" and "/environments/" in path:
            state = states.pop(0) if len(states) > 1 else states[0]
            return _resp({"properties": {"publishDetails": {"state": state}}})
        raise AssertionError(f"unexpected send: {method} {path}")

    c.send.side_effect = _send
    return c


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the publish poll loop spin without real delays."""
    monkeypatch.setattr(env_mod.time, "sleep", lambda *_a, **_k: None)


def _tiny_wheel(tmp_path: Path, name: str = "example_plugin_wheel-1.4.4-py3-none-any.whl") -> Path:
    p = tmp_path / name
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    return p


def test_four_step_sequence(tmp_path: Path) -> None:
    wheel = _tiny_wheel(tmp_path)
    c = _mock_client(libraries_body=_published_body(wheel.name))
    result = sync_wheel(c, "ws-1", "env-1", wheel)

    # 1. multipart upload to staging/libraries with the single "file" field
    c.send_multipart.assert_called_once()
    mp_args, mp_kwargs = c.send_multipart.call_args
    assert mp_args[:2] == (
        "POST",
        "/v1/workspaces/ws-1/environments/env-1/staging/libraries",
    )
    assert list(mp_kwargs["files"].keys()) == ["file"]

    # 2-4. publish trigger, env-item poll, published-libraries verify all go via send()
    sent = [(call.args[0], call.args[1]) for call in c.send.call_args_list]
    assert ("POST", "/v1/workspaces/ws-1/environments/env-1/staging/publish") in sent
    assert ("GET", "/v1/workspaces/ws-1/environments/env-1") in sent
    assert ("GET", "/v1/workspaces/ws-1/environments/env-1/libraries") in sent
    # We verify the PUBLISHED set, never /staging/libraries.
    assert ("GET", "/v1/workspaces/ws-1/environments/env-1/staging/libraries") not in sent

    assert isinstance(result, WheelUploadResult)
    assert result.staging_upload_status == "Uploaded"
    assert result.publish_lro_status == "Success"
    assert result.installed_library_name == wheel.name


def test_publish_waits_until_success(tmp_path: Path) -> None:
    wheel = _tiny_wheel(tmp_path)
    c = _mock_client(
        publish_states=["Running", "Running", "Success"],
        libraries_body=_published_body(wheel.name),
    )
    result = sync_wheel(c, "ws-1", "env-1", wheel)
    # Three env-item GETs (two Running + final Success).
    env_polls = [
        call
        for call in c.send.call_args_list
        if call.args[0] == "GET" and call.args[1].endswith("/environments/env-1")
    ]
    assert len(env_polls) == 3
    assert result.publish_lro_status == "Success"


def test_publish_failure_raises(tmp_path: Path) -> None:
    c = _mock_client(publish_states=["Running", "Failed"])
    with pytest.raises(WheelPublishFailedError, match="Failed"):
        sync_wheel(c, "ws-1", "env-1", _tiny_wheel(tmp_path))


def test_publish_timeout_raises(tmp_path: Path) -> None:
    # Negative timeout: the first non-terminal poll trips the wall-clock cap.
    c = _mock_client(publish_states=["Running"])
    with pytest.raises(WheelPublishTimeoutError, match="did not finish"):
        sync_wheel(c, "ws-1", "env-1", _tiny_wheel(tmp_path), publish_timeout=-1.0)


def test_size_cap_rejects(tmp_path: Path) -> None:
    """301 MB wheel must raise WheelTooLargeError before any HTTP call."""
    c = _mock_client()
    big_path = tmp_path / "huge.whl"
    big_path.write_bytes(b"\x00" * (300 * 1024 * 1024 + 1))
    with pytest.raises(WheelTooLargeError, match="300 MB"):
        sync_wheel(c, "ws-1", "env-1", big_path)
    c.send_multipart.assert_not_called()
    c.send.assert_not_called()


def test_sha256_mismatch_rejects(tmp_path: Path) -> None:
    c = _mock_client()
    wheel = _tiny_wheel(tmp_path)
    with pytest.raises(WheelHashMismatchError, match="sha256"):
        sync_wheel(c, "ws-1", "env-1", wheel, expected_sha256="0" * 64)
    c.send_multipart.assert_not_called()
    c.send.assert_not_called()


def test_sha256_match_proceeds(tmp_path: Path) -> None:
    wheel = _tiny_wheel(tmp_path)
    c = _mock_client(libraries_body=_published_body(wheel.name))
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    result = sync_wheel(c, "ws-1", "env-1", wheel, expected_sha256=actual)
    c.send_multipart.assert_called_once()
    assert isinstance(result, WheelUploadResult)
    assert result.installed_library_name == wheel.name


def test_installed_name_found_real_schema(tmp_path: Path) -> None:
    wheel = _tiny_wheel(tmp_path)
    c = _mock_client(libraries_body=_published_body("other.whl", wheel.name))
    result = sync_wheel(c, "ws-1", "env-1", wheel)
    assert result.installed_library_name == wheel.name


def test_installed_name_none_when_not_listed(tmp_path: Path) -> None:
    c = _mock_client(libraries_body=_published_body("something-else.whl"))
    result = sync_wheel(c, "ws-1", "env-1", _tiny_wheel(tmp_path))
    assert result.installed_library_name is None


def test_installed_name_none_when_libraries_404(tmp_path: Path) -> None:
    c = _mock_client(libraries_404=True)
    result = sync_wheel(c, "ws-1", "env-1", _tiny_wheel(tmp_path))
    assert result.installed_library_name is None


def test_wheel_upload_result_is_frozen(tmp_path: Path) -> None:
    c = _mock_client()
    result = sync_wheel(c, "ws-1", "env-1", _tiny_wheel(tmp_path))
    with pytest.raises((AttributeError, TypeError)):
        result.wheel_name = "other.whl"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# reconcile_wheels — the add-only fix (delete superseded, upload, single publish)
# --------------------------------------------------------------------------- #


# Vendor-neutral placeholder package names: the core stays consumer-agnostic, so
# core tests must avoid consumer brand names (a repo-wide grep gate enforces it).
_PKG_A_OLD = "core_platform-1.6.0-py3-none-any.whl"
_PKG_B_OLD = "quality_gate-2.1.2-py3-none-any.whl"
_PKG_B_NEW = "quality_gate-2.2.0-py3-none-any.whl"


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("quality_gate-2.2.0-py3-none-any.whl", "quality-gate"),
        ("core_platform-1.6.0-py3-none-any.whl", "core-platform"),
        ("Some-Pkg-10.0.0rc1-py3-none-any.whl", "some-pkg"),
    ],
)
def test_wheel_pkg_name(filename: str, expected: str) -> None:
    assert _wheel_pkg_name(filename) == expected


def _reconcile_client(
    *, staging: list[str], published: list[str], publish_states: list[str] | None = None
) -> MagicMock:
    """Mock routing the reconcile call sites; records DELETEs on ``c.deleted``."""
    c = MagicMock(spec=FabricRestClient)
    c.send_multipart.return_value = _resp({"status": "Uploaded"})
    states = list(publish_states or ["Success"])
    deleted: list[str] = []

    def _send(method: str, path: str, **kwargs):
        if path.endswith("/staging/libraries"):
            if method == "GET":
                return _resp(_published_body(*staging))
            if method == "DELETE":
                deleted.append((kwargs.get("params") or {}).get("libraryToDelete"))
                return _resp({})
        if method == "POST" and path.endswith("/staging/publish"):
            return _resp({})
        if method == "GET" and path.endswith("/libraries"):  # published set
            return _resp(_published_body(*published))
        if method == "GET" and "/environments/" in path:
            state = states.pop(0) if len(states) > 1 else states[0]
            return _resp({"properties": {"publishDetails": {"state": state}}})
        raise AssertionError(f"unexpected send: {method} {path}")

    c.send.side_effect = _send
    c.deleted = deleted  # type: ignore[attr-defined]
    return c


def test_reconcile_upgrades_package_removing_superseded(tmp_path: Path) -> None:
    new = _tiny_wheel(tmp_path, _PKG_B_NEW)
    c = _reconcile_client(
        staging=[_PKG_A_OLD, _PKG_B_OLD],
        published=[_PKG_A_OLD, _PKG_B_NEW],
    )
    result = reconcile_wheels(c, "ws-1", "env-1", [new])

    # superseded version removed; the other package (not named) left alone
    assert result.deleted == [_PKG_B_OLD]
    assert result.uploaded == [_PKG_B_NEW]
    assert _PKG_A_OLD not in c.deleted
    assert result.publish_state == "Success"
    assert _PKG_B_NEW in result.published
    c.send_multipart.assert_called_once()


def test_reconcile_dry_run_makes_no_changes(tmp_path: Path) -> None:
    new = _tiny_wheel(tmp_path, _PKG_B_NEW)
    c = _reconcile_client(staging=[_PKG_B_OLD], published=[_PKG_B_OLD])
    result = reconcile_wheels(c, "ws-1", "env-1", [new], dry_run=True)

    assert result.dry_run is True
    assert result.publish_state is None
    assert result.deleted == [_PKG_B_OLD]
    assert result.uploaded == [_PKG_B_NEW]
    c.send_multipart.assert_not_called()
    assert c.deleted == []  # nothing actually deleted


def test_reconcile_noop_when_already_current(tmp_path: Path) -> None:
    new = _tiny_wheel(tmp_path, _PKG_B_NEW)
    c = _reconcile_client(
        staging=[_PKG_A_OLD, _PKG_B_NEW],
        published=[_PKG_A_OLD, _PKG_B_NEW],
    )
    result = reconcile_wheels(c, "ws-1", "env-1", [new])

    assert result.deleted == []
    assert result.uploaded == []
    assert result.publish_state is None  # no publish triggered
    c.send_multipart.assert_not_called()


def test_reconcile_republishes_when_staging_matches_but_published_missing(
    tmp_path: Path,
) -> None:
    """Interrupted prior run: staging matches desired, published does not.

    A prior ``env reconcile`` uploaded the new wheel to staging, then its
    publish was interrupted (Ctrl-C / timeout) before the Spark image rebuilt.
    The natural retry finds staging already matching desired -- but the
    PUBLISHED set still holds the OLD wheel. This MUST trigger the publish and
    block until it converges, not report a false no-op that exits 0 while the
    environment keeps serving stale code.
    """
    new = _tiny_wheel(tmp_path, _PKG_B_NEW)
    # staging carries the NEW wheel (prior run uploaded it); published still
    # holds the OLD wheel (prior publish never completed).
    published_now = {"wheels": [_PKG_B_OLD]}

    c = MagicMock(spec=FabricRestClient)
    c.send_multipart.return_value = _resp({"status": "Uploaded"})

    def _send(method: str, path: str, **kwargs):
        if path.endswith("/staging/libraries") and method == "GET":
            return _resp(_published_body(_PKG_B_NEW))
        if method == "POST" and path.endswith("/staging/publish"):
            published_now["wheels"] = [_PKG_B_NEW]  # the publish converges the set
            return _resp({})
        if method == "GET" and path.endswith("/libraries"):  # published set
            return _resp(_published_body(*published_now["wheels"]))
        if method == "GET" and "/environments/" in path:
            return _resp({"properties": {"publishDetails": {"state": "Success"}}})
        raise AssertionError(f"unexpected send: {method} {path}")

    c.send.side_effect = _send
    result = reconcile_wheels(c, "ws-1", "env-1", [new])

    # A publish WAS triggered (not a false no-op) and it converged.
    assert result.publish_state == "Success"
    assert _PKG_B_NEW in result.published
    posts = [
        call
        for call in c.send.call_args_list
        if call.args[0] == "POST" and call.args[1].endswith("/staging/publish")
    ]
    assert len(posts) == 1
