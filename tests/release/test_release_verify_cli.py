"""CliRunner tests for ``sigantry release verify``.

The command exposes the ledger's tamper-evidence to an operator. These tests
are written fail-direction-first: each break arm mutates a real seeded ledger
and asserts the command REPORTS the break, because a verifier that has only
ever been observed passing is not evidence of anything.

The two that matter most:

- ``test_reseal_forgery_is_caught_at_following_record`` -- the property that
  distinguishes a chain from a row of independent hashes. An attacker who
  edits a record AND recomputes that record's own ``audit_hash`` produces a
  self-consistent record; it is the NEXT record's ``prev_hash`` that breaks.
- ``test_unparseable_line_is_reported_not_skipped`` -- guards the design
  decision in ``_read_ledger_lines_strict``. ``ledger.iter_records`` logs and
  skips unparseable lines by design; had ``verify`` been built on it, a
  corrupt line would be misdiagnosed as a hash mismatch on the following
  record and the record count would silently understate the file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

runner = CliRunner()


def _seed(tmp_path: Path, *, release_id: str, offset_seconds: int = 0) -> None:
    record = DeployRecord(
        workspace="ws-test",
        release_id=release_id,
        work_items=[],
        fabric_items_changed=["nb_a.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC) + timedelta(seconds=offset_seconds),
    ).with_hash()
    emit_deploy_record(record, audit_dir=tmp_path)


def _seed_chain(tmp_path: Path, count: int = 3) -> None:
    for i in range(count):
        _seed(tmp_path, release_id=f"R{i}", offset_seconds=i)


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "deploys.jsonl"


def _read_lines(tmp_path: Path) -> list[dict]:
    raw = _ledger(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in raw if line.strip()]


def _write_lines(tmp_path: Path, payloads: list[dict]) -> None:
    body = "\n".join(json.dumps(p) for p in payloads) + "\n"
    _ledger(tmp_path).write_text(body, encoding="utf-8")


def _verify(tmp_path: Path, *extra: str):
    return runner.invoke(app, ["release", "verify", "--audit-dir", str(tmp_path), *extra])


# --- clean direction ---------------------------------------------------------


def test_verify_subcommand_listed_in_release_help() -> None:
    result = runner.invoke(app, ["release", "--help"])
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    assert "verify" in result.stdout


def test_verify_help_resolves() -> None:
    result = runner.invoke(app, ["release", "verify", "--help"])
    assert result.exit_code == 0, f"stdout={result.stdout!r}"


def test_valid_chain_passes(tmp_path: Path) -> None:
    _seed_chain(tmp_path, 3)
    result = _verify(tmp_path)
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    assert "CHAIN VALID" in result.stdout
    assert "3 record" in result.stdout


def test_absent_ledger_is_vacuous_not_silently_green(tmp_path: Path) -> None:
    """Exit 0, but the output must SAY there was nothing to verify.

    A verifier that prints a plain success over an empty ledger is
    indistinguishable from one that verified real records.
    """
    result = _verify(tmp_path)
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    assert "NOTHING TO VERIFY" in result.stdout

    payload = json.loads(_verify(tmp_path, "--json").stdout)
    assert payload["records"] == 0
    assert payload["vacuous"] is True


def test_json_mode_shape(tmp_path: Path) -> None:
    _seed_chain(tmp_path, 2)
    result = _verify(tmp_path, "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["records"] == 2
    assert payload["vacuous"] is False
    assert payload["first_bad_index"] is None


# --- break arms --------------------------------------------------------------


def test_field_tamper_is_caught(tmp_path: Path) -> None:
    """Edit a field without touching hashes -- verify_hash() fails at that record."""
    _seed_chain(tmp_path, 3)
    payloads = _read_lines(tmp_path)
    payloads[1]["approver"] = "mallory@example.invalid"
    _write_lines(tmp_path, payloads)

    result = _verify(tmp_path)
    assert result.exit_code == 1, f"stdout={result.stdout!r}"
    assert "CHAIN BROKEN" in result.stdout

    payload = json.loads(_verify(tmp_path, "--json").stdout)
    assert payload["valid"] is False
    assert payload["first_bad_index"] == 1


def test_deletion_is_caught(tmp_path: Path) -> None:
    """Remove a middle record -- the successor's prev_hash no longer matches."""
    _seed_chain(tmp_path, 3)
    payloads = _read_lines(tmp_path)
    del payloads[1]
    _write_lines(tmp_path, payloads)

    result = _verify(tmp_path)
    assert result.exit_code == 1, f"stdout={result.stdout!r}"
    assert "CHAIN BROKEN" in result.stdout


