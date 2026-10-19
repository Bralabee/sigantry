"""``python -m sigantry_core.sync._notify_main``: what reaches the channel (#34).

``sigantry diff`` exits 2 on an operational error and leaves ``drift.json``
empty; a drift job that fails before the diff step leaves it empty too (the
templates create it first). These tests run ``main()`` against a real Teams
sink posting to a local HTTP listener and read what the listener received:
a check that produced no report must be announced as an operational failure
and exit 1, and must never be summarised as "no drift".
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sigantry_core.sync import _notify_main
from sigantry_core.sync.diff import DriftReport

_ADDED = {"logical_id": "x", "display_name": "N", "type": "Notebook", "folder_path": ""}


class _Listener:
    def __init__(self) -> None:
        self.bodies: list[str] = []
        received = self.bodies

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                received.append(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def listener(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Listener]:
    hook = _Listener()
    for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "BUILD_BUILDID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIGANTRY_NOTIFICATION_SINK", "teams")
    monkeypatch.setenv("SIGANTRY_TEAMS_WEBHOOK", hook.url)
    monkeypatch.setenv("SIGANTRY_TEAMS_WEBHOOK_FORMAT", "messagecard")
    yield hook
    hook.close()


def _run(tmp_path: Path, content: str | None) -> int:
    path = tmp_path / "drift.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    return _notify_main.main(["--drift-json", str(path), "--workspace-id", "ws-1"])


_NOT_REPORTS = {
    "missing": None,
    "empty": "",
    "whitespace": "\n",
    "diff-error-text": "Error: could not resolve workspace\n",
    "array": "[]",
    "empty-object": "{}",
    "error-object": '{"error": "token expired"}',
    "a-key-missing": json.dumps({k: v for k, v in DriftReport().to_json().items() if k != "added"}),
    "list-not-a-list": json.dumps({**DriftReport().to_json(), "added": "abc"}),
    "entries-not-objects": json.dumps({**DriftReport().to_json(), "added": ["abc"]}),
    "schema-2": json.dumps({**DriftReport().to_json(), "schema_version": "2.0.0"}),
}


@pytest.mark.parametrize("content", list(_NOT_REPORTS.values()), ids=list(_NOT_REPORTS))
def test_no_report_is_announced_as_an_operational_failure(
    tmp_path: Path, listener: _Listener, content: str | None
) -> None:
    assert _run(tmp_path, content) == 1
    assert len(listener.bodies) == 1, listener.bodies
    body = listener.bodies[0]
    assert "drift check FAILED" in body and "not drift" in body, body
    assert "drift OK" not in body and "no drift." not in body, body
    assert "token expired" not in body and "could not resolve" not in body, (
        "the file's content must not be echoed into the channel"
    )


@pytest.mark.parametrize(
    ("report", "expected", "absent"),
    [
        (DriftReport(), "drift OK", "FAILED"),
        (DriftReport(added=[_ADDED]), "+1 added", "FAILED"),
    ],
    ids=["no-drift", "drift"],
)
def test_a_drift_report_is_summarised_as_before(
    tmp_path: Path, listener: _Listener, report: DriftReport, expected: str, absent: str
) -> None:
    assert _run(tmp_path, json.dumps(report.to_json())) == 0
    assert len(listener.bodies) == 1 and expected in listener.bodies[0], listener.bodies
    assert absent not in listener.bodies[0]


def test_a_newer_minor_schema_with_an_extra_key_is_still_a_report(
    tmp_path: Path, listener: _Listener
) -> None:
    payload = {**DriftReport().to_json(), "schema_version": "1.1.0", "summary": {}}
    assert _run(tmp_path, json.dumps(payload)) == 0
    assert "drift OK" in listener.bodies[0]


def test_the_failure_event_links_the_run(
    tmp_path: Path, listener: _Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    assert _run(tmp_path, "") == 1
    assert "https://github.com/o/r/actions/runs/42" in listener.bodies[0]


def test_ado_run_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SYSTEM_COLLECTIONURI", "https://dev.azure.com/org/")
    monkeypatch.setenv("SYSTEM_TEAMPROJECT", "proj")
    monkeypatch.setenv("BUILD_BUILDID", "7")
    assert _notify_main._run_url() == "https://dev.azure.com/org/proj/_build/results?buildId=7"


def test_an_invalid_sink_configuration_sends_nothing_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGANTRY_NOTIFICATION_SINK", "")
    assert _run(tmp_path, "") == 1
