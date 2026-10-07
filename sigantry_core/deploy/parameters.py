"""sigantry_core.deploy.parameters - parameters.yml loader + validator (DEPLOY-03).

fabric-cicd-native schema: ``find_replace``, ``key_value_replace``, ``spark_pool``,
``semantic_model_binding``. Reference forms:
  - ``$items.<Type>.<Name>`` / ``$items.<Type>.<Name>.$id``
  - ``$workspace.$id`` / ``$workspace.<name>``
  - ``$ENV:<VAR>`` (resolved at deploy time by **the toolkit** -- fabric-cicd
    1.x rejects this form in ``replace_value`` slots by default, so
    ``deploy/core.py`` substitutes ``$ENV:VAR`` -> ``os.environ[VAR]`` into a
    tempfile copy of ``parameters.yml`` before handing it to
    ``FabricWorkspace``. Upstream 1.1.0 adds a flag-gated
    ``enable_environment_variable_replacement`` path, but it only reads env
    vars literally NAMED ``$ENV:VAR`` -- unusable for the plain-named
    operator contract; pinned by
    ``tests/sigantry_core/deploy/test_fabric_cicd_contract.py``)
  - ``_ALL_`` environment wildcard

The file may be spelled ``parameters.yml`` (sigantry's docs) or
``parameter.yml`` (fabric-cicd's own default). :func:`resolve_parameters_path`
accepts either spelling and falls back to the other when the named one is
absent.

The toolkit adds TWO validators on top of upstream's shape check:
  1. Reject raw GUIDs outside ``$items.`` / ``$workspace.`` / ``_ALL_`` prefixes
     (HardcodedGuidError — DEPLOY-03 teeth, T-4-03 mitigation). A stock
     fabric-cicd file carries raw GUIDs by design, so the rule can be switched
     off with ``allow_raw_guids`` (CLI ``--allow-raw-guids``, settings
     ``[deploy] allow_raw_guids``, env ``SIGANTRY_DEPLOY__ALLOW_RAW_GUIDS``);
     every GUID so allowed is logged and listed on the result.
  2. ``$ENV:<VAR>`` references must resolve to a SET environment variable
     (RuntimeError; pre-flight catch rather than a cryptic deploy-time failure).
     With a target ``environment`` only that environment's slots, ``_ALL_``
     slots and slots outside the per-environment maps are checked, so a
     DEV deploy does not demand PROD's secrets.

Substitution is the toolkit's responsibility because fabric-cicd 1.x
(verified through 1.1.0) emits ``Invalid replace_value variable format``
for ``$ENV:`` refs outside its flag-gated path. See
``substitute_env_references`` + ``write_substituted_parameters``.

The full schema is validated by fabric-cicd at publish time — the toolkit does
NOT re-implement upstream's shape check.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

logger = logging.getLogger(__name__)

_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ENV_REF_RE = re.compile(r"\$ENV:([A-Z_][A-Z0-9_]*)")

# GUIDs are allowed only inside upstream fabric-cicd reference forms.
_ALLOWED_PREFIXES: tuple[str, ...] = ("$items.", "$workspace.", "_ALL_")

#: The two spellings the file may carry; sigantry's docs use the first,
#: fabric-cicd's own default is the second.
PARAMETER_FILE_NAMES: tuple[str, ...] = ("parameters.yml", "parameter.yml")

#: Keys whose value is a per-environment map (``{DEV: ..., PROD: ..., _ALL_: ...}``).
_ENV_KEYED: frozenset[str] = frozenset({"replace_value", "connection_id"})

#: The top-level blocks whose entries carry a ``replace_value`` map.
_ENTRY_SECTIONS: tuple[str, ...] = ("find_replace", "key_value_replace", "spark_pool")

ALL_ENVIRONMENTS = "_ALL_"


class HardcodedGuidError(ValueError):
    """Raised when a raw GUID appears in parameters.yml outside an allowed form.

    Message contains the offending GUID and the dotted path into the YAML
    (e.g. ``find_replace.[0].replace_value.DEV``) for actionable remediation.
    """


class UnknownEnvironmentError(ValueError):
    """Raised when the target environment is not declared anywhere in the file.

    Catches a mistyped ``--environment`` (``PRDO`` for ``PROD``) before a
    deploy that would silently apply no substitutions. A file whose every
    per-environment map carries ``_ALL_``, or that declares no environments
    at all, accepts any target.
    """


@dataclass(frozen=True, slots=True)
class ParametersConfig:
    """Validated parameters.yml payload.

    ``environments_seen`` excludes the ``_ALL_`` wildcard. ``raw_guids``
    lists the dotted path of every raw GUID that ``allow_raw_guids`` let
    through, so a caller can say how many the deploy relies on.
    """

    raw: dict[str, Any]
    path: str
    environments_seen: frozenset[str] = field(default_factory=frozenset)
    raw_guids: tuple[str, ...] = ()


def resolve_parameters_path(path: str | Path) -> Path:
    """Return the parameters file to read, accepting either spelling.

    ``path`` is used as given when it exists. When it does not, a sibling
    with the other spelling (``parameters.yml`` <-> ``parameter.yml``) is
    used; when ``path`` is a directory, the first spelling found inside it
    is used. Otherwise ``FileNotFoundError`` names every spelling tried.
    """
    p = Path(path)
    if p.is_file():
        return p
    candidates: list[Path] = []
    if p.is_dir():
        candidates = [p / name for name in PARAMETER_FILE_NAMES]
    elif p.name in PARAMETER_FILE_NAMES:
        candidates = [p.with_name(name) for name in PARAMETER_FILE_NAMES if name != p.name]
    for candidate in candidates:
        if candidate.is_file():
            logger.info("parameters file %s not found; using %s", p, candidate)
            return candidate
    tried = ", ".join(str(c) for c in [p, *candidates])
    raise FileNotFoundError(f"parameters file not found; tried {tried}")


def resolve_allow_raw_guids(flag: bool = False, *, settings_path: str | Path | None = None) -> bool:
    """Resolve the raw-GUID opt-in: explicit flag, else settings, else False.

    Order of precedence:
    1. ``flag`` (a CLI ``--allow-raw-guids``) when True.
    2. ``[deploy] allow_raw_guids`` in the settings file, or
       ``SIGANTRY_DEPLOY__ALLOW_RAW_GUIDS``, through
       :func:`sigantry_core.config.load_settings` (``settings_path`` names
       the file explicitly; ``None`` resolves it from the working directory).
    3. ``False``.

    A settings file that fails to load does not raise here: the opt-in stays
    off and the reason is logged, so the strict default is what applies.
    """
    if flag:
        return True
    try:
        from sigantry_core.config import load_settings

        settings = load_settings(settings_path)
    except Exception as exc:  # the strict default applies; say why
        logger.warning("allow_raw_guids: settings could not be loaded (%s); default False", exc)
        return False
    return bool(settings.deploy.allow_raw_guids)


def load_and_validate(
    path: str | Path,
    *,
    allow_raw_guids: bool = False,
    environment: str | None = None,
) -> ParametersConfig:
    """Read ``parameters.yml`` (or ``parameter.yml``); run the toolkit's validators.

    Args:
        path: Path to the parameters file; see :func:`resolve_parameters_path`.
        allow_raw_guids: Let raw GUIDs through (logged, and listed on the
            result) instead of raising ``HardcodedGuidError``.
        environment: The environment this deploy targets. When given, it must
            be declared in the file (or the file must use ``_ALL_``), and only
            its ``$ENV:`` references, ``_ALL_``'s and those outside the
            per-environment maps must resolve.

    Raises:
        FileNotFoundError: no file under either spelling.
        HardcodedGuidError: raw GUID outside allowed prefix (``allow_raw_guids`` off).
        UnknownEnvironmentError: ``environment`` is declared nowhere in the file.
        RuntimeError: unresolved ``$ENV:<VAR>`` reference in scope.

    Returns:
        ``ParametersConfig`` with the raw dict, the environments seen and the
        raw GUIDs allowed.
    """
    p = resolve_parameters_path(path)
    with p.open("r", encoding="utf-8") as fp:
        doc = yaml.safe_load(fp) or {}

    if not isinstance(doc, dict):
        # pyyaml returns scalars for weird files (e.g. a single string); reject
        # with a clear error rather than letting the validator walk miss.
        raise ValueError(f"parameters.yml at {p} must be a mapping; got {type(doc).__name__}")

    environments = _collect_environments(doc)
    if environment is not None:
        _check_target_environment(doc, environments, environment, str(p))
    raw_guids = _reject_hardcoded_guids(doc, str(p), allow_raw_guids=allow_raw_guids)
    _resolve_env_references(doc, environment=environment)

    return ParametersConfig(
        raw=doc,
        path=str(p),
        environments_seen=frozenset(environments),
        raw_guids=tuple(raw_guids),
    )


def _env_maps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every per-environment map in the document, in file order."""
    maps: list[dict[str, Any]] = []
    for section in _ENTRY_SECTIONS:
        for entry in doc.get(section, []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("replace_value"), dict):
                maps.append(entry["replace_value"])
    binding = doc.get("semantic_model_binding") or {}
    if isinstance(binding, dict):
        default = binding.get("default") or {}
        if isinstance(default, dict) and isinstance(default.get("connection_id"), dict):
            maps.append(default["connection_id"])
    return maps


