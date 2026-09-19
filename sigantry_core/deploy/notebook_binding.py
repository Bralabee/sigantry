"""Set a Fabric Notebook's attached Environment / default Lakehouse.

A Fabric notebook stores its *attached environment* and *default lakehouse*
inside its own ``notebook-content.ipynb`` under ``metadata.dependencies``::

    "metadata": {
      "dependencies": {
        "environment": {"environmentId": "<env-guid>", "workspaceId": "<ws-guid>"},
        "lakehouse": {
          "default_lakehouse": "<lh-guid>",
          "default_lakehouse_name": "<name>",
          "default_lakehouse_workspace_id": "<ws-guid>",
          "known_lakehouses": [{"id": "<lh-guid>"}]
        }
      }
    }

Why this module exists
----------------------

Those bindings are **workspace-specific GUIDs**, so a source-controlled
notebook (correctly) carries none. Any deploy that replaces the notebook
definition -- ``sigantry sync apply --with-publish --republish-existing``,
a raw ``updateDefinition``, git-sync, etc. -- therefore **wipes** the
binding. If the notebook imports a custom-library package at runtime, it
then fails (no environment -> the wheel is not on the session). This module
re-applies the binding **after** such a deploy, keeping the repo GUID-free.

It is item-generic: drive it with ``(workspace_id, item_id, ...)`` for any
notebook in any workspace/project. The complementary durable fix -- having
``sync apply`` *preserve* an existing target binding across republish -- is
tracked separately; until then this is the post-deploy re-apply step.

Design
------

- **Read-modify-write** the notebook definition: ``getDefinition`` (ipynb
  format) -> inject ``metadata.dependencies`` into the
  ``notebook-content.ipynb`` part -> ``updateDefinition`` with the full,
  otherwise-unchanged part set (``.platform`` preserved verbatim, so the
  ``logicalId`` is untouched).
- **Idempotent**: re-running with the same values is a no-op write
  (``changed=False``) -- the function compares the resolved ``dependencies``
  block against what is already there before issuing the update.
- **Additive merge**: only the keys you pass are touched. Passing just an
  environment leaves an existing lakehouse binding intact, and vice versa.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from sigantry_core.client import FabricRestClient

_NOTEBOOK_CONTENT_PART = "notebook-content.ipynb"


class NotebookBindingError(Exception):
    """Raised when a notebook binding update cannot be completed."""


@dataclass(frozen=True, slots=True)
class BindingResult:
    """Outcome of :func:`set_notebook_binding`.

    Fields
    ------
    changed:
        ``True`` if an ``updateDefinition`` was issued; ``False`` if the
        notebook already carried the requested binding (no-op).
    environment:
        The ``environment`` dependency block now on the notebook, or
        ``None`` if no environment binding was requested or present.
    lakehouse:
        The ``lakehouse`` dependency block now on the notebook, or
        ``None`` if none was requested or present.
    """

    changed: bool
    environment: dict[str, str] | None
    lakehouse: dict[str, Any] | None


def _normalise_lf(data: bytes) -> bytes:
    """Collapse CRLF / lone CR to LF (Fabric corrupts CRLF item content)."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _find_content_part(parts: list[dict[str, Any]]) -> dict[str, Any]:
    for part in parts:
        if isinstance(part, dict) and str(part.get("path", "")).endswith(_NOTEBOOK_CONTENT_PART):
            return part
    raise NotebookBindingError(
        f"getDefinition response has no {_NOTEBOOK_CONTENT_PART!r} part "
        f"(is the item a Notebook?). Parts: {[p.get('path') for p in parts]}"
    )


def _build_environment_block(
    environment_id: str | None, environment_workspace_id: str | None
) -> dict[str, str] | None:
    if environment_id is None and environment_workspace_id is None:
        return None
    if environment_id is None or environment_workspace_id is None:
        raise NotebookBindingError(
            "environment binding requires BOTH --environment-id and "
            "--environment-workspace-id (Fabric stores the env's home workspace)."
        )
    return {"environmentId": environment_id, "workspaceId": environment_workspace_id}


def _build_lakehouse_block(
    lakehouse_id: str | None,
    lakehouse_name: str | None,
    lakehouse_workspace_id: str | None,
) -> dict[str, Any] | None:
    if lakehouse_id is None and lakehouse_name is None and lakehouse_workspace_id is None:
        return None
    if lakehouse_id is None or lakehouse_name is None:
        raise NotebookBindingError(
            "lakehouse binding requires at least --lakehouse-id and --lakehouse-name."
        )
    block: dict[str, Any] = {
        "default_lakehouse": lakehouse_id,
        "default_lakehouse_name": lakehouse_name,
        "known_lakehouses": [{"id": lakehouse_id}],
    }
    if lakehouse_workspace_id is not None:
        block["default_lakehouse_workspace_id"] = lakehouse_workspace_id
    return block


