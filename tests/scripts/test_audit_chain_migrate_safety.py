"""Data-safety contract of ``scripts/audit_chain_migrate.py``.

The migration re-seals every record it touches, so a mistake in it does not
look like a mistake afterwards: ``sigantry release verify`` reports whatever
it wrote as a valid chain. Before this contract a re-run on a copy of a real
ledger replayed the ``.pre-w3.1.bak`` backup over the live file, dropping 13
of 63 deploy records at exit 0 with verify VALID; a hand-edited record was
re-sealed into a verifying chain; and a ledger with a broken third link was
passed as "already chained" and left broken.

Each test below fails against that version of the script.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
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


def _legacy_line(i: int) -> dict[str, Any]:
    """A pre-W3.1 on-disk record: no ``prev_hash`` key, hash over the rest."""
    payload = _record(i).model_dump(mode="json")
    payload.pop("audit_hash", None)
    payload.pop("prev_hash", None)
    payload["audit_hash"] = hashlib.sha256(json.dumps(payload, **_KW).encode()).hexdigest()
    return payload


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines), encoding="utf-8")


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _emit_chained(audit_dir: Path, ids: range) -> None:
    for i in ids:
        emit_deploy_record(_record(i).with_hash(), audit_dir=audit_dir)


def _verifies(path: Path) -> bool:
    ok, _, _ = verify_audit_chain([DeployRecord(**x) for x in _read(path)])
    return ok


def _snapshot(audit_dir: Path) -> dict[str, bytes]:
    return {
        p.name: p.read_bytes() for p in sorted(audit_dir.iterdir()) if not p.name.endswith(".lock")
    }


# --- the three defects ---------------------------------------------------------


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
    assert _snapshot(audit) == before, "a refused ledger was modified or backed up"
    assert "REFUSED: deploys.jsonl: record 1 fails verification" in capsys.readouterr().err


def test_broken_link_beyond_the_second_record_is_refused(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _emit_chained(audit, range(3))
    lines = _read(audit / "deploys.jsonl")
    lines[2]["prev_hash"] = "f" * 64
    _write(audit / "deploys.jsonl", lines)
    before = _snapshot(audit)

    with pytest.raises(_mig.MigrationRefusedError, match="record 2"):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)
    assert _snapshot(audit) == before
    assert _mig.main(["--audit-dir", str(audit)]) != 0


def test_resealed_broken_link_is_refused(tmp_path: Path) -> None:
    """A link rewritten AND re-sealed passes its own hash; the link check refuses it."""
    audit = tmp_path / "audit"
    _emit_chained(audit, range(3))
    lines = _read(audit / "deploys.jsonl")
    forged = DeployRecord(**lines[2]).model_copy(update={"prev_hash": "f" * 64}).with_hash()
    lines[2] = forged.model_dump(mode="json")
    _write(audit / "deploys.jsonl", lines)

    with pytest.raises(_mig.MigrationRefusedError, match="broken chain link"):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda lines: lines[1:], "truncated head"),
        (lambda lines: [*lines, _legacy_line(7)], "unlinked but follows linked records"),
    ],
    ids=["head-dropped", "unlinked-after-chain"],
)
def test_chain_shape_violations_are_refused(tmp_path: Path, mutate: Any, reason: str) -> None:
    audit = tmp_path / "audit"
    _emit_chained(audit, range(3))
    _write(audit / "deploys.jsonl", mutate(_read(audit / "deploys.jsonl")))

    with pytest.raises(_mig.MigrationRefusedError, match=reason):
        _mig.migrate_one_file(audit / "deploys.jsonl", record_cls=DeployRecord)


def test_dry_run_still_refuses(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    lines = [_legacy_line(i) for i in range(2)]
    lines[0]["workspace"] = "edited"
    _write(audit / "deploys.jsonl", lines)
    before = _snapshot(audit)

    assert _mig.main(["--audit-dir", str(audit), "--dry-run"]) != 0
    assert _snapshot(audit) == before


# --- the shape real ledgers have -------------------------------------------------


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


def test_existing_backup_is_never_overwritten(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    live = audit / "deploys.jsonl"
    _write(live, [_legacy_line(i) for i in range(3)])
    first_backup = audit / "deploys.jsonl.pre-w3.1.bak"
    first_backup.write_bytes(b"an earlier run's backup\n")
    original = live.read_bytes()

    _mig.migrate_one_file(live, record_cls=DeployRecord)

    assert first_backup.read_bytes() == b"an earlier run's backup\n"
    assert (audit / "deploys.jsonl.pre-w3.1.bak.1").read_bytes() == original
    assert _verifies(live)


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
    _write(audit / "deploys.jsonl.pre-w3.1.bak", [_legacy_line(i) for i in range(3)])

    assert _mig.main(["--audit-dir", str(audit)]) == 0

    assert [r["release_id"] for r in _read(audit / "deploys.jsonl")] == ["R-0", "R-1", "R-2"]
    assert _verifies(audit / "deploys.jsonl")


def test_migration_holds_the_writer_lock_across_read_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "audit"
    live = audit / "deploys.jsonl"
    _write(live, [_legacy_line(i) for i in range(2)])
    events: list[str] = []
    real_replace = _mig._atomic_replace
    real_read_bytes = Path.read_bytes

    @contextmanager
    def recording_lock(path: Path) -> Iterator[None]:
        assert path == live
        events.append("lock")
        yield
        events.append("unlock")

    def recording_read_bytes(self: Path) -> bytes:
        events.append("read")
        return real_read_bytes(self)

    def recording_replace(path: Path, text: str) -> None:
        events.append("replace")
        real_replace(path, text)

    monkeypatch.setattr(_mig, "audit_chain_lock", recording_lock)
    monkeypatch.setattr(Path, "read_bytes", recording_read_bytes)
    monkeypatch.setattr(_mig, "_atomic_replace", recording_replace)

    _mig.migrate_one_file(live, record_cls=DeployRecord)

    assert events == ["lock", "read", "replace", "unlock"]
