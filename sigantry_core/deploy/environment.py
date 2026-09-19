"""Fabric Environment wheel upload + publish (Pitfall 6 primitive).

Ships the generic mechanism for Python wheel distribution without
``sys.path.append`` hacks. Consumer plugins (e.g. deploy profiles registered
under ``sigantry.deploy_profiles``; the legacy
``fabric_dataops_toolkits.deploy_profiles`` group is dual-read during v3.0) compose this with
``deploy_workspace`` + ``emit_telemetry``; the base layer ships only the
primitive.

MEDIUM confidence on the multipart form-field name — the Microsoft Learn
page for ``POST /v1/workspaces/{ws}/environments/{env}/staging/libraries``
404'd during research. Plan 04-03 Task 3 Step 1 runs a ``curl -v`` probe
against the test tenant to confirm the field; when the test tenant is
unavailable (tenant blocker) the probe is deferred and the constant
:data:`_MULTIPART_FIELD_NAME` defaults to ``"file"`` (best-guess HTTP
convention). The live integration scaffold's docstring explicitly names
the fix location if the probe later reveals a different name.

Size cap: 300 MB per library file (Fabric Environment Anti-Pattern);
exceeding it triggers :exc:`WheelTooLargeError` before any HTTP call.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sigantry_core.client import FabricRestClient
from sigantry_core.client.errors import NotFoundError

_MULTIPART_FIELD_NAME = "file"
"""HTTP multipart form-field name for the wheel upload.

