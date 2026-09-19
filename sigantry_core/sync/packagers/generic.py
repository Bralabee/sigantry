"""GenericPackager -- copy-as-platform for arbitrary item types.

Implements SYNC-03 (uniform packager seam for non-Notebook item types
covered by a stock ``.platform.j2`` Jinja2 template) and the D-12
sidecar pattern (one sidecar file per item type to avoid lock
contention if multiple packagers run in parallel).

What this packager does
-----------------------

Given a source path (file OR directory) at ``<source>``, the packager:

1. Resolves a UUID4 ``logicalId`` -- either from the caller-supplied
   ``logical_id`` argument (manifest-pinned) or from the per-type
   sidecar at
   ``<source.parent>/.sigantry/<type-lowercase>-ids.json`` (D-12 /
   D-13).
2. Creates ``<staging_dir>/<target_folder>/<display_name>.<Type>/``
   (refuses to overwrite an existing tree).
3. Copies the source into the staged folder. Files are copied with LF
   normalisation on the union of Notebook's
   ``_LF_TARGET_SUFFIXES`` plus ``.tmdl`` and ``.bim`` (the SemanticModel
   text-format files); directories are walked recursively.
4. Renders the ``<item_type>.platform.j2`` Jinja2 template with
   ``display_name`` + the resolved ``logical_id`` and writes
   ``.platform`` into the staged folder root.

Why per-type instances
----------------------

The :data:`PACKAGER_REGISTRY
<sigantry_core.sync.packagers.PACKAGER_REGISTRY>` constructs one
``GenericPackager`` instance per item type so the operator-facing
registry is uniform: ``PACKAGER_REGISTRY[item_type].pack(...)`` for
every type. The packager's only state is ``self.item_type`` (used to
resolve the template and the sidecar filename); instances are safe to
share across calls.

Lakehouse / Warehouse / SQLDatabase / MLExperiment are explicitly
out-of-scope for v3.0 -- a manifest item referencing them raises
``KeyError`` at registry-resolution time in Plan 13-04.
"""

from __future__ import annotations

import json
import logging
import uuid
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

logger = logging.getLogger("sigantry_core.sync.packagers.generic")

_SIDECAR_DIR_NAME = ".sigantry"
_SIDECAR_SCHEMA_VERSION = "1.0"

#: Suffixes that get LF normalisation on write. Notebook's set
#: (``.platform`` / ``.py`` / ``.json`` / ``.ipynb``) extended with the
#: SemanticModel text-format files (``.tmdl`` / ``.bim``).
_LF_TARGET_SUFFIXES: tuple[str, ...] = (
    ".platform",
    ".py",
    ".json",
    ".ipynb",
    ".tmdl",
    ".bim",
)