def _collect_environments(doc: dict[str, Any]) -> set[str]:
    """Scan every per-environment map; capture environment keys used."""
    envs: set[str] = set()
    for env_map in _env_maps(doc):
        envs.update(str(k) for k in env_map)
    return {e for e in envs if e != ALL_ENVIRONMENTS}


def _check_target_environment(
    doc: dict[str, Any], environments: set[str], environment: str, path: str
) -> None:
    """Raise unless the target environment is declared, or the file uses ``_ALL_``."""
    if environment in environments or not environments:
        return
    # A map that carries ``_ALL_`` applies to any target; only a map with
    # neither the target nor the wildcard would silently apply nothing.
    if not any(
        env_map and ALL_ENVIRONMENTS not in env_map and environment not in env_map
        for env_map in _env_maps(doc)
    ):
        return
    raise UnknownEnvironmentError(
        f"{path}: environment {environment!r} is not declared in the file; "
        f"declared: {', '.join(sorted(environments))}. A mistyped --environment "
        f"would apply no substitutions."
    )


def _reject_hardcoded_guids(
    doc: dict[str, Any], path: str, *, allow_raw_guids: bool = False
) -> list[str]:
    """Walk the document; raise on the first raw GUID, or list them all when allowed."""
    allowed: list[str] = []

    def _walk(node: Any, trail: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, (*trail, str(k)))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, (*trail, f"[{i}]"))
        elif isinstance(node, str):
            match = _GUID_RE.search(node)
            if match is None:
                return
            stripped = node.strip()
            # Accept if the surrounding string begins with an allowed prefix.
            if any(stripped.startswith(pfx) for pfx in _ALLOWED_PREFIXES):
                return
            where = ".".join(trail) or "<root>"
            if allow_raw_guids:
                logger.warning(
                    "%s: raw GUID %s at %s allowed by allow_raw_guids", path, match.group(0), where
                )
                allowed.append(where)
                return
            raise HardcodedGuidError(
                f"{path}: hard-coded GUID {match.group(0)} at {where}. "
                f"Use $items.<Type>.<Name>.$id, $workspace.$id, or $ENV:<VAR>, "
                f"or pass --allow-raw-guids for a stock fabric-cicd file."
            )

    _walk(doc, ())
    return allowed