def test_reorder_is_caught(tmp_path: Path) -> None:
    _seed_chain(tmp_path, 3)
    payloads = _read_lines(tmp_path)
    payloads[1], payloads[2] = payloads[2], payloads[1]
    _write_lines(tmp_path, payloads)

    result = _verify(tmp_path)
    assert result.exit_code == 1, f"stdout={result.stdout!r}"


def test_reseal_forgery_is_caught_at_following_record(tmp_path: Path) -> None:
    """The decisive chain property.

    An attacker edits record 1 and recomputes record 1's OWN audit_hash, so
    record 1 is internally self-consistent. Because ``prev_hash`` is inside the
    hashed payload, record 2 still carries the OLD hash of record 1 -- so the
    break surfaces at index 2, not index 1. A row of independent hashes would
    not catch this at all.
    """
    _seed_chain(tmp_path, 3)
    payloads = _read_lines(tmp_path)

    forged = dict(payloads[1])
    forged["approver"] = "mallory@example.invalid"
    forged["audit_hash"] = ""
    resealed = DeployRecord(**forged).with_hash()
    payloads[1] = json.loads(resealed.model_dump_json())
    _write_lines(tmp_path, payloads)

    payload = json.loads(_verify(tmp_path, "--json").stdout)
    assert payload["valid"] is False
    # Record 1 verifies against itself; the FOLLOWING record exposes the forgery.
    assert payload["first_bad_index"] == 2, payload
    assert "prev_hash" in (payload["reason"] or "")


def test_unparseable_line_is_reported_not_skipped(tmp_path: Path) -> None:
    """Regression guard for the ``_read_ledger_lines_strict`` design decision.

    ``ledger.iter_records`` logs-and-skips a corrupt line by design, which is
    right for traversal and wrong for verification. Measured against a
    skip-on-corrupt reader, this ledger reports ``CHAIN BROKEN`` at index 1
    with an ``audit_hash`` mismatch -- the corrupt line is never named, and the
    record count silently understates what was on disk. The operator is told
    a hash disagreed when the truth is that a line is unreadable, and the
    two demand different responses.

    So the assertion is on the DIAGNOSIS, not merely on a non-zero exit: the
    offending line must be named. Verified by break arm -- reverting to
    skip-on-corrupt fails this test and only this test.
    """
    _seed_chain(tmp_path, 3)
    raw = _ledger(tmp_path).read_text(encoding="utf-8").splitlines()
    raw[1] = "{ this is not json"
    _ledger(tmp_path).write_text("\n".join(raw) + "\n", encoding="utf-8")

    result = _verify(tmp_path)
    assert result.exit_code == 1, f"stdout={result.stdout!r}"
    assert "CHAIN UNVERIFIABLE" in result.stdout

    payload = json.loads(_verify(tmp_path, "--json").stdout)
    assert payload["valid"] is False
    assert payload["problems"], "the offending line must be named, not swallowed"


def test_head_tamper_is_caught(tmp_path: Path) -> None:
    """Deleting record 0 leaves a non-None prev_hash on the new head."""
    _seed_chain(tmp_path, 3)
    payloads = _read_lines(tmp_path)
    del payloads[0]
    _write_lines(tmp_path, payloads)

    payload = json.loads(_verify(tmp_path, "--json").stdout)
    assert payload["valid"] is False
    assert payload["first_bad_index"] == 0
