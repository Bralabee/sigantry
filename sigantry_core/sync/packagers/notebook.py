"""NotebookPackager -- raw ``.ipynb`` -> staged ``.Notebook/`` folder.

Implements SYNC-02 (sigantry sync packager for raw ``.ipynb`` files)
and the D-11 sidecar manifest pattern that keeps ``logicalId`` stable
across machines.

What this packager does
-----------------------

Given a raw ``.ipynb`` file at ``<source>``, the packager:

1. Resolves a UUID4 ``logicalId`` -- either from the caller-supplied
   ``logical_id`` argument (manifest-pinned) or from the sidecar
   manifest at ``<source.parent>/.sigantry/notebook-ids.json``. If the
   sidecar has no entry for ``<source.name>``, a fresh UUID4 is minted
   and written back atomically (D-11 / D-13).
2. Creates ``<staging_dir>/<target_folder>/<display_name>.Notebook/``
   (refuses to overwrite an existing tree -- callers must pass a fresh
   staging tempdir per :class:`Packager` contract).
3. Copies the source bytes into ``notebook-content.ipynb`` with LF
   normalisation (Phase 4 Pitfall 3 invariant: Fabric silently corrupts
   items with CRLF line endings; D-11 extends ``_LF_TARGET_SUFFIXES``
   to cover ``.ipynb``). Also coerces nbformat ``cells[].source`` (and
   output ``text``/``traceback``) from the single-string form to the
   list-of-lines form, which Fabric's NotebookService strictly requires
   (the string form is rejected with ``InvalidNotebookContent``). This
   is a no-op for already-compliant notebooks (byte-identical fast
   path); see :meth:`_normalise_ipynb_multiline`.
4. Writes the schema-2.0 ``.platform`` JSON (Council B verified
   against ``fabric_cicd/fabric_workspace.py:302-322``):
   ``metadata.{type, displayName}`` + ``config.{version: "2.0",
   logicalId}``.

Sidecar manifest format (D-13)
------------------------------

::

    <source-dir>/.sigantry/notebook-ids.json:
    {
      "schema_version": "1.0",
      "ids": {
        "<relative-path-from-source-dir>": "<uuid4>"
      }
    }

Reads accept a missing ``ids`` map (treat as empty) and an unknown
``schema_version`` (forward-compat warning, fall through). Writes
preserve existing entries -- a caller-driven ``force`` flag is reserved
for a future plan but not exposed today.

LF normalisation
----------------

``_LF_TARGET_SUFFIXES = (".platform", ".py", ".json", ".ipynb")`` --
extends the Phase 4 ``sigantry_core.deploy.item_copy`` set with
``.ipynb``. JSON-text content is unaffected (JSON parsers accept
either line ending) but Fabric's repo-walk does not.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger("sigantry_core.sync.packagers.notebook")

_PLATFORM_SCHEMA_URL = (
    "https://developer.microsoft.com/json-schemas/fabric/"
    "gitIntegration/platformProperties/2.0.0/schema.json"
)
_SIDECAR_DIR_NAME = ".sigantry"
_SIDECAR_FILE_NAME = "notebook-ids.json"
_SIDECAR_SCHEMA_VERSION = "1.0"


class NotebookPackager:
    """Pack a raw ``.ipynb`` into a staged ``.Notebook/`` item folder.

    Implements :class:`sigantry_core.sync.protocols.Packager` -- the
    runtime-checkable Protocol seam (D-03). Stateless; the registry
    constructs a single instance which is safe to share across calls.
    """

    #: Suffixes that get LF normalisation on write. Phase 4 set
    #: (``.platform`` / ``.py`` / ``.json``) extended with ``.ipynb``
    #: per D-11.
    _LF_TARGET_SUFFIXES: tuple[str, ...] = (
        ".platform",
        ".py",
        ".json",
        ".ipynb",
    )

    def pack(
        self,
        source: Path,
        *,
        display_name: str,
        target_folder: str,
        logical_id: str | None,
        staging_dir: Path,
    ) -> Path:
        """Pack ``source`` (a raw ``.ipynb``) into a staged folder.

        See :class:`sigantry_core.sync.protocols.Packager` for the
        signature contract. Raises:

        - :class:`ValueError` if ``source`` is not an ``.ipynb`` file.
        - :class:`FileExistsError` if the target staged folder already
          exists (callers must pass a fresh staging tempdir).
        """
        source_path = Path(source).resolve()
        if not source_path.is_file() or source_path.suffix != ".ipynb":
            raise ValueError(
                f"NotebookPackager.pack: source must be an .ipynb file; got {source_path}"
            )
        staging_dir = Path(staging_dir).resolve()

        # Sidecar resolution (D-11 / D-13).
        resolved_id = logical_id if logical_id else self._sidecar_resolve(source_path)

        # Build target dir under staging.
        sub_path = target_folder.lstrip("/").lstrip("\\")
        target_dir = (
            staging_dir / sub_path / f"{display_name}.Notebook"
            if sub_path
            else staging_dir / f"{display_name}.Notebook"
        )
        if target_dir.exists():
            raise FileExistsError(
                "NotebookPackager.pack: refuse to overwrite "
                f"{target_dir} -- caller must place each item in a "
                "fresh staging tree."
            )
        target_dir.mkdir(parents=True, exist_ok=False)

        # notebook-content.ipynb -- copy with LF normalisation.
        ipynb_bytes = source_path.read_bytes()
        ipynb_bytes = self._normalise_lf(ipynb_bytes)
        # Fabric's NotebookService casts `cells[].source` (and output
        # `text`/`traceback`) to ``List<string>`` and rejects the
        # single-string form with ``InvalidNotebookContent`` even though
        # nbformat permits it. Notebooks edited by tooling that emits a
        # string per cell therefore fail to publish. Coerce to the
        # list-of-lines form; a no-op for already-compliant notebooks
        # keeps the byte-identical fast path (D-11 LF invariant intact).
        normalised_obj, changed = self._normalise_ipynb_multiline(ipynb_bytes)
        if changed:
            ipynb_text = json.dumps(normalised_obj, indent=1, ensure_ascii=False) + "\n"
            ipynb_bytes = self._normalise_lf(ipynb_text.encode("utf-8"))
        (target_dir / "notebook-content.ipynb").write_bytes(ipynb_bytes)

        # .platform -- schema 2.0 (Council B).
        platform_payload = {
            "$schema": _PLATFORM_SCHEMA_URL,
            "metadata": {
                "type": "Notebook",
                "displayName": display_name,
            },
            "config": {
                "version": "2.0",
                "logicalId": resolved_id,
            },
        }
        platform_text = json.dumps(platform_payload, indent=2, sort_keys=False) + "\n"
        platform_bytes = self._normalise_lf(platform_text.encode("utf-8"))
        (target_dir / ".platform").write_bytes(platform_bytes)

        return target_dir

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_lf(data: bytes) -> bytes:
        """Collapse CRLF and lone CR to LF (Phase 4 Pitfall 3 invariant)."""
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    @classmethod
    def _normalise_ipynb_multiline(cls, raw: bytes) -> tuple[dict | None, bool]:
        """Coerce nbformat multiline string fields to Fabric's list form.

        nbformat allows ``cells[].source`` (and per-output ``text`` /
        ``traceback``) to be EITHER a single ``str`` OR a ``list[str]`` of
        line-preserving lines. Fabric's NotebookService only accepts the
        list form -- the single-string form is rejected with
        ``InvalidNotebookContent`` ("Failed to cast json string to type
        ... List`1[System.String]`"). We split any offending string on
        line boundaries with ``keepends=True`` so the round-trip is
        loss-less (``"a\\nb"`` -> ``["a\\n", "b"]``).

        Returns ``(notebook_obj, changed)``. ``changed`` is ``False`` --
        and ``notebook_obj`` is ``None`` -- when the bytes are not parseable
        JSON or nothing needed coercion, so the caller can keep the
        byte-identical LF-only fast path for already-compliant notebooks.
        """
        try:
            notebook = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, False
        if not isinstance(notebook, dict):
            return None, False

        changed = False
        for cell in notebook.get("cells", []) or []:
            if not isinstance(cell, dict):
                continue
            if isinstance(cell.get("source"), str):
                cell["source"] = cell["source"].splitlines(keepends=True)
                changed = True
            for output in cell.get("outputs", []) or []:
                if not isinstance(output, dict):
                    continue
                for key in ("text", "traceback"):
                    if isinstance(output.get(key), str):
                        output[key] = output[key].splitlines(keepends=True)
                        changed = True
        return (notebook, True) if changed else (None, False)

    @classmethod
    def _sidecar_resolve(cls, source_path: Path) -> str:
        """Read or mint the ``logicalId`` for ``source_path``; persist on mint.

        Sidecar lives at ``<source_path.parent>/.sigantry/notebook-ids.json``
        (D-11). The key is ``source_path.name`` -- in the Plan 13-03
        surface the manifest validator (Plan 13-01) prevents two
        ``.ipynb`` files with the same basename in the same source-dir,
        so this is unambiguous. Plan 13-07 / v3.x can extend the key to
        a relative-path-from-manifest-root if nested sources are added.
        """
        sidecar_dir = source_path.parent / _SIDECAR_DIR_NAME
        sidecar = sidecar_dir / _SIDECAR_FILE_NAME

        payload: dict = {}
        if sidecar.is_file():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "notebook_sidecar_unparseable path=%s err=%s",
                    sidecar,
                    exc,
                )
                payload = {}

        observed_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if observed_version is not None and observed_version != _SIDECAR_SCHEMA_VERSION:
            logger.warning(
                "notebook_sidecar_unknown_schema_version path=%s observed=%s expected=%s",
                sidecar,
                observed_version,
                _SIDECAR_SCHEMA_VERSION,
            )

        ids: dict[str, str] = payload.get("ids", {}) if isinstance(payload, dict) else {}
        if not isinstance(ids, dict):
            ids = {}
        key = source_path.name
        existing = ids.get(key)
        if existing:
            return existing
        new_id = str(uuid.uuid4())
        ids[key] = new_id
        new_payload = {
            "schema_version": _SIDECAR_SCHEMA_VERSION,
            "ids": ids,
        }
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps(new_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return new_id


__all__ = ("NotebookPackager",)
