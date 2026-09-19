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

The toolkit adds TWO validators on top of upstream's shape check:
  1. Reject raw GUIDs outside ``$items.`` / ``$workspace.`` / ``_ALL_`` prefixes
     (HardcodedGuidError — DEPLOY-03 teeth, T-4-03 mitigation).
  2. ``$ENV:<VAR>`` references must resolve to a SET environment variable
     (RuntimeError; pre-flight catch rather than a cryptic deploy-time failure).

Substitution is the toolkit's responsibility because fabric-cicd 1.x
(verified through 1.1.0) emits ``Invalid replace_value variable format``
for ``$ENV:`` refs outside its flag-gated path. See
``substitute_env_references`` + ``write_substituted_parameters``.

The full schema is validated by fabric-cicd at publish time — the toolkit does
NOT re-implement upstream's shape check.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ENV_REF_RE = re.compile(r"\$ENV:([A-Z_][A-Z0-9_]*)")

# GUIDs are allowed only inside upstream fabric-cicd reference forms.
_ALLOWED_PREFIXES: tuple[str, ...] = ("$items.", "$workspace.", "_ALL_")


class HardcodedGuidError(ValueError):
    """Raised when a raw GUID appears in parameters.yml outside an allowed form.

    Message contains the offending GUID and the dotted path into the YAML
    (e.g. ``find_replace.[0].replace_value.DEV``) for actionable remediation.
    """


@dataclass(frozen=True, slots=True)
class ParametersConfig:
    """Validated parameters.yml payload.

    ``environments_seen`` excludes the ``_ALL_`` wildcard.
    """

    raw: dict[str, Any]
    path: str
    environments_seen: frozenset[str] = field(default_factory=frozenset)


def load_and_validate(path: str | Path) -> ParametersConfig:
    """Read ``parameters.yml``; run the toolkit's two validators.

    Args:
        path: Path to parameters.yml.

    Raises:
        FileNotFoundError: missing file.
        HardcodedGuidError: raw GUID outside allowed prefix.
        RuntimeError: unresolved ``$ENV:<VAR>`` reference.

    Returns:
        ``ParametersConfig`` with the raw dict + environments-seen set.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"parameters.yml not found at {p}")
    with p.open("r", encoding="utf-8") as fp:
        doc = yaml.safe_load(fp) or {}

    if not isinstance(doc, dict):
        # pyyaml returns scalars for weird files (e.g. a single string); reject
        # with a clear error rather than letting the validator walk miss.
        raise ValueError(f"parameters.yml at {p} must be a mapping; got {type(doc).__name__}")

    environments = _collect_environments(doc)
    _reject_hardcoded_guids(doc, str(p))
    _resolve_env_references(doc)

    return ParametersConfig(
        raw=doc,
        path=str(p),
        environments_seen=frozenset(environments),
    )


def _collect_environments(doc: dict[str, Any]) -> set[str]:
    """Scan every ``replace_value`` dict; capture environment keys used."""
    envs: set[str] = set()
    for entry in doc.get("find_replace", []) or []:
        if isinstance(entry, dict):
            envs.update((entry.get("replace_value") or {}).keys())
    for entry in doc.get("key_value_replace", []) or []:
        if isinstance(entry, dict):
            envs.update((entry.get("replace_value") or {}).keys())
    for entry in doc.get("spark_pool", []) or []:
        if isinstance(entry, dict):
            envs.update((entry.get("replace_value") or {}).keys())
    binding = doc.get("semantic_model_binding") or {}
    if isinstance(binding, dict):
        default = binding.get("default") or {}
        if isinstance(default, dict):
            conn = default.get("connection_id") or {}
            if isinstance(conn, dict):
                envs.update(conn.keys())
    return {e for e in envs if e != "_ALL_"}


def _reject_hardcoded_guids(doc: dict[str, Any], path: str) -> None:
    """Recursive walk — fails on any GUID outside an allowed reference form."""

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
            raise HardcodedGuidError(
                f"{path}: hard-coded GUID {match.group(0)} at "
                f"{'.'.join(trail) or '<root>'}. "
                f"Use $items.<Type>.<Name>.$id, $workspace.$id, or $ENV:<VAR>."
            )

    _walk(doc, ())


def _resolve_env_references(doc: dict[str, Any]) -> None:
    """Check every ``$ENV:<VAR>`` reference resolves to a set environment variable.

    Pre-flight reachability check. Substitution is performed separately by
    :func:`substitute_env_references` immediately before the substituted
    document is written to disk and handed to fabric-cicd.
    """

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
        elif isinstance(node, str):
            for m in _ENV_REF_RE.finditer(node):
                if m.group(1) not in os.environ:
                    raise RuntimeError(
                        f"parameters.yml references $ENV:{m.group(1)} but the "
                        f"variable is not set in the current environment."
                    )

    _walk(doc)


def substitute_env_references(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``doc`` with every ``$ENV:<VAR>`` token expanded.

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

    Args:
        doc: Parsed parameters.yml mapping (as returned by
            :class:`ParametersConfig`.raw).

    Returns:
        A new dict with ``$ENV:VAR`` tokens replaced by ``os.environ[VAR]``.
        Non-string scalars + dict / list structure are preserved exactly.

    Raises:
        KeyError: a referenced env var is unset. ``load_and_validate``
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

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            return _expand(node)
        return node

    return _walk(doc)


def write_substituted_parameters(config: ParametersConfig, target_path: str | Path) -> Path:
    """Substitute ``$ENV:`` refs in ``config.raw`` and write to ``target_path``.

    Convenience wrapper around :func:`substitute_env_references` +
    ``yaml.safe_dump``. ``target_path``'s parent directory is created if it
    does not already exist. The file is written with ``sort_keys=False``
    so YAML key order in the substituted document matches the source.

    Args:
        config: A validated ``ParametersConfig`` from :func:`load_and_validate`.
        target_path: Where to write the substituted YAML.

    Returns:
        The resolved ``Path`` that was written.
    """
    substituted = substitute_env_references(config.raw)
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(substituted, fp, sort_keys=False)
    return p
