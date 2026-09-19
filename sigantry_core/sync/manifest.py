"""``sync.yml`` contract for the Phase 13 introspection / sync engine (SYNC-01 / SYNC-06).

Implements the pydantic v2 :class:`SyncManifest` + :class:`SyncItem`
models with all the locked validators. The contract surface is:

- :class:`SyncItem` -- one entry in ``items:``; ``frozen`` and
  ``extra='forbid'`` so a mistyped key fails fast.
- :class:`SyncManifest` -- top-level structure with the SemVer-pinned
  ``schema_version`` (D-05), the items list (validated per D-06), and
  the optional ``folders`` preservation set (Council D constraint #5).
- :func:`load_manifest` -- read + parse + validate a path on disk;
  raises :class:`ManifestValidationError` with a structured
  ``violations`` payload on any failure.

Locked decisions enforced here:

- **D-05** (forward-compat): ``schema_version`` accepts ``"1.0"`` and
  ``"1.0.0"``; ``"2.0.0"`` is rejected. Minor versions stable on the
  major; major bumps require a new committed JSON Schema.
- **D-06** (item type): ``SyncItem.type`` is canonicalised to the
  :data:`fabric_cicd.constants.ItemType` enum's ``.value``
  (PascalCase) via case-insensitive lookup; unknown types raise.
- **D-07 / SYNC-06** (folder-less types): items whose canonicalised
  type is in :data:`_FOLDER_LESS_CANONICAL` (or whose pre-canonical
  string is one of the spec-locked aliases retained for forward
  compatibility) emit a ``folder_less_type_override`` ``WARNING`` log
  record at validation; ``SyncManifest.normalised()`` returns a copy
  where their ``target_folder`` is forced to ``"/"``. The original
  instance preserves the operator-supplied input for diagnostics.
- **Council D #3** (depth): ``target_folder`` capped at 10 segments.
- **Council D #4** (segment hygiene): banned characters
  (``~"#.&*:<>?/{|}``), leading/trailing whitespace, length > 255,
  and C0/C1 control characters are rejected. Same rules apply to
  ``display_name``.

The module relies on :mod:`yaml` (a fabric-cicd transitive dependency)
and uses ``yaml.safe_load`` exclusively per the project-wide
T-10-06-01 invariant -- ``yaml.load`` is forbidden everywhere in
``sigantry_core/``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Final

import yaml
from fabric_cicd.constants import ItemType
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from sigantry_core.sync.errors import ManifestValidationError

logger = logging.getLogger("sigantry_core.sync.manifest")


# --------------------------------------------------------------------------
# Constants -- Council D constraints + locked decisions
# --------------------------------------------------------------------------

#: Banned characters in folder / display-name segments (Council D #4).
_BANNED_FOLDER_CHARS: Final[frozenset[str]] = frozenset('~"#.&*:<>?{|}')

#: Maximum nesting depth for ``target_folder`` (Council D #3).
_MAX_FOLDER_DEPTH: Final[int] = 10

#: Maximum length of any folder / display-name segment (Council D #4).
_MAX_NAME_LENGTH: Final[int] = 255

#: Allowed ``schema_version`` literals (D-05 forward-compat: minor
#: versions accepted on the same major; major bump requires a new
#: committed JSON Schema).
_ALLOWED_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset({"1.0", "1.0.0"})

#: Map from lower-cased upstream enum names AND ``.value`` strings to the
#: canonical ``.value`` (PascalCase). Built once at import time.
_TYPE_LOOKUP: Final[dict[str, str]] = {
    **{m.name.lower(): m.value for m in ItemType},
    **{m.value.lower(): m.value for m in ItemType},
}

#: Canonical (upstream-enum-value) names of folder-less item types
#: present in ``fabric_cicd.constants.ItemType`` as of fabric-cicd
#: 1.0.x. ``Dataflow`` is the upstream spelling for "Dataflow Gen2"
#: which Council D flagged as folder-less.
_FOLDER_LESS_CANONICAL: Final[frozenset[str]] = frozenset({"Dataflow"})

#: Spec-locked SYNC-06 aliases (lower-cased). These are kept as a
#: forward-compatibility marker -- a manifest that uses any of them as
#: ``type:`` will be rejected by the upstream-enum validator (D-06)
#: BEFORE the post-validate hook runs, but the aliases stay listed so a
#: future fabric-cicd release that introduces e.g.
#: ``StreamingSemanticModel`` lights up the warning automatically once
#: the canonical lookup picks the upstream spelling up.
_FOLDER_LESS_ALIASES: Final[frozenset[str]] = frozenset(
    {"dataflow_gen2", "streaming_semantic_model", "streaming_dataflow"}
)

#: Combined set used by the post-validate hook -- canonical
#: PascalCase entries plus the spec-locked aliases plus their
#: lower-cased forms.
_FOLDER_LESS_LOWER: Final[frozenset[str]] = (
    frozenset(s.lower() for s in _FOLDER_LESS_CANONICAL) | _FOLDER_LESS_ALIASES
)

#: C0 (``\x00-\x1f``) + C1 (``\x7f-\x9f``) control characters.
_CONTROL_CHAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f-\x9f]")


# --------------------------------------------------------------------------
# Helper -- validates a single segment against Council D #4
# --------------------------------------------------------------------------


def _segment_violations(segment: str, *, field: str) -> list[dict[str, Any]]:
    """Return the list of violations for one folder / display-name segment.

    The function returns a list (possibly empty) so callers can choose
    between raising on the first violation (``SyncItem`` field
    validators) or aggregating across an entire manifest.
    """
    violations: list[dict[str, Any]] = []
    if not segment:
        violations.append(
            {
                "field": field,
                "reason": "empty segment",
                "decision_id": "D-05/Council-D-4",
                "severity": "error",
            }
        )
        return violations
    if segment != segment.strip():
        violations.append(
            {
                "field": field,
                "reason": "leading or trailing whitespace",
                "decision_id": "D-05/Council-D-4",
                "severity": "error",
            }
        )
    if len(segment) > _MAX_NAME_LENGTH:
        violations.append(
            {
                "field": field,
                "reason": f"length {len(segment)} > {_MAX_NAME_LENGTH}",
                "decision_id": "D-05/Council-D-4",
                "severity": "error",
            }
        )
    banned = sorted({c for c in segment if c in _BANNED_FOLDER_CHARS})
    if banned:
        violations.append(
            {
                "field": field,
                "reason": f"banned characters: {banned}",
                "decision_id": "D-05/Council-D-4",
                "severity": "error",
            }
        )
    if _CONTROL_CHAR_PATTERN.search(segment):
        violations.append(
            {
                "field": field,
                "reason": "C0 or C1 control character",
                "decision_id": "D-05/Council-D-4",
                "severity": "error",
            }
        )
    return violations


# --------------------------------------------------------------------------
# SyncItem -- one manifest entry
# --------------------------------------------------------------------------


class SyncItem(BaseModel):
    """One entry in ``sync.yml`` ``items:``.

    Frozen and ``extra='forbid'`` so unintended keys fail fast.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: Path
    type: str
    target_folder: str = "/"
    display_name: str
    logical_id: str | None = None

    @field_validator("local_path", mode="before")
    @classmethod
    def _validate_local_path(cls, value: object) -> Path:
        """Reject parent-traversal segments at the syntactic boundary.

        Audit-2026-05-08 review follow-up (BL-03): pre-fix
        ``local_path`` had no validator. A manifest with
        ``local_path: ../../../etc/passwd`` was accepted by pydantic
        and resolved by ``_pack_phase`` against the operator's
        filesystem. The notebook packager would then copy the file
        into the staging tree (read-leak) and write a sidecar at
        ``<source.parent>/.sigantry/notebook-ids.json``
        (arbitrary-write outside the manifest dir). The threat
        materialises whenever a manifest comes from an untrusted
        source -- a PR, an external repo, or an operator picking up
        the ``templates/starter/`` scaffold.

        This validator is the syntactic line of defence: any path
        whose ``parts`` contain a literal ``".."`` segment is
        rejected at validate-time, before any filesystem access.

        Absolute paths are tolerated for backward compatibility with
        operator workflows that point at fixtures outside the
        manifest dir (e.g. test harnesses that share a source tree
        across manifests). The containment check in
        ``apply._pack_phase`` is the load-bearing security boundary:
        after ``Path.resolve()`` follows symlinks, the resolved real
        path MUST live within ``manifest_dir.resolve()``. An
        absolute ``/etc/shadow`` and a symlink-to-/etc/shadow both
        fail the containment check identically.

        Returns the value as a :class:`pathlib.Path` so downstream
        readers do not need a second ``Path()`` construction.
        """
        if not isinstance(value, str | Path):
            raise ValueError(
                f"local_path must be a string or pathlib.Path; got {type(value).__name__}"
            )
        p = Path(value)
        if any(part == ".." for part in p.parts):
            raise ValueError(
                f"local_path must not traverse parent directories with '..' "
                f"segments; got {value!r} (BL-03)"
            )
        return p

    @field_validator("type", mode="before")
    @classmethod
    def _canonicalise_type(cls, value: object) -> str:
        """Resolve ``type`` against ``ItemType`` (D-06).

        Lookup is case-insensitive against both the enum's ``.name`` and
        ``.value`` strings; the canonical ``.value`` (PascalCase, e.g.
        ``"Notebook"``) is returned. Unknown types raise -- the
        :func:`load_manifest` translator surfaces the failure with the
        ``D-06`` decision id.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValueError("type must be a non-empty string (D-06)")
        canonical = _TYPE_LOOKUP.get(value.strip().lower())
        if canonical is None:
            allowed = sorted({m.value for m in ItemType})
            raise ValueError(f"unknown item type {value!r}; expected one of {allowed} (D-06)")
        return canonical

    @field_validator("target_folder", mode="before")
    @classmethod
    def _normalise_target_folder(cls, value: object) -> str:
        """Normalise the path: empty -> '/', leading '/' optional, no trailing '/'.

        Returns the normalised string for the syntactic validator below
        to walk segment-by-segment.
        """
        if value in (None, ""):
            return "/"
        if not isinstance(value, str):
            raise ValueError("target_folder must be a string")
        normalised = value if value.startswith("/") else f"/{value}"
        if normalised != "/" and normalised.endswith("/"):
            normalised = normalised.rstrip("/") or "/"
        return normalised

    @field_validator("target_folder")
    @classmethod
    def _validate_target_folder(cls, value: str) -> str:
        """Enforce depth (Council D #3) and segment hygiene (Council D #4)."""
        if value == "/":
            return value
        segments = value.lstrip("/").split("/")
        if len(segments) > _MAX_FOLDER_DEPTH:
            raise ValueError(
                f"target_folder depth {len(segments)} exceeds "
                f"{_MAX_FOLDER_DEPTH} (D-05/Council-D-3)"
            )
        for i, seg in enumerate(segments):
            violations = _segment_violations(seg, field=f"target_folder[{i}]")
            if violations:
                detail = "; ".join(v["reason"] for v in violations)
                raise ValueError(f"target_folder segment {seg!r}: {detail} (D-05/Council-D-4)")
        return value

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        """Enforce Council D #4 hygiene rules on the display name."""
        violations = _segment_violations(value, field="display_name")
        if violations:
            detail = "; ".join(v["reason"] for v in violations)
            raise ValueError(f"display_name {value!r}: {detail} (D-05/Council-D-4)")
        return value


# --------------------------------------------------------------------------
# SyncManifest -- top-level sync.yml structure
# --------------------------------------------------------------------------


class SyncManifest(BaseModel):
    """Top-level ``sync.yml`` structure (SemVer-pinned via ``schema_version``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    items: list[SyncItem]
    folders: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        """Reject majors other than ``1`` (D-05 forward-compat policy)."""
        if value not in _ALLOWED_SCHEMA_VERSIONS:
            raise ValueError(
                f"schema_version {value!r} not in {sorted(_ALLOWED_SCHEMA_VERSIONS)} (D-05)"
            )
        return value

    @field_validator("folders")
    @classmethod
    def _validate_folders_preservation_set(cls, value: list[str]) -> list[str]:
        """Apply target_folder hygiene to operator-supplied preservation paths.

        The preservation list (Council D constraint #5) cannot smuggle
        banned characters or oversize segments through; we re-use the
        :class:`SyncItem` target-folder rules but do NOT mutate the
        operator's verbatim input.
        """
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError(
                    f"folders preservation entry must be a string, got {type(raw).__name__}"
                )
            normalised = raw if raw.startswith("/") else f"/{raw}"
            if normalised != "/" and normalised.endswith("/"):
                normalised = normalised.rstrip("/") or "/"
            if normalised == "/":
                continue
            segments = normalised.lstrip("/").split("/")
            if len(segments) > _MAX_FOLDER_DEPTH:
                raise ValueError(
                    f"folders entry {raw!r} depth {len(segments)} exceeds "
                    f"{_MAX_FOLDER_DEPTH} (D-05/Council-D-3)"
                )
            for i, seg in enumerate(segments):
                violations = _segment_violations(seg, field=f"folders[{i}]")
                if violations:
                    detail = "; ".join(v["reason"] for v in violations)
                    raise ValueError(
                        f"folders entry {raw!r} segment {seg!r}: {detail} (D-05/Council-D-4)"
                    )
        return value

    @model_validator(mode="after")
    def _post_validate(self) -> SyncManifest:
        """Surface SYNC-06 / D-07 folder-less type warnings.

        Validation passes -- the override is informational. The
        operator-supplied ``target_folder`` is preserved on the original
        instance (use :meth:`normalised` to obtain the corrected copy).
        """
        for index, item in enumerate(self.items):
            if item.type.lower() in _FOLDER_LESS_LOWER and item.target_folder != "/":
                logger.warning(
                    "folder_less_type_override "
                    "items[%d] type=%s display_name=%s "
                    "requested_target_folder=%s decision_id=D-07/SYNC-06 "
                    "will_force=/",
                    index,
                    item.type,
                    item.display_name,
                    item.target_folder,
                )
        return self

    def normalised(self) -> SyncManifest:
        """Return a copy where folder-less items have ``target_folder='/'``.

        The original :class:`SyncManifest` is preserved so error
        messages can quote the operator's literal input. This method is
        the deliberate gate for the D-07 / SYNC-06 override -- callers
        who want the corrected payload (e.g. ``sync apply``) opt in.
        """
        new_items: list[SyncItem] = []
        for item in self.items:
            if item.type.lower() in _FOLDER_LESS_LOWER and item.target_folder != "/":
                new_items.append(item.model_copy(update={"target_folder": "/"}))
            else:
                new_items.append(item)
        return SyncManifest(
            schema_version=self.schema_version,
            items=new_items,
            folders=list(self.folders),
        )


# --------------------------------------------------------------------------
# Public loader
# --------------------------------------------------------------------------


def load_manifest(path: Path | str) -> SyncManifest:
    """Read ``sync.yml`` from disk, parse via PyYAML, validate via pydantic.

    Raises :class:`ManifestValidationError` with structured
    ``violations`` on any validation failure -- pydantic's
    ``ValidationError`` is translated so callers do not need to import
    pydantic to render an error.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        payload = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ManifestValidationError(
            f"sync.yml at {p} failed YAML parse: {exc}",
            violations=[
                {
                    "field": "<root>",
                    "reason": str(exc),
                    "decision_id": "D-05",
                    "severity": "error",
                }
            ],
        ) from exc

    try:
        return SyncManifest.model_validate(payload)
    except ValidationError as exc:
        violations: list[dict[str, Any]] = []
        for err in exc.errors():
            field_path = ".".join(str(part) for part in err["loc"])
            reason = err.get("msg", "")
            decision_id = _classify_decision_id(field_path, reason)
            violations.append(
                {
                    "field": field_path,
                    "reason": reason,
                    "decision_id": decision_id,
                    "severity": "error",
                }
            )
        raise ManifestValidationError(
            f"sync.yml at {p} failed validation: {exc.error_count()} error(s)",
            violations=violations,
        ) from exc


def _classify_decision_id(field_path: str, reason: str) -> str:
    """Best-effort decision-id tagger for translated pydantic errors.

    Surfaces the most-specific locked decision id available given the
    field path + reason text. Falls back to the catch-all
    ``D-05/D-06/D-07`` triple for cases the specific tags do not match
    (e.g. a missing required field, which the SPEC covers under the
    general manifest-validation umbrella).
    """
    text = f"{field_path} {reason}"
    if "Council-D-3" in text or "depth" in reason.lower():
        return "D-05/Council-D-3"
    if "Council-D-4" in text or "banned characters" in reason or "control character" in reason:
        return "D-05/Council-D-4"
    if field_path.startswith("schema_version") or "schema_version" in reason:
        return "D-05"
    if field_path.endswith(".type") or "unknown item type" in reason:
        return "D-06"
    return "D-05/D-06/D-07"


__all__ = (
    "SyncItem",
    "SyncManifest",
    "load_manifest",
)