def set_notebook_binding(
    client: FabricRestClient,
    *,
    workspace_id: str,
    item_id: str,
    environment_id: str | None = None,
    environment_workspace_id: str | None = None,
    lakehouse_id: str | None = None,
    lakehouse_name: str | None = None,
    lakehouse_workspace_id: str | None = None,
) -> BindingResult:
    """Attach an Environment and/or default Lakehouse to a Fabric notebook.

    Parameters
    ----------
    client:
        An authenticated :class:`FabricRestClient`.
    workspace_id:
        GUID of the workspace that holds the notebook.
    item_id:
        GUID of the notebook item.
    environment_id / environment_workspace_id:
        The Environment to attach and the workspace that owns it (an env can
        be attached cross-workspace). Both or neither.
    lakehouse_id / lakehouse_name / lakehouse_workspace_id:
        The default lakehouse. ``id`` + ``name`` are required to set one;
        ``workspace_id`` is optional (defaults to the notebook's workspace
        in Fabric when omitted).

    Returns
    -------
    BindingResult

    Raises
    ------
    NotebookBindingError
        On invalid argument combinations, a non-notebook item, or a
        malformed ``getDefinition`` response.
    """
    env_block = _build_environment_block(environment_id, environment_workspace_id)
    lh_block = _build_lakehouse_block(lakehouse_id, lakehouse_name, lakehouse_workspace_id)
    if env_block is None and lh_block is None:
        raise NotebookBindingError(
            "Nothing to do: pass an environment (--environment-id + "
            "--environment-workspace-id) and/or a lakehouse (--lakehouse-id + "
            "--lakehouse-name)."
        )

    get_path = f"/v1/workspaces/{workspace_id}/items/{item_id}/getDefinition"
    try:
        body = client.send_lro("POST", get_path, params={"format": "ipynb"})
    except Exception as exc:
        raise NotebookBindingError(
            f"getDefinition failed for workspace={workspace_id} item={item_id}: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise NotebookBindingError(
            f"getDefinition returned non-dict body for item {item_id}: {type(body).__name__}"
        )
    parts = (body.get("definition") or {}).get("parts")
    if not isinstance(parts, list):
        raise NotebookBindingError(
            f"getDefinition response missing 'definition.parts' for item {item_id}."
        )

    content_part = _find_content_part(parts)
    try:
        notebook = json.loads(base64.b64decode(content_part["payload"]))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise NotebookBindingError(
            f"Could not decode {_NOTEBOOK_CONTENT_PART} for item {item_id}: {exc}"
        ) from exc

    metadata = notebook.setdefault("metadata", {})
    deps = dict(metadata.get("dependencies") or {})
    before = json.dumps(deps, sort_keys=True)
    if env_block is not None:
        deps["environment"] = env_block
    if lh_block is not None:
        deps["lakehouse"] = lh_block
    after = json.dumps(deps, sort_keys=True)

    if after == before:
        return BindingResult(
            changed=False,
            environment=deps.get("environment"),
            lakehouse=deps.get("lakehouse"),
        )

    metadata["dependencies"] = deps
    new_payload = _normalise_lf(json.dumps(notebook, ensure_ascii=False).encode("utf-8"))
    content_part["payload"] = base64.b64encode(new_payload).decode("ascii")
    content_part["payloadType"] = "InlineBase64"

    # ``format: "ipynb"`` MUST be inside the definition body. We fetched the
    # parts with ``?format=ipynb`` (so notebook-content is the .ipynb part);
    # without echoing the format here the service defaults to the ``.py``
    # source path and the async operation fails with ``PyToIPynbFailure``
    # ("file suffix type .ipynb is not supported") even though the POST is
    # accepted (202). Mirrors fabric-cicd's ``api_format`` handling.
    update_path = f"/v1/workspaces/{workspace_id}/items/{item_id}/updateDefinition"
    try:
        client.send_lro(
            "POST", update_path, json={"definition": {"format": "ipynb", "parts": parts}}
        )
    except Exception as exc:
        raise NotebookBindingError(
            f"updateDefinition failed for workspace={workspace_id} item={item_id}: {exc}"
        ) from exc

    return BindingResult(
        changed=True,
        environment=deps.get("environment"),
        lakehouse=deps.get("lakehouse"),
    )


__all__ = ("BindingResult", "NotebookBindingError", "set_notebook_binding")