Probe-deferred default: ``"file"`` is the most common convention. If the
live integration test surfaces a validation error naming a different field
(e.g. ``"library"``, ``"wheel"``), change this constant — it is the
single source of truth for the upload call site.
"""

_MAX_WHEEL_BYTES = 300 * 1024 * 1024
"""300 MB Fabric Environment per-library cap (see Anti-Patterns)."""

# Terminal states of a Fabric Environment publish, read from
# ``properties.publishDetails.state`` on the environment item. Note Fabric
# uses ``"Success"`` here (NOT the LRO spec's ``"Succeeded"``); we accept both
# defensively. Confirmed live 2026-06-13: a wheel publish to a real sandbox
# Environment took ~6 minutes and the state went ``Running -> Success``.
_PUBLISH_OK_STATES = frozenset({"Success", "Succeeded"})
_PUBLISH_FAIL_STATES = frozenset({"Failed", "Cancelled", "Undetermined"})

_DEFAULT_PUBLISH_TIMEOUT = 1800.0
"""Wall-clock cap (seconds) for the environment build. Spark image rebuilds
routinely take several minutes; 30 min leaves generous headroom."""

_DEFAULT_PUBLISH_POLL_INTERVAL = 15.0
"""Seconds between publish-state polls."""


class WheelTooLargeError(ValueError):
    """Wheel exceeds Fabric Environment 300 MB per-library cap."""


class WheelHashMismatchError(ValueError):
    """Optional ``expected_sha256`` digest check failed."""


class WheelPublishFailedError(RuntimeError):
    """The Fabric Environment publish reached a terminal Failed state."""


class WheelPublishTimeoutError(TimeoutError):
    """The Fabric Environment publish did not finish within the timeout."""


@dataclass(frozen=True, slots=True)
class WheelUploadResult:
    """Outcome snapshot returned by :func:`sync_wheel`."""

    workspace_id: str
    environment_id: str
    wheel_name: str
    staging_upload_status: str
    publish_lro_status: str
    installed_library_name: str | None


def sync_wheel(
    client: FabricRestClient,
    workspace_id: str,
    environment_id: str,
    wheel_path: str | Path,
    *,
    expected_sha256: str | None = None,
    publish_timeout: float = _DEFAULT_PUBLISH_TIMEOUT,
    publish_poll_interval: float = _DEFAULT_PUBLISH_POLL_INTERVAL,
) -> WheelUploadResult:
    """Upload + publish a wheel to a Fabric Environment (Pitfall 6).

    Four-step sequence:
        1. POST multipart ``.../staging/libraries`` — uploads the bytes.
        2. POST ``.../staging/publish`` — *triggers* the environment build.
        3. Poll the environment item's ``publishDetails.state`` until it
           leaves ``Running`` (the build is async; the publish POST returns
           200 immediately and is NOT a pollable LRO — progress lives on the
           item, not an operation URL).
        4. GET ``.../libraries`` — verify the wheel is a *published* library.

    Why this differs from a naive ``send_lro`` call: ``/staging/publish``
    returns ``200`` synchronously, so ``send_lro`` would treat it as terminal
    and return ~2s in while the Spark image is still rebuilding (~6 min
    observed). A CI step that trusted that signal could run a notebook before
    its wheel was importable. This function blocks until the build genuinely
    finishes (or fails / times out).

    Args:
        client: a :class:`sigantry_core.client.FabricRestClient`.
        workspace_id: Fabric workspace id.
        environment_id: Fabric Environment id (must already exist).
        wheel_path: path to the ``.whl`` artefact on disk.
        expected_sha256: optional SHA-256 hex digest to validate the bytes
            before upload. Mismatch raises :exc:`WheelHashMismatchError`
            BEFORE any HTTP call.
        publish_timeout: wall-clock cap (seconds) for the environment build.
        publish_poll_interval: seconds between publish-state polls.

    Raises:
        WheelTooLargeError: wheel exceeds the 300 MB cap.
        WheelHashMismatchError: ``expected_sha256`` did not match.
        WheelPublishFailedError: the environment publish ended in a terminal
            failed state.
        WheelPublishTimeoutError: the publish did not finish within
            ``publish_timeout``.

    Returns:
        :class:`WheelUploadResult` populated from the observed responses.
        ``installed_library_name`` is the wheel filename when confirmed
        present in the published library set, else ``None``.
    """
    path = Path(wheel_path)
    data = path.read_bytes()

    if len(data) > _MAX_WHEEL_BYTES:
        raise WheelTooLargeError(
            f"{path.name} is {len(data)} bytes (>300 MB Fabric Environment cap). "
            "Split into core + extras envs or move heavy deps to cluster init scripts."
        )

    if expected_sha256 is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise WheelHashMismatchError(
                f"{path.name}: expected sha256={expected_sha256}, actual={actual}"
            )

    upload_resp = client.send_multipart(
        "POST",
        f"/v1/workspaces/{workspace_id}/environments/{environment_id}/staging/libraries",
        files={
            _MULTIPART_FIELD_NAME: (
                path.name,
                data,
                "application/octet-stream",
            ),
        },
    )
    upload_body = upload_resp.json_body if isinstance(upload_resp.json_body, dict) else {}
    upload_status = upload_body.get("status", "Uploaded")

    # Trigger the build. This returns 200 synchronously; it does NOT wait.
    client.send(
        "POST",
        f"/v1/workspaces/{workspace_id}/environments/{environment_id}/staging/publish",
    )

    # Block until the environment item reports a terminal publish state.
    publish_status = _await_publish(
        client,
        workspace_id,
        environment_id,
        timeout=publish_timeout,
        poll_interval=publish_poll_interval,
    )

    # Verify against the PUBLISHED libraries (the post-build, importable set),
    # not /staging/libraries (which lists staged-but-maybe-unbuilt files). The
    # published endpoint 404s only while zero libraries are published; after a
    # successful wheel build it returns the wheel under customLibraries.
    installed = _read_published_library(client, workspace_id, environment_id, path.name)

    return WheelUploadResult(
        workspace_id=workspace_id,
        environment_id=environment_id,
        wheel_name=path.name,
        staging_upload_status=upload_status,
        publish_lro_status=publish_status,
        installed_library_name=installed,
    )


def _wheel_pkg_name(filename: str) -> str:
    """Normalised distribution name from a wheel filename.

    ``fabric_data_quality-2.2.0-py3-none-any.whl`` -> ``fabric-data-quality``.
    The name is everything before the first ``-`` segment that starts with a
    digit (the version), lowercased with underscores normalised to hyphens so
    ``2.1.2`` and ``2.2.0`` of the same package compare equal.
    """
    base = filename[:-4] if filename.endswith(".whl") else filename
    parts: list[str] = []
    for seg in base.split("-"):
        if seg and seg[0].isdigit():
            break
        parts.append(seg)
    return "-".join(parts).lower().replace("_", "-")


def _list_staging_wheels(
    client: FabricRestClient, workspace_id: str, environment_id: str
) -> list[str]:
    """Wheel filenames currently in the Environment's *staging* library set."""
    try:
        resp = client.send(
            "GET",
            f"/v1/workspaces/{workspace_id}/environments/{environment_id}/staging/libraries",
        )
    except NotFoundError:
        return []
    body = resp.json_body if isinstance(resp.json_body, dict) else {}
    return list((body.get("customLibraries") or {}).get("wheelFiles") or [])


