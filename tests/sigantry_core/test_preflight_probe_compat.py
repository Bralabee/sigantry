"""A custom probe written for 1.0.1 still runs under the 1.1.0 engine.

1.0.1's engine called ``probe.run(manifest_path=..., environment=...,
params_path=..., client=...)``. 1.1.0 adds ``token_provider``,
``workspace_id`` and ``tenant_id``; passing them to a ``run`` that does not
declare them would raise ``TypeError`` and record the probe as FAIL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from sigantry_core.preflight.engine import PreflightEngine
from sigantry_core.preflight.models import ProbeResult, ProbeStatus
from sigantry_core.preflight.probes import BaseProbe


class _ProbeFrom101(BaseProbe):
    """The signature a 1.0.1 custom probe had."""

    name = "from-1.0.1"

    def run(  # type: ignore[override]
        self,
        *,
        manifest_path: Path,
        environment: str,
        params_path: Path | None = None,
        client: Any = None,
    ) -> ProbeResult:
        return ProbeResult(name=self.name, status=ProbeStatus.PASS, message=environment)


class _ProbeWithKwargs(BaseProbe):
    name = "kwargs"
    seen: ClassVar[dict[str, Any]] = {}

    def run(self, **kwargs: Any) -> ProbeResult:  # type: ignore[override]
        type(self).seen = dict(kwargs)
        return ProbeResult(name=self.name, status=ProbeStatus.PASS, message="ok")


class _ProbeNamingSome(BaseProbe):
    name = "some"
    seen: ClassVar[dict[str, Any]] = {}

    def run(  # type: ignore[override]
        self, *, manifest_path: Path, environment: str, tenant_id: str | None = None
    ) -> ProbeResult:
        type(self).seen = {"tenant_id": tenant_id}
        return ProbeResult(name=self.name, status=ProbeStatus.PASS, message="ok")


def _run(probe: BaseProbe, tmp_path: Path) -> Any:
    return PreflightEngine(probes=[probe]).run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        token_provider=object(),
        workspace_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
    )


def test_a_probe_written_for_1_0_1_runs(tmp_path: Path) -> None:
    report = _run(_ProbeFrom101(), tmp_path)

    assert [(r.name, r.status, r.message) for r in report.results] == [
        ("from-1.0.1", ProbeStatus.PASS, "prod")
    ]
    assert report.passed


def test_a_probe_taking_kwargs_gets_every_argument(tmp_path: Path) -> None:
    _run(_ProbeWithKwargs(), tmp_path)

    assert set(_ProbeWithKwargs.seen) == {
        "manifest_path",
        "environment",
        "params_path",
        "client",
        "token_provider",
        "workspace_id",
        "tenant_id",
    }


def test_a_probe_gets_each_new_argument_it_declares(tmp_path: Path) -> None:
    report = _run(_ProbeNamingSome(), tmp_path)

    assert report.passed
    assert _ProbeNamingSome.seen == {"tenant_id": "22222222-2222-2222-2222-222222222222"}


def test_schema_syntax_probe_forwards_environment_to_parameters_validator(
    tmp_path: Path,
) -> None:
    """SIG-005 / preflight-params-probe-env-discard: SchemaSyntaxProbe forwards environment
    into load_and_validate so environment-scoped $ENV: validation and UnknownEnvironmentError work."""
    from sigantry_core.preflight.probes import ProbeStatus, SchemaSyntaxProbe

    manifest = tmp_path / "sync.yml"
    manifest.write_text(
        "schema_version: '1.0'\nitems: []\n",
        encoding="utf-8",
    )
    params = tmp_path / "parameters.yml"
    params.write_text(
        "find_replace:\n"
        "  - find_value: 'DEV_ENDPOINT'\n"
        "    replace_value:\n"
        "      DEV: '$ENV:SIGANTRY_TEST_DEV_WS_ID'\n"
        "      PROD: '$ENV:SIGANTRY_TEST_UNSET_PROD_WS_ID'\n",
        encoding="utf-8",
    )
    import os

    os.environ["SIGANTRY_TEST_DEV_WS_ID"] = "22222222-2222-2222-2222-222222222222"
    os.environ.pop("SIGANTRY_TEST_UNSET_PROD_WS_ID", None)
    try:
        probe = SchemaSyntaxProbe()
        # 1. Validating DEV should PASS even when PROD's $ENV: var is unset
        res_dev = probe.run(manifest_path=manifest, environment="DEV", params_path=params)
        assert res_dev.status == ProbeStatus.PASS, res_dev.message

        # 2. Validating an undeclared environment STAGING should FAIL
        res_unknown = probe.run(manifest_path=manifest, environment="STAGING", params_path=params)
        assert res_unknown.status == ProbeStatus.FAIL
    finally:
        os.environ.pop("SIGANTRY_TEST_DEV_WS_ID", None)
