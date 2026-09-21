"""Plan 13-07 / W2 plan-checker resolution + SPEC §Constraints #1.

Locks the runtime gate behind ``workflow.preview_apis_acknowledged``: the
Phase 13 sync CLI emits a one-time WARNING on first ``sigantry sync apply``
or ``sigantry sync pull`` invocation per process when the flag is False
(default), and suppresses the warning when the flag is True.

Without this test, the docs/reference/api-stability.md claim that the gate
is enforced at runtime is unfalsifiable. With these three tests, a
regression where the warning silently never fires (or fires more than once
per process) is caught at CI time.

Council D #1: the Folders REST endpoint family is Preview as of Feb 2026;
operators acknowledge by setting the flag in `.sigantry.toml` (or via
the ``SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED`` env var).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.cli import sync_app
from sigantry_core.sync.errors import ManifestValidationError

runner = CliRunner()


@pytest.fixture
def reset_preview_warning_emitted():
    """Reset the process-scoped warning sentinel between tests.

    The helper is module-level so the "fires only once per process"
    invariant must be exercised by clearing the sentinel BEFORE each test
    that exercises the warning surface, otherwise tests bleed into each
    other depending on collection order.
    """
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()
    yield
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


def _stub_apply_to_raise_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``apply_sync`` so the warning emitter runs first, then the call
    short-circuits with a deterministic error.

    The warning is emitted at the very top of ``apply_cmd`` BEFORE the
    underlying engine is invoked. We don't need a working engine to assert
    the warning fires -- we just need the call path to reach the emitter.
    """

    def fake_apply(*_a, **_k):
        raise ManifestValidationError(
            "synthetic manifest-validation halt",
            violations=[],
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)


def test_preview_warning_emits_when_flag_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reset_preview_warning_emitted: None,
) -> None:
    """When ``preview_apis_acknowledged is False``, the WARNING fires once.

    Asserts:
    1. ``caplog`` captures exactly one WARNING-level record from the
       ``sigantry_core.sync.cli`` logger.
    2. The message body names the gate's TOML key + env var so operators
       grepping their CI logs land on the acknowledgement instructions.
    """
    # Default settings have preview_apis_acknowledged=False; we use a
    # synthetic manifest path so the underlying engine doesn't need to
    # exist (the stubbed apply_sync raises before touching disk).
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = tmp_path / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        result = runner.invoke(
            sync_app,
            [
                "apply",
                "--manifest",
                str(manifest),
                "--workspace-id",
                "ws-synthetic",
            ],
        )

    # The stubbed apply raises ManifestValidationError -> exit 1.
    assert result.exit_code == 1, result.output

    preview_records = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]
    assert len(preview_records) == 1, (
        f"Expected exactly 1 preview-API WARNING record; "
        f"got {len(preview_records)}: "
        f"{[r.message for r in preview_records]}"
    )
    msg = preview_records[0].message
    assert "preview_apis_acknowledged" in msg
    assert "SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED" in msg


def test_preview_warning_suppressed_when_flag_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reset_preview_warning_emitted: None,
) -> None:
    """With ``preview_apis_acknowledged=True``, NO warning fires.

    Sets the env var (which beats the TOML default) and asserts the
    warning emission path short-circuits before the logger call.

    Deliberately still uses the legacy ``FDT_`` prefix: this is the
    end-to-end proof that the one-minor legacy env surface keeps working
    through the CLI, not just in the loader's own unit tests.
    """
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = tmp_path / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        result = runner.invoke(
            sync_app,
            [
                "apply",
                "--manifest",
                str(manifest),
                "--workspace-id",
                "ws-synthetic",
            ],
        )

    assert result.exit_code == 1, result.output  # ManifestValidationError stub

    preview_records = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]
    assert preview_records == [], (
        f"Preview warning leaked despite preview_apis_acknowledged=true: "
        f"{[r.message for r in preview_records]}"
    )


def test_preview_warning_emits_only_once_per_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    reset_preview_warning_emitted: None,
) -> None:
    """Two consecutive ``sigantry sync apply`` calls produce ONE warning.

    The module-level ``_PREVIEW_WARNING_EMITTED`` sentinel guarantees the
    operator gets one acknowledgement nudge per process, not one per
    invocation -- a CI loop running 100 sync applies emits one WARNING,
    not 100.
    """
    _stub_apply_to_raise_validation_error(monkeypatch)
    manifest = tmp_path / "synthetic-sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        for _ in range(2):
            runner.invoke(
                sync_app,
                [
                    "apply",
                    "--manifest",
                    str(manifest),
                    "--workspace-id",
                    "ws-synthetic",
                ],
            )

    preview_records = [
        r
        for r in caplog.records
        if r.name == "sigantry_core.sync.cli"
        and r.levelno == logging.WARNING
        and "preview" in r.message.lower()
    ]
    assert len(preview_records) == 1, (
        f"Expected exactly 1 preview-API WARNING across 2 invocations; "
        f"got {len(preview_records)}: "
        f"{[r.message for r in preview_records]}"
    )


def test_non_utf8_config_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A config file with a non-UTF-8 byte must not crash the sync command.

    ``tomllib.load`` decodes the file itself, so a stray byte raises
    ``UnicodeDecodeError`` -- a ``ValueError`` sibling of
    ``TOMLDecodeError``, and caught by neither it nor ``OSError``. The
    narrowed handler therefore let it escape, turning "your config is
    unreadable" into a traceback out of a governance command whose docstring
    two lines above promises a logged fallback to defaults.
    """
    (tmp_path / ".sigantry.toml").write_bytes(b'tenant_id = "\xff\xfe not utf-8"\n')
    monkeypatch.chdir(tmp_path)
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()

    with caplog.at_level(logging.WARNING, logger="sigantry_core.sync.cli"):
        sync_cli_mod._emit_preview_warning_once()

    load_failures = [
        r.message for r in caplog.records if "Could not load Sigantry settings" in r.message
    ]
    assert load_failures, (
        f"the unreadable config was not reported at all: {[r.message for r in caplog.records]}"
    )
    assert "UnicodeDecodeError" in load_failures[0]