def _list_published_wheels(
    client: FabricRestClient, workspace_id: str, environment_id: str
) -> list[str]:
    """Wheel filenames currently in the Environment's *published* library set."""
    try:
        resp = client.send(
            "GET",
            f"/v1/workspaces/{workspace_id}/environments/{environment_id}/libraries",
        )
    except NotFoundError:
        return []
    body = resp.json_body if isinstance(resp.json_body, dict) else {}
    return list((body.get("customLibraries") or {}).get("wheelFiles") or [])


@dataclass
class ReconcileResult:
    """Outcome of :func:`reconcile_wheels`."""

    workspace_id: str
    environment_id: str
    desired: list[str]  # desired wheel filenames
    deleted: list[str]  # stale wheels removed from staging
    uploaded: list[str]  # wheels uploaded to staging
    published: list[str]  # published wheel set after reconcile
    publish_state: str | None  # terminal publish state, or None if no-op/dry-run
    dry_run: bool


def reconcile_wheels(
    client: FabricRestClient,
    workspace_id: str,
    environment_id: str,
    wheel_paths: list[str | Path],
    *,
    dry_run: bool = False,
    publish_timeout: float = _DEFAULT_PUBLISH_TIMEOUT,
    publish_poll_interval: float = _DEFAULT_PUBLISH_POLL_INTERVAL,
) -> ReconcileResult:
    """Reconcile an Environment's custom libraries to the desired wheel set.

    Solves the *add-only* limitation of :func:`sync_wheel`: for every package in
    ``wheel_paths`` it removes any superseded version of *that* package from
    staging, uploads the desired wheel if missing, triggers a SINGLE publish, and
    blocks until the build reaches a terminal state — then verifies the published
    set. Packages not named are left untouched, so it scales to any number of
    custom libraries. Idempotent: a no-op when staging already matches.

    Args:
        client: a :class:`sigantry_core.client.FabricRestClient`.
        workspace_id: Fabric workspace id.
        environment_id: Fabric Environment id (must already exist).
        wheel_paths: paths to the desired ``.whl`` files (one per package).
        dry_run: plan only; perform no mutation and no publish.
        publish_timeout: wall-clock cap (seconds) for the environment build.
        publish_poll_interval: seconds between publish-state polls.

    Raises:
        WheelTooLargeError: a wheel exceeds the 300 MB cap.
        WheelPublishFailedError / WheelPublishTimeoutError: as in :func:`sync_wheel`.

    Returns:
        :class:`ReconcileResult` describing what changed and the published set.
    """
    paths = [Path(p) for p in wheel_paths]
    for p in paths:
        size = p.stat().st_size
        if size > _MAX_WHEEL_BYTES:
            raise WheelTooLargeError(f"{p.name} is {size} bytes (>300 MB Fabric Environment cap).")

    desired = {p.name: p for p in paths}  # filename -> path
    desired_pkgs = {_wheel_pkg_name(n): n for n in desired}  # pkg -> desired filename

    staging = _list_staging_wheels(client, workspace_id, environment_id)
    to_delete = [w for w in staging if _wheel_pkg_name(w) in desired_pkgs and w not in desired]
    to_upload = [p for name, p in desired.items() if name not in staging]

    if dry_run:
        return ReconcileResult(
            workspace_id=workspace_id,
            environment_id=environment_id,
            desired=list(desired),
            deleted=to_delete,
            uploaded=[p.name for p in to_upload],
            published=_list_published_wheels(client, workspace_id, environment_id),
            publish_state=None,
            dry_run=True,
        )

    if not to_delete and not to_upload:
        published_now = _list_published_wheels(client, workspace_id, environment_id)
        if all(name in published_now for name in desired):
            # Genuinely converged: staging already matches desired AND every
            # desired wheel is in the published set. Safe no-op.
            return ReconcileResult(
                workspace_id=workspace_id,
                environment_id=environment_id,
                desired=list(desired),
                deleted=[],
                uploaded=[],
                published=published_now,
                publish_state=None,
                dry_run=False,
            )
        # Staging matches desired, but the PUBLISHED set is missing a desired
        # wheel -- the tell-tale of a prior run that uploaded/deleted and then
        # had its publish interrupted (Ctrl-C, timeout). The natural retry must
        # NOT read this as a no-op: staging alone would compare equal and exit 0
        # while the Spark env keeps serving the stale package. Fall through to
        # trigger the publish (the delete/upload loops below are empty here) and
        # block until it converges.

    for fn in to_delete:
        client.send(
            "DELETE",
            f"/v1/workspaces/{workspace_id}/environments/{environment_id}/staging/libraries",
            params={"libraryToDelete": fn},
        )
    for p in to_upload:
        client.send_multipart(
            "POST",
            f"/v1/workspaces/{workspace_id}/environments/{environment_id}/staging/libraries",
            files={_MULTIPART_FIELD_NAME: (p.name, p.read_bytes(), "application/octet-stream")},
        )

    client.send(
        "POST",
        f"/v1/workspaces/{workspace_id}/environments/{environment_id}/staging/publish",
    )
    publish_state = _await_publish(
        client,
        workspace_id,
        environment_id,
        timeout=publish_timeout,
        poll_interval=publish_poll_interval,
    )

    return ReconcileResult(
        workspace_id=workspace_id,
        environment_id=environment_id,
        desired=list(desired),
        deleted=to_delete,
        uploaded=[p.name for p in to_upload],
        published=_list_published_wheels(client, workspace_id, environment_id),
        publish_state=publish_state,
        dry_run=False,
    )


