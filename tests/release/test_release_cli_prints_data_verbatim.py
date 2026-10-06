"""``sigantry release`` prints recorded names, release ids and paths as written.

Rich reads ``[...]`` in a string it prints as markup. A recorded item name, a
release id or a path that held a closing tag such as ``[/x]`` raised
``MarkupError`` (exit 1 with a traceback in place of the message), and a word
in square brackets such as ``[draft]`` was dropped from the output.

Paths are given relative to the test's working directory, so a line stays
short enough that Rich never splits a path inside a word.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

runner = CliRunner()

TAG_LIKE = "Q3 [draft].Notebook"
CLOSING_TAG = "Sales [/old].Notebook"
SHARED = "Shared [/s].Lakehouse"
AUDIT = "a[/x]"  # a directory "a[" holding "x]": the path text holds a closing tag


@pytest.fixture(autouse=True)
def _offline_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.chdir(tmp_path)


def _flat(text: str) -> str:
    """Rich wraps long lines at the console width; compare word by word."""
    return " ".join(text.split())


def _seed(
    audit_dir: Path,
    release_id: str,
    items: list[str],
    *,
    workspace: str = "ws-test",
    approver: str = "alice@example.invalid",
) -> DeployRecord:
    audit_dir.mkdir(parents=True, exist_ok=True)
    record = DeployRecord(
        workspace=workspace,
        release_id=release_id,
        work_items=[],
        fabric_items_changed=items,
        test_evidence={"smoke": "passed"},
        approver=approver,
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()
    emit_deploy_record(record, audit_dir=audit_dir)
    return record


def test_release_diff_human_prints_item_names_as_written() -> None:
    _seed(Path(AUDIT), "R-a", [TAG_LIKE, SHARED])
    _seed(Path(AUDIT), "R-b", [CLOSING_TAG, SHARED])
    result = runner.invoke(app, ["release", "diff", "R-a", "R-b", "--audit-dir", AUDIT])
    assert result.exit_code == 0, repr(result.exception)
    assert f"+ {CLOSING_TAG}" in result.stdout
    assert f"- {TAG_LIKE}" in result.stdout
    assert f"= {SHARED}" in result.stdout


@pytest.mark.parametrize("which", ["first", "second"])
def test_release_diff_missing_release_id_printed_as_written(which: str) -> None:
    _seed(Path(AUDIT), "R-a", [SHARED])
    args = ["[/x]", "R-a"] if which == "first" else ["R-a", "[/x]"]
    result = runner.invoke(app, ["release", "diff", *args, "--audit-dir", AUDIT])
    assert result.exit_code == 1, repr(result.exception)
    assert "No release [/x] in ledger." in result.stdout


def test_release_diff_html_out_path_printed_as_written(tmp_path: Path) -> None:
    _seed(Path(AUDIT), "R-a", [TAG_LIKE])
    _seed(Path(AUDIT), "R-b", [CLOSING_TAG])
    (tmp_path / "d[").mkdir()
    result = runner.invoke(
        app,
        [
            "release",
            "diff",
            "R-a",
            "R-b",
            "--audit-dir",
            AUDIT,
            "--html",
            "--html-out",
            "d[/x].html",
        ],
    )
    assert result.exit_code == 0, repr(result.exception)
    assert (tmp_path / "d[" / "x].html").is_file()
    assert "HTML release diff report written to: d[/x].html" in _flat(result.stdout)


def test_release_show_missing_release_id_printed_as_written(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    result = runner.invoke(app, ["release", "show", "[/x]", "--audit-dir", "empty"])
    assert result.exit_code == 1, repr(result.exception)
    assert "No release [/x] found in ledger." in result.stdout


def test_release_show_html_out_path_printed_as_written(tmp_path: Path) -> None:
    _seed(Path(AUDIT), "R-a", [TAG_LIKE])
    (tmp_path / "r[").mkdir()
    result = runner.invoke(
        app, ["release", "show", "R-a", "--audit-dir", AUDIT, "--html", "--html-out", "r[/x].html"]
    )
    assert result.exit_code == 0, repr(result.exception)
    assert (tmp_path / "r[" / "x].html").is_file()
    assert "HTML release report written to: r[/x].html" in _flat(result.stdout)


@pytest.mark.parametrize("ledger", ["missing", "empty"])
def test_release_verify_prints_ledger_path_as_written(ledger: str) -> None:
    Path(AUDIT).mkdir(parents=True)
    if ledger == "empty":
        (Path(AUDIT) / "deploys.jsonl").write_text("", encoding="utf-8")
    result = runner.invoke(app, ["release", "verify", "--audit-dir", AUDIT])
    assert result.exit_code == 0, repr(result.exception)
    assert "NOTHING TO VERIFY" in result.stdout
    assert str(Path(AUDIT) / "deploys.jsonl") in _flat(result.stdout)


def test_release_verify_valid_chain_prints_path_as_written() -> None:
    _seed(Path(AUDIT), "R-a", [TAG_LIKE])
    result = runner.invoke(app, ["release", "verify", "--audit-dir", AUDIT])
    assert result.exit_code == 0, repr(result.exception)
    assert "CHAIN VALID" in result.stdout
    assert str(Path(AUDIT) / "deploys.jsonl") in _flat(result.stdout)


def test_release_verify_unparseable_line_printed_as_written() -> None:
    Path(AUDIT).mkdir(parents=True)
    (Path(AUDIT) / "deploys.jsonl").write_text("not json [/y]\n", encoding="utf-8")
    result = runner.invoke(app, ["release", "verify", "--audit-dir", AUDIT])
    assert result.exit_code == 1, repr(result.exception)
    assert "CHAIN UNVERIFIABLE" in result.stdout
    assert str(Path(AUDIT) / "deploys.jsonl") in _flat(result.stdout)
    assert "line 1: not valid JSON" in result.stdout


def test_release_verify_broken_chain_prints_path_as_written() -> None:
    _seed(Path(AUDIT), "R-a", [TAG_LIKE])
    _seed(Path(AUDIT), "R-b", [CLOSING_TAG])
    ledger = Path(AUDIT) / "deploys.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["prev_hash"] = "0" * 64
    second["audit_hash"] = ""
    lines[1] = json.dumps(DeployRecord(**second).with_hash().model_dump(mode="json"))
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = runner.invoke(app, ["release", "verify", "--audit-dir", AUDIT])
    assert result.exit_code == 1, repr(result.exception)
    assert "CHAIN BROKEN" in result.stdout
    assert str(ledger) in _flat(result.stdout)
    assert "first bad record index: 1" in result.stdout


def test_release_list_prints_ledger_values_as_written() -> None:
    _seed(Path(AUDIT), "[/old]", [SHARED], workspace="ws[/w]", approver="a[b]")
    _seed(Path(AUDIT), "v1 [draft]", [SHARED], workspace="w [x]", approver="[/c]")
    result = runner.invoke(app, ["release", "list", "--audit-dir", AUDIT])
    assert result.exit_code == 0, repr(result.exception)
    for value in ("[/old]", "ws[/w]", "a[b]", "w [x]", "[/c]"):
        assert value in result.stdout, value
    assert "v1" in result.stdout and "[draft]" in result.stdout


def test_release_record_success_line_prints_release_id_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary prints after the ledger write and the work-item comments, so a
    failure there exits 1 on a record that was written."""
    provider = MagicMock(name="provider")
    import sigantry_core.workitems.ado as ado_mod

    monkeypatch.setattr(
        ado_mod.AdoWorkItemProvider, "from_defaults", MagicMock(return_value=provider)
    )
    result = runner.invoke(
        app,
        [
            "release",
            "record",
            "--provider",
            "ado",
            "--ado-organization",
            "org",
            "--ado-project",
            "proj",
            "--audit-dir",
            AUDIT,
            "--release-id",
            "[/x] [draft]",
            "--workspace",
            "ws-test",
            "--work-items",
            "1",
            "--approver",
            "alice@example.invalid",
            "--fabric-items",
            CLOSING_TAG,
        ],
    )
    assert result.exit_code == 0, repr(result.exception)
    provider.link_release.assert_called_once()
    assert len((Path(AUDIT) / "deploys.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    flat = _flat(result.stdout)
    assert "Recorded release [/x] [draft] with audit_hash" in flat
    assert "commented on 1 work items." in flat
