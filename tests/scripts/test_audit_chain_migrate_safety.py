"""Data-safety contract of ``scripts/audit_chain_migrate.py``.

The migration re-seals every record it touches, so a mistake in it does not
look like a mistake afterwards: ``sigantry release verify`` reports whatever
it wrote as a valid chain. Before this contract a re-run on a copy of a real
ledger replayed the ``.pre-w3.1.bak`` backup over the live file, dropping 13
of 63 deploy records at exit 0 with verify VALID; a hand-edited record was
re-sealed into a verifying chain; and a ledger with a broken third link was
passed as "already chained" and left broken.

The tests under "refusals" and "backups and recovery" each fail against that
version of the script; the "real ledger shape" tests are positive controls
that pass on both.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.governance.audit_io import verify_audit_chain
from sigantry_core.release.record import DeployRecord


def _load_migrate_module():
    # ``tests/scripts/__init__.py`` shadows the real ``scripts/`` package;
    # load the file directly (same pattern as test_audit_chain_migrate_atomic).
    script = Path(__file__).resolve().parents[2] / "scripts" / "audit_chain_migrate.py"
    spec = importlib.util.spec_from_file_location("audit_chain_migrate_safety_under_test", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mig = _load_migrate_module()
_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


def _record(i: int, *, approver: str = "alice@corp.example") -> DeployRecord:
    return DeployRecord(
        workspace=f"ws-{i}",
        release_id=f"R-{i}",
        work_items=[f"AB#{1000 + i}"],
        fabric_items_changed=[f"nb{i}.Notebook"],
        test_evidence={"smoke": "passed"},
        approver=approver,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _legacy_line(i: int, **fields: Any) -> dict[str, Any]:
    """A pre-W3.1 on-disk record: no ``prev_hash`` key, hash over the rest."""
    payload = _record(i).model_dump(mode="json")
    payload.pop("audit_hash", None)
    payload.pop("prev_hash", None)
    payload.update(fields)
    payload["audit_hash"] = hashlib.sha256(json.dumps(payload, **_KW).encode()).hexdigest()
    return payload


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in lines),
        encoding="utf-8",
    )


def _read(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(x) for x in text.split("\n") if x.strip()]


def _emit_chained(audit_dir: Path, ids: range) -> None:
    for i in ids:
        emit_deploy_record(_record(i).with_hash(), audit_dir=audit_dir)


def _verifies(path: Path) -> bool:
    ok, _, _ = verify_audit_chain([DeployRecord(**x) for x in _read(path)])
    return ok


def _snapshot(audit_dir: Path) -> dict[str, bytes]:
    """Every file in the directory, lock files included, by content."""
    return {p.name: p.read_bytes() for p in sorted(audit_dir.iterdir())}


def _refused(audit: Path, match: str) -> None:
    """Assert migrate_one_file refuses the ledger and changes nothing on disk."""
    before = _snapshot(audit)
    with pytest.raises(_mig.MigrationRefusedError, match=match):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)
    after = {k: v for k, v in _snapshot(audit).items() if not k.endswith(".lock")}
    assert after == {k: v for k, v in before.items() if not k.endswith(".lock")}
    assert set(_snapshot(audit)) - set(before) <= {"deploys.jsonl.lock"}


# --- refusals --------------------------------------------------------------------


def test_rerun_keeps_records_written_after_the_first_migration(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl", [_legacy_line(i) for i in range(3)])
    assert _mig.main(["--audit-dir", str(audit)]) == 0
    _emit_chained(audit, range(90, 92))  # real writes after the migration
    before = [r["release_id"] for r in _read(audit / "deploys.jsonl")]
    assert len(before) == 5

    assert _mig.main(["--audit-dir", str(audit)]) == 0

    after = [r["release_id"] for r in _read(audit / "deploys.jsonl")]
    assert after == before, f"re-run lost records: {sorted(set(before) - set(after))}"
    assert _verifies(audit / "deploys.jsonl")


def test_record_that_fails_its_own_hash_is_refused_not_resealed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    audit = tmp_path / "audit"
    lines = [_legacy_line(i) for i in range(3)]
    lines[1]["approver"] = "mallory@evil.example"  # edited after sealing
    _write(audit / "deploys.jsonl", lines)
    before = _snapshot(audit)

    rc = _mig.main(["--audit-dir", str(audit)])

    assert rc != 0
    after = {k: v for k, v in _snapshot(audit).items() if not k.endswith(".lock")}
    assert after == before, "a refused ledger was modified or backed up"
    assert "REFUSED: deploys.jsonl: record 1 fails verification" in capsys.readouterr().err


def test_edit_to_a_chained_record_in_the_real_ledger_shape_is_refused(tmp_path: Path) -> None:
    """Legacy prefix + chained suffix, with a field of a CHAINED record edited."""
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl", [_legacy_line(i) for i in range(4)])
    _emit_chained(audit, range(10, 13))
    lines = _read(audit / "deploys.jsonl")
    lines[5]["approver"] = "mallory@evil.example"  # prev_hash left alone
    _write(audit / "deploys.jsonl", lines)

    _refused(audit, "record 5 fails verification")


def test_broken_link_beyond_the_second_record_is_refused(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _emit_chained(audit, range(3))
    lines = _read(audit / "deploys.jsonl")
    lines[2]["prev_hash"] = "f" * 64  # own hash no longer matches either
    _write(audit / "deploys.jsonl", lines)

    _refused(audit, "record 2 fails verification")
    assert _mig.main(["--audit-dir", str(audit)]) != 0


def test_resealed_broken_link_is_refused(tmp_path: Path) -> None:
    """A link rewritten AND re-sealed passes its own hash; the link check refuses it."""
    audit = tmp_path / "audit"
    _emit_chained(audit, range(3))
    lines = _read(audit / "deploys.jsonl")
    forged = DeployRecord(**lines[2]).model_copy(update={"prev_hash": "f" * 64}).with_hash()
    lines[2] = forged.model_dump(mode="json")
    _write(audit / "deploys.jsonl", lines)

    _refused(audit, "record 2 prev_hash does not match record 1")


def _reset_at(index: int):
    def mutate(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reset = DeployRecord(**lines[index]).model_copy(update={"prev_hash": None}).with_hash()
        lines[index] = reset.model_dump(mode="json")
        return lines

    return mutate


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda lines: lines[1:], "truncated head"),
        (lambda lines: [*lines, _legacy_line(7)], "has no prev_hash but follows chained records"),
        (_reset_at(1), "record 1 restarts the chain"),
        (_reset_at(2), "record 2 restarts the chain"),
    ],
    ids=["head-dropped", "legacy-after-chain", "reset-at-1", "reset-at-2"],
)
def test_chain_shape_violations_are_refused(tmp_path: Path, mutate: Any, reason: str) -> None:
    """A reset head at index 1 is refused exactly like one further down the chain."""
    audit = tmp_path / "audit"
    _emit_chained(audit, range(4))
    _write(audit / "deploys.jsonl", mutate(_read(audit / "deploys.jsonl")))

    _refused(audit, reason)


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (b"\xff\xfe not utf-8\n", "not valid UTF-8"),
        (b'{"workspace": "x"\n', "line 1 is not a valid DeployRecord"),
        (b"[1, 2]\n", "line 1 is not a valid DeployRecord"),
    ],
    ids=["invalid-utf8", "truncated-json", "not-an-object"],
)
def test_unparseable_ledger_is_refused_and_the_others_still_run(
    tmp_path: Path, data: bytes, match: str, capsys: pytest.CaptureFixture[str]
) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "deploys.jsonl").write_bytes(data)
    _write(audit / "approvals.jsonl", [])  # a ledger the run must still reach

    assert _mig.main(["--audit-dir", str(audit)]) == 1

    out = capsys.readouterr()
    assert match in out.err
    assert "approvals.jsonl" in out.out, "one bad ledger stopped the run"
    assert (audit / "deploys.jsonl").read_bytes() == data


def test_dry_run_still_refuses_and_creates_nothing(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    lines = [_legacy_line(i) for i in range(2)]
    lines[0]["workspace"] = "edited"
    _write(audit / "deploys.jsonl", lines)
    before = _snapshot(audit)

    assert _mig.main(["--audit-dir", str(audit), "--dry-run"]) != 0
    assert _snapshot(audit) == before, "a dry run created or changed a file"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permission bits"
)
def test_dry_run_works_on_a_read_only_copy(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl", [_legacy_line(i) for i in range(2)])
    audit.chmod(0o500)
    try:
        assert _mig.main(["--audit-dir", str(audit), "--dry-run"]) == 0
    finally:
        audit.chmod(0o700)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_ledger_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "deploys.jsonl"
    _write(target, [_legacy_line(i) for i in range(2)])
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "deploys.jsonl").symlink_to(target)

    with pytest.raises(_mig.MigrationRefusedError, match="symlink"):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)
    assert (audit / "deploys.jsonl").is_symlink()


# --- backups and recovery ---------------------------------------------------------


def test_existing_backup_is_never_overwritten(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    live = audit / "deploys.jsonl"
    _write(live, [_legacy_line(i) for i in range(3)])
    first_backup = audit / "deploys.jsonl.pre-w3.1.bak"
    _write(first_backup, [_legacy_line(i) for i in range(2)])  # an earlier, contained copy
    earlier = first_backup.read_bytes()
    original = live.read_bytes()

    _mig.migrate_one_file(live, record_cls=DeployRecord)

    assert first_backup.read_bytes() == earlier
    new_backup = audit / "deploys.jsonl.pre-w3.1.bak.1"
    assert new_backup.read_bytes() == original
    assert _verifies(live)
    assert not [p.name for p in audit.iterdir() if p.name.endswith(".tmp")], "temp file left"
    if sys.platform != "win32":
        assert stat.S_IMODE(new_backup.stat().st_mode) == 0o600


def test_backup_holding_records_the_live_ledger_lacks_is_refused(tmp_path: Path) -> None:
    """The old script renamed the ledger to the backup and died; a writer then
    started a fresh ledger. The pre-migration records exist only in the backup,
    and the live ledger alone would pass as "already chained"."""
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl.pre-w3.1.bak", [_legacy_line(i) for i in range(4)])
    _emit_chained(audit, range(99, 100))

    _refused(audit, "deploys.jsonl.pre-w3.1.bak holds 4 record")
    assert _mig.main(["--audit-dir", str(audit)]) == 1


def test_empty_live_ledger_does_not_hide_a_backup(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl.pre-w3.1.bak", [_legacy_line(i) for i in range(4)])
    (audit / "deploys.jsonl").write_bytes(b"")

    _refused(audit, "holds 4 record")


def test_missing_live_ledger_with_later_backups_is_refused(tmp_path: Path) -> None:
    """Only the old script's interrupted rename leaves the live file absent.

    If later backups exist, something else removed it; replaying the first
    backup would drop every record in them.
    """
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl.pre-w3.1.bak", [_legacy_line(i) for i in range(2)])
    _write(audit / "deploys.jsonl.pre-w3.1.bak.1", [_legacy_line(i) for i in range(4)])

    with pytest.raises(_mig.MigrationRefusedError, match="later backups exist"):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)
    assert not (audit / "deploys.jsonl").exists()


def test_main_recovers_an_interrupted_legacy_run(tmp_path: Path) -> None:
    """``main`` finds a ledger that exists only as its backup (WR-03 state)."""
    audit = tmp_path / "audit"
    backup = audit / "deploys.jsonl.pre-w3.1.bak"
    _write(backup, [_legacy_line(i) for i in range(3)])
    held = backup.read_bytes()

    assert _mig.main(["--audit-dir", str(audit)]) == 0

    assert [r["release_id"] for r in _read(audit / "deploys.jsonl")] == ["R-0", "R-1", "R-2"]
    assert _verifies(audit / "deploys.jsonl")
    assert backup.read_bytes() == held, "the only pre-migration copy was changed"
    assert not (audit / "deploys.jsonl.pre-w3.1.bak.1").exists()


def test_recovery_from_an_already_chained_backup_restores_the_live_ledger(
    tmp_path: Path,
) -> None:
    audit = tmp_path / "audit"
    _emit_chained(audit, range(3))
    (audit / "deploys.jsonl").rename(audit / "deploys.jsonl.pre-w3.1.bak")

    _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)

    assert [r["release_id"] for r in _read(audit / "deploys.jsonl")] == ["R-0", "R-1", "R-2"]
    assert _verifies(audit / "deploys.jsonl")


# --- the real ledger shape (positive controls) -------------------------------------


def test_legacy_prefix_then_chained_suffix_is_migrated_whole(tmp_path: Path) -> None:
    """Real ledgers: pre-W3.1 records, then W3.1 records linked onto the last of them."""
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl", [_legacy_line(i) for i in range(4)])
    _emit_chained(audit, range(10, 13))
    live = audit / "deploys.jsonl"
    original = live.read_bytes()
    assert _read(live)[4]["prev_hash"] == _read(live)[3]["audit_hash"]

    processed, changed = _mig.migrate_one_file(live, record_cls=DeployRecord)

    assert (processed, changed) == (7, 7)
    assert [r["release_id"] for r in _read(live)] == [f"R-{i}" for i in (0, 1, 2, 3, 10, 11, 12)]
    assert _verifies(live)
    assert (audit / "deploys.jsonl.pre-w3.1.bak").read_bytes() == original


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_line_separator_characters_inside_a_field_do_not_split_the_record(
    tmp_path: Path, separator: str
) -> None:
    """The writers emit these raw (ensure_ascii=False); only ``\\n`` ends a record."""
    audit = tmp_path / "audit"
    lines = [_legacy_line(0), _legacy_line(1, approver=f"Ann{separator}Lee"), _legacy_line(2)]
    _write(audit / "deploys.jsonl", lines)

    assert _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord) == (3, 3)
    assert _read(audit / "deploys.jsonl")[1]["approver"] == f"Ann{separator}Lee"
    assert _verifies(audit / "deploys.jsonl")


def test_migration_holds_the_writer_lock_across_choice_read_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "audit"
    live = audit / "deploys.jsonl"
    _write(live, [_legacy_line(i) for i in range(2)])
    events: list[str] = []
    real_replace = _mig._atomic_replace
    real_read_bytes = Path.read_bytes
    real_is_file = Path.is_file

    @contextmanager
    def recording_lock(path: Path) -> Iterator[None]:
        assert path == live
        events.append("lock")
        yield
        events.append("unlock")

    def recording_is_file(self: Path) -> bool:
        if self == live:
            events.append("choose")
        return real_is_file(self)

    def recording_read_bytes(self: Path) -> bytes:
        if self == live:
            events.append("read")
        return real_read_bytes(self)

    def recording_replace(path: Path, text: str) -> None:
        events.append("replace")
        real_replace(path, text)

    monkeypatch.setattr(_mig, "audit_chain_lock", recording_lock)
    monkeypatch.setattr(Path, "is_file", recording_is_file)
    monkeypatch.setattr(Path, "read_bytes", recording_read_bytes)
    monkeypatch.setattr(_mig, "_atomic_replace", recording_replace)

    _mig.migrate_one_file(live, record_cls=DeployRecord)

    inside = events[events.index("lock") + 1 : events.index("unlock")]
    assert inside[0] == "choose", f"read source not chosen under the lock: {events}"
    assert "read" in inside and inside[-1] == "replace", events


def test_a_record_appended_while_waiting_for_the_lock_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the backup exists; a writer creates the live ledger while we wait.

    Choosing the read source before taking the lock would recover the backup
    over the writer's record.
    """
    audit = tmp_path / "audit"
    _write(audit / "deploys.jsonl.pre-w3.1.bak", [_legacy_line(i) for i in range(3)])
    real_lock = _mig.audit_chain_lock

    @contextmanager
    def racing_lock(path: Path) -> Iterator[None]:
        _emit_chained(audit, range(50, 51))  # the concurrent write
        with real_lock(path):
            yield

    monkeypatch.setattr(_mig, "audit_chain_lock", racing_lock)

    with pytest.raises(_mig.MigrationRefusedError, match="holds 3 record"):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)
    assert [r["release_id"] for r in _read(audit / "deploys.jsonl")] == ["R-50"]