class GenericPackager:
    """Pack a directory or file into a staged ``<displayName>.<Type>/`` folder.

    Implements :class:`sigantry_core.sync.protocols.Packager` for any
    item type that has a stock ``<Type>.platform.j2`` Jinja2 template
    under :mod:`sigantry_core.sync.templates`. The PACKAGER_REGISTRY
    constructs one instance per type (DataPipeline, SemanticModel,
    Report, SparkJobDefinition).
    """

    def __init__(self, item_type: str) -> None:
        self.item_type = item_type
        self._template_name = f"{item_type}.platform.j2"

    def pack(
        self,
        source: Path,
        *,
        display_name: str,
        target_folder: str,
        logical_id: str | None,
        staging_dir: Path,
    ) -> Path:
        """Pack ``source`` into a staged ``<displayName>.<Type>/`` folder.

        See :class:`sigantry_core.sync.protocols.Packager` for the
        signature contract. Raises:

        - :class:`FileNotFoundError` if ``source`` does not exist.
        - :class:`FileExistsError` if the target staged folder already
          exists.
        - :class:`jinja2.TemplateNotFound` (propagated) if no
          ``<item_type>.platform.j2`` ships for the configured type.
        """
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"GenericPackager.pack: source missing: {source_path}")
        staging_dir = Path(staging_dir).resolve()

        resolved_id = logical_id if logical_id else self._sidecar_resolve(source_path)

        sub_path = target_folder.lstrip("/").lstrip("\\")
        target_dir = (
            staging_dir / sub_path / f"{display_name}.{self.item_type}"
            if sub_path
            else staging_dir / f"{display_name}.{self.item_type}"
        )
        if target_dir.exists():
            raise FileExistsError(f"GenericPackager.pack: refuse to overwrite {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=False)

        # Copy source contents into target_dir (file OR directory).
        if source_path.is_file():
            self._copy_file_with_lf(source_path, target_dir / source_path.name)
        else:
            self._copy_tree_with_lf(source_path, target_dir)

        # Render and write .platform from the Jinja2 stock template.
        platform_text = self._render_template(display_name=display_name, logical_id=resolved_id)
        (target_dir / ".platform").write_bytes(self._normalise_lf(platform_text.encode("utf-8")))

        return target_dir

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_lf(data: bytes) -> bytes:
        """Collapse CRLF and lone CR to LF (Phase 4 Pitfall 3 invariant)."""
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    @classmethod
    def _copy_file_with_lf(cls, src: Path, dst: Path) -> None:
        """Copy ``src`` to ``dst``, normalising LF on text-suffix files."""
        data = src.read_bytes()
        if src.suffix in _LF_TARGET_SUFFIXES:
            data = cls._normalise_lf(data)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)

    @classmethod
    def _copy_tree_with_lf(cls, src_dir: Path, dst_dir: Path) -> None:
        """Walk ``src_dir`` recursively, copying every file via _copy_file_with_lf.

        The ``.sigantry/`` sidecar subdirectory is skipped -- it carries
        operator-side identity state, not item content, and propagating
        it to the staging tree would confuse fabric-cicd's repo walk.
        """
        for entry in src_dir.rglob("*"):
            if entry.is_dir():
                continue
            rel = entry.relative_to(src_dir)
            if rel.parts and rel.parts[0] == _SIDECAR_DIR_NAME:
                continue
            cls._copy_file_with_lf(entry, dst_dir / rel)

    def _render_template(self, *, display_name: str, logical_id: str) -> str:
        """Render ``<item_type>.platform.j2`` with the supplied substitutions.

        Uses ``StrictUndefined`` so a typo in the template variable
        names fails loudly. ``select_autoescape`` with ``.j2`` disabled
        because the templates emit JSON, not HTML; defence-in-depth is
        the manifest validator (Plan 13-01) which already rejected
        banned characters in ``display_name`` / ``logical_id`` before
        any template ever rendered.
        """
        template_pkg = "sigantry_core.sync.templates"
        with resources.as_file(
            resources.files(template_pkg) / self._template_name
        ) as template_path:
            env = Environment(
                loader=FileSystemLoader(template_path.parent),
                autoescape=select_autoescape(disabled_extensions=("j2",)),
                undefined=StrictUndefined,
                keep_trailing_newline=True,
            )
            template = env.get_template(template_path.name)
        return template.render(display_name=display_name, logical_id=logical_id)

    def _sidecar_resolve(self, source_path: Path) -> str:
        """Read or mint logical_id; persist on mint (D-12 / D-13).

        Sidecar lives at ``<source_path.parent>/.sigantry/<type-lower>-ids.json``;
        one sidecar PER item type so cross-type parallelism is safe
        (D-12). Within a type, Plan 13-04 packs items sequentially.

        For directory sources the key is ``source_path.name`` (the
        directory's own name); for file sources the key is the
        basename. The Plan 13-01 validator rejects collisions in the
        same source-dir before any packager sees the manifest.
        """
        sidecar_dir = source_path.parent / _SIDECAR_DIR_NAME
        sidecar_name = f"{self.item_type.lower()}-ids.json"
        sidecar = sidecar_dir / sidecar_name

        payload: dict = {}
        if sidecar.is_file():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "generic_sidecar_unparseable type=%s path=%s err=%s",
                    self.item_type,
                    sidecar,
                    exc,
                )
                payload = {}

        observed_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if observed_version is not None and observed_version != _SIDECAR_SCHEMA_VERSION:
            logger.warning(
                "generic_sidecar_unknown_schema_version type=%s path=%s observed=%s expected=%s",
                self.item_type,
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


__all__ = ("GenericPackager",)