def _await_publish(
    client: FabricRestClient,
    workspace_id: str,
    environment_id: str,
    *,
    timeout: float,
    poll_interval: float,
) -> str:
    """Poll ``publishDetails.state`` until terminal; return the terminal state.

    Raises :exc:`WheelPublishFailedError` on a terminal failed state and
    :exc:`WheelPublishTimeoutError` if ``timeout`` elapses first. Non-terminal
    states (``Running``, ``Waiting``, ``Cancelling``, missing) are polled again.
    """
    started = time.monotonic()
    last_state = "Unknown"
    while True:
        resp = client.send(
            "GET",
            f"/v1/workspaces/{workspace_id}/environments/{environment_id}",
        )
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        publish_details = (body.get("properties") or {}).get("publishDetails") or {}
        last_state = publish_details.get("state", "Unknown")

        if last_state in _PUBLISH_OK_STATES:
            return last_state
        if last_state in _PUBLISH_FAIL_STATES:
            raise WheelPublishFailedError(
                f"environment {environment_id} publish ended in state "
                f"{last_state!r}: {publish_details!r}"
            )
        if time.monotonic() - started > timeout:
            raise WheelPublishTimeoutError(
                f"environment {environment_id} publish did not finish within "
                f"{timeout:.0f}s (last state={last_state!r})"
            )
        time.sleep(poll_interval)


def is_wheel_published(
    client: FabricRestClient,
    workspace_id: str,
    environment_id: str,
    wheel_name: str,
) -> bool:
    """True if a wheel of this exact filename is already in the env's published set.

    Used by ``env sync-all`` to skip the ~minutes-long republish when the target
    Environment already carries the wheel (idempotency / no needless rebuilds).
    """
    return _read_published_library(client, workspace_id, environment_id, wheel_name) is not None


def _read_published_library(
    client: FabricRestClient,
    workspace_id: str,
    environment_id: str,
    wheel_name: str,
) -> str | None:
    """Return the wheel filename if present in the published library set, else None."""
    try:
        libs_resp = client.send(
            "GET",
            f"/v1/workspaces/{workspace_id}/environments/{environment_id}/libraries",
        )
    except NotFoundError:
        # No published libraries at all — unexpected after a successful publish,
        # but report it honestly as "not confirmed" rather than crashing.
        return None
    return _find_installed(libs_resp.json_body, wheel_name)


def _find_installed(body: Any, wheel_name: str) -> str | None:
    """Look up the wheel in a Fabric Environment libraries response.

    The real Fabric schema (confirmed live 2026-06-13) is::

        {"customLibraries": {"wheelFiles": ["pkg-1.0-py3-none-any.whl"],
                             "pyFiles": [], "jarFiles": [], "rTarFiles": []},
         "environmentYml": "..."}

    i.e. ``customLibraries`` is a dict of typed filename-string lists, and the
    wheel is listed by its FULL filename. A defensive fallback also tolerates a
    hypothetical list-of-``{"name": ...}`` shape (matched by distribution name).
    Returns the matched wheel filename, or ``None`` if not found.
    """
    if not isinstance(body, dict):
        return None

    custom = body.get("customLibraries")
    if isinstance(custom, dict):
        wheel_files = custom.get("wheelFiles") or []
        if isinstance(wheel_files, list) and wheel_name in wheel_files:
            return wheel_name
        return None

    # Defensive fallback: older/alternative list-of-dicts shape.
    distribution = wheel_name.removesuffix(".whl")
    for key in ("customLibraries", "custom_libraries", "libraries"):
        libs = body.get(key) or []
        if isinstance(libs, list):
            for lib in libs:
                if isinstance(lib, dict) and lib.get("name") in (distribution, wheel_name):
                    return lib.get("name")
    return None
