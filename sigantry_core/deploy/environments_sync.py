"""Fan-out wheel sync across many Fabric Environments from an ``environments.yml``.

This is the orchestration layer over the single-target :func:`sync_wheel`
primitive. It exists so the *risk controls* of fleet deployment live in tested
code rather than in copy-pasted pipeline YAML:

* **gating** — targets marked ``gated`` (e.g. PROD) are skipped unless the caller
  explicitly opts in, so an automatic on-release trigger can keep DEV/SANDBOX
  current without ever touching production unattended;
* **pin vs float** — pin targets refuse glob/"latest" wheels (enforced at manifest
  load); here a pin target additionally fails loudly if a listed wheel is missing;
* **fail-isolation** — one Environment failing (publish error, missing wheel) does
  not abort the rest; every outcome is recorded and the run reports non-zero only
  at the end (unless ``fail_fast``);
* **idempotency** — a wheel already published to a target is skipped (no needless
  ~minutes-long Spark image rebuild) unless ``force``;
* **dry-run** — resolve + plan with zero tenant calls, so the plan can be reviewed
  before anything mutates a live workspace.
"""

from __future__ import annotations

import glob
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sigantry_core.client import FabricRestClient
from sigantry_core.deploy.environment import (
    WheelUploadResult,
    is_wheel_published,
    sync_wheel,
)
from sigantry_core.deploy.environments_manifest import EnvironmentsManifest, EnvTarget

# Action vocabularies (stable strings — they appear in the JSON summary).
WHEEL_SYNCED = "synced"
WHEEL_SKIPPED = "skipped-unchanged"
WHEEL_DRY_RUN = "dry-run"
WHEEL_FAILED = "failed"

TARGET_PROCESSED = "processed"
TARGET_GATED = "gated-skipped"
TARGET_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WheelOutcome:
    """Per-wheel result within a target."""

    wheel: str
    action: str
    installed_library_name: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "wheel": self.wheel,
            "action": self.action,
            "installedLibraryName": self.installed_library_name,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class TargetOutcome:
    """Result for one Environment target."""

    name: str
    workspace_id: str
    environment_id: str
    action: str
    wheels: tuple[WheelOutcome, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        if self.action == TARGET_FAILED:
            return False
        return all(w.action != WHEEL_FAILED for w in self.wheels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workspaceId": self.workspace_id,
            "environmentId": self.environment_id,
            "action": self.action,
            "wheels": [w.to_dict() for w in self.wheels],
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class EnvSyncReport:
    """Aggregate outcome of an ``env sync-all`` run."""

    targets: tuple[TargetOutcome, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.targets)

    def to_dict(self) -> dict[str, Any]:
        synced = sum(1 for t in self.targets for w in t.wheels if w.action == WHEEL_SYNCED)
        skipped = sum(1 for t in self.targets for w in t.wheels if w.action == WHEEL_SKIPPED)
        failed = sum(1 for t in self.targets for w in t.wheels if w.action == WHEEL_FAILED)
        gated = sum(1 for t in self.targets if t.action == TARGET_GATED)
        return {
            "ok": self.ok,
            "summary": {
                "targets": len(self.targets),
                "wheelsSynced": synced,
                "wheelsSkipped": skipped,
                "wheelsFailed": failed,
                "targetsGatedSkipped": gated,
            },
            "targets": [t.to_dict() for t in self.targets],
        }


def _resolve_wheels(target: EnvTarget) -> list[Path]:
    """Resolve a target's wheel patterns to concrete ``.whl`` files (deduped by name).

    Raises ``ValueError`` (recorded as a target failure) when a pattern matches no
    file, a resolved path is missing or is not a wheel. Pin targets reject globs at
    manifest-load time; this is the runtime existence check.
    """
    resolved: dict[str, Path] = {}
    for pattern in target.wheels:
        matches = sorted(glob.glob(pattern))  # glob handles absolute + relative patterns
        if not matches:
            raise ValueError(f"no wheel matched {pattern!r}")
        for match in matches:
            path = Path(match)
            if not path.is_file():
                raise ValueError(f"wheel not found: {path}")
            if path.suffix != ".whl":
                raise ValueError(f"not a wheel: {path}")
            resolved[path.name] = path
    return [resolved[name] for name in sorted(resolved)]


def sync_environments(
    client: FabricRestClient | None,
    manifest: EnvironmentsManifest,
    *,
    include_gated: bool = False,
    force: bool = False,
    dry_run: bool = False,
    fail_fast: bool = False,
    sync_wheel_fn: Callable[..., WheelUploadResult] = sync_wheel,
    is_published_fn: Callable[[FabricRestClient, str, str, str], bool] = is_wheel_published,
) -> EnvSyncReport:
    """Sync wheels to every (non-gated, unless ``include_gated``) target.

    ``client`` may be ``None`` only when ``dry_run`` is True (no tenant calls).
    ``sync_wheel_fn`` / ``is_published_fn`` are injectable for testing.
    """
    if client is None and not dry_run:
        raise ValueError("client is required unless dry_run=True")

    outcomes: list[TargetOutcome] = []
    for target in manifest.targets:
        if target.gated and not include_gated:
            outcomes.append(
                TargetOutcome(
                    name=target.name,
                    workspace_id=target.workspace_id,
                    environment_id=target.environment_id,
                    action=TARGET_GATED,
                )
            )
            continue

        try:
            wheel_paths = _resolve_wheels(target)
        except ValueError as exc:
            outcomes.append(
                TargetOutcome(
                    name=target.name,
                    workspace_id=target.workspace_id,
                    environment_id=target.environment_id,
                    action=TARGET_FAILED,
                    error=str(exc),
                )
            )
            if fail_fast:
                break
            continue

        wheel_outcomes: list[WheelOutcome] = []
        for path in wheel_paths:
            name = path.name
            if dry_run:
                wheel_outcomes.append(WheelOutcome(wheel=name, action=WHEEL_DRY_RUN))
                continue
            assert client is not None  # guaranteed by the dry_run guard above
            if not force and is_published_fn(
                client, target.workspace_id, target.environment_id, name
            ):
                wheel_outcomes.append(
                    WheelOutcome(
                        wheel=name,
                        action=WHEEL_SKIPPED,
                        installed_library_name=name,
                    )
                )
                continue
            try:
                result = sync_wheel_fn(
                    client, target.workspace_id, target.environment_id, str(path)
                )
                wheel_outcomes.append(
                    WheelOutcome(
                        wheel=name,
                        action=WHEEL_SYNCED,
                        installed_library_name=result.installed_library_name,
                    )
                )
            except Exception as exc:  # isolate one wheel's failure; record + continue
                wheel_outcomes.append(WheelOutcome(wheel=name, action=WHEEL_FAILED, error=str(exc)))
                if fail_fast:
                    break

        target_failed = any(w.action == WHEEL_FAILED for w in wheel_outcomes)
        outcomes.append(
            TargetOutcome(
                name=target.name,
                workspace_id=target.workspace_id,
                environment_id=target.environment_id,
                action=TARGET_FAILED if target_failed else TARGET_PROCESSED,
                wheels=tuple(wheel_outcomes),
            )
        )
        if fail_fast and target_failed:
            break

    return EnvSyncReport(targets=tuple(outcomes))