def _in_scope(parent_key: str | None, key: Any, environment: str | None) -> bool:
    """Whether a per-environment slot applies to the target environment."""
    if environment is None or parent_key not in _ENV_KEYED:
        return True
    return key in (environment, ALL_ENVIRONMENTS)


def _resolve_env_references(doc: dict[str, Any], *, environment: str | None = None) -> None:
    """Check every in-scope ``$ENV:<VAR>`` reference resolves to a set variable.

    Pre-flight reachability check. Substitution is performed separately by
    :func:`substitute_env_references` immediately before the substituted
    document is written to disk and handed to fabric-cicd. With a target
    ``environment``, slots keyed by another environment are not checked:
    a DEV deploy does not need PROD's secrets set.
    """

    def _walk(node: Any, parent_key: str | None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if _in_scope(parent_key, k, environment):
                    _walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                _walk(v, parent_key)
        elif isinstance(node, str):
            for m in _ENV_REF_RE.finditer(node):
                if m.group(1) not in os.environ:
                    raise RuntimeError(
                        f"parameters.yml references $ENV:{m.group(1)} but the "
                        f"variable is not set in the current environment."
                    )

    _walk(doc, None)


def substitute_env_references(
    doc: dict[str, Any], *, environment: str | None = None
) -> dict[str, Any]:
    """Return a deep copy of ``doc`` with every in-scope ``$ENV:<VAR>`` token expanded.

    fabric-cicd 1.x does NOT recognise ``$ENV:`` references in
    ``replace_value`` slots by default -- it raises ``Invalid replace_value
    variable format``. (1.1.0's flag-gated alternative reads env vars
    literally named ``$ENV:VAR`` and so cannot serve the plain-named
    contract.) The toolkit performs the substitution itself so operators
    may use env-var refs in ``parameters.yml`` per the documented contract.

    Multiple ``$ENV:VAR`` tokens may appear inside a single string and each
    is replaced independently. Tokens are matched by ``_ENV_REF_RE``
    (``\\$ENV:[A-Z_][A-Z0-9_]*``); anything outside that shape is left
    untouched. Callers MUST run :func:`load_and_validate` first so missing
    env vars surface as a clear ``RuntimeError`` rather than silently
    leaving placeholders in the substituted document.

    With a target ``environment``, the copy keeps only that environment's
    slot and ``_ALL_`` in every per-environment map, so no other
    environment's token, resolved or not, reaches fabric-cicd. An entry
    whose ``replace_value`` has no slot left applies to no item in this
    deploy and is dropped; a ``connection_id`` with no slot left is removed.

    Args:
        doc: Parsed parameters.yml mapping (as returned by
            :class:`ParametersConfig`.raw).
        environment: The environment this deploy targets, or ``None`` to
            expand every slot.

    Returns:
        A new dict with ``$ENV:VAR`` tokens replaced by ``os.environ[VAR]``.
        Non-string scalars + dict / list structure are preserved exactly.

    Raises:
        KeyError: an in-scope env var is unset. ``load_and_validate``
            already gates on this; the explicit raise here is a defence
            in depth so direct callers cannot accidentally produce a
            silently-broken document.
    """

    def _expand(text: str) -> str:
        def _sub(m: Any) -> str:
            name = m.group(1)
            if name not in os.environ:
                raise KeyError(
                    f"parameters.yml references $ENV:{name} but the variable "
                    f"is not set in the current environment."
                )
            return os.environ[name]

        return _ENV_REF_RE.sub(_sub, text)

    def _walk(node: Any, parent_key: str | None) -> Any:
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for k, v in node.items():
                if not _in_scope(parent_key, k, environment):
                    continue
                child = _walk(v, str(k))
                if k == "connection_id" and environment is not None and child == {}:
                    continue  # no slot for this environment: not bound here
                out[k] = child
            return out
        if isinstance(node, list):
            return [_walk(v, parent_key) for v in node]
        if isinstance(node, str):
            return _expand(node)
        return node

    substituted = cast(dict[str, Any], _walk(doc, None))
    if environment is not None:
        for section in _ENTRY_SECTIONS:
            entries = substituted.get(section)
            if isinstance(entries, list):
                substituted[section] = [
                    e
                    for e in entries
                    if not (
                        isinstance(e, dict) and "replace_value" in e and e["replace_value"] == {}
                    )
                ]
    return substituted


def write_substituted_parameters(
    config: ParametersConfig,
    target_path: str | Path,
    *,
    environment: str | None = None,
) -> Path:
    """Substitute ``$ENV:`` refs in ``config.raw`` and write to ``target_path``.

    Convenience wrapper around :func:`substitute_env_references` +
    ``yaml.safe_dump``. ``target_path``'s parent directory is created if it
    does not already exist. The file is written with ``sort_keys=False``
    so YAML key order in the substituted document matches the source.

    Args:
        config: A validated ``ParametersConfig`` from :func:`load_and_validate`.
        target_path: Where to write the substituted YAML.
        environment: Passed through to :func:`substitute_env_references`.

    Returns:
        The resolved ``Path`` that was written.
    """
    substituted = substitute_env_references(config.raw, environment=environment)
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(substituted, fp, sort_keys=False)
    return p
