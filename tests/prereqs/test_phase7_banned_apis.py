"""Dependency direction and the one-HTTP-client rule for ``sigantry_core/``.

Packages built on Sigantry depend on ``sigantry_core``, never the reverse.
Plugins are found at run time through their entry-point groups
(``sigantry_core/registry.py``), never imported by name. So at import time
(defined below), a module of the base may import only:

* the standard library;
* ``sigantry_core`` itself;
* a third-party root in ``_PERMITTED_THIRD_PARTY_ROOTS`` below, or a module
  inside one. Each root is declared in ``pyproject.toml`` or required by a
  dependency declared there.

An ``import`` or ``from ... import`` statement at import time that names
anything else -- an organisation's private package, a plugin, an undeclared
library outside the permitted roots -- fails here without this file having
to name it. "Import time" here means everything outside a function body:
the module body and every ``if``, ``try``, ``with``, loop and class body
inside it. That includes ``if TYPE_CHECKING:`` blocks, which never run but
are read like the rest. An import inside a function body is not policed
here, even when that function is called while the module is imported.

The last two tests keep the one-HTTP-client rule for two subpackages:
anywhere in ``sigantry_core/dq/`` or ``sigantry_core/deploy/profiles/``,
function bodies included, no import statement imports ``httpx`` or a module
inside it, and none imports the name ``httpx`` from another module.
Package-wide, ``httpx`` imports are limited by ruff's TID251 ``banned-api``
setting in ``pyproject.toml`` and by
``tests/sigantry_core/client/test_package_structure.py``.

These are file reads and AST walks: nothing under test is imported, and
there is no network. Two checks read the installed environment instead:
which names are namespace packages, and what a declared parent requires
(``importlib`` lookups and package metadata; no module of a permitted root
is executed).
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tomllib
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SIGANTRY_CORE = _REPO_ROOT / "sigantry_core"
_FIRST_PARTY = "sigantry_core"

# Namespace packages: several distributions install modules under each of
# these names, so the name alone does not say which distribution an import
# needs. An import under a namespace is checked one component deeper:
# ``azure.identity``, and ``azure.keyvault.secrets`` because
# ``azure.keyvault`` is itself shared by several distributions.
# The check reads names, not distributions: a module that another
# distribution installs inside a permitted root is accepted with that root.
_NAMESPACES = frozenset({"azure", "azure.keyvault"})

# Root (see ``_root``) -> (distribution that provides it, declared parent).
# The parent is None when that distribution is itself declared in
# pyproject.toml (as a dependency or in an extra). Otherwise the parent is
# the declared dependency that requires the distribution, and it must be
# declared instead.
#
# Adding an entry is a dependency decision: say which declared dependency
# provides the root. Removing the last import of a root means removing its
# entry in the same change (``test_permitted_roots_are_all_in_use``).
_PERMITTED_THIRD_PARTY_ROOTS: dict[str, tuple[str, str | None]] = {
    # Not declared directly: azure-identity requires it.
    "azure.core": ("azure-core", "azure-identity"),
    "azure.identity": ("azure-identity", None),
    "azure.keyvault.secrets": ("azure-keyvault-secrets", None),
    "fabric_cicd": ("fabric-cicd", None),
    "httpx": ("httpx", None),
    "jinja2": ("jinja2", None),
    "jsonschema": ("jsonschema", None),
    "jwt": ("PyJWT", None),
    "nacl": ("pynacl", None),
    "pydantic": ("pydantic", None),
    "pydantic_settings": ("pydantic-settings", None),
    "pyrate_limiter": ("pyrate-limiter", None),
    # Imported only by the pytest plugin module (sigantry_core/testing/
    # fixtures.py, registered under the pytest11 entry point), which pytest
    # itself loads. Declared in the dev and test extras.
    "pytest": ("pytest", None),
    "rich": ("rich", None),
    "tenacity": ("tenacity", None),
    "typer": ("typer", None),
    # Not declared directly: fabric-cicd requires it.
    "yaml": ("PyYAML", "fabric-cicd"),
}

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalise(name: str) -> str:
    """The PEP 503 form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_distributions() -> set[str]:
    """Every distribution pyproject.toml declares, extras included."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names: set[str] = set()
    for requirement in requirements:
        match = _REQUIREMENT_NAME.match(requirement)
        assert match, f"cannot read a distribution name from {requirement!r} in pyproject.toml"
        names.add(_normalise(match.group(1)))
    assert names, "pyproject.toml declares no dependencies; this check would be vacuous"
    return names


def _import_time_imports(node: ast.AST) -> Iterator[tuple[int, str]]:
    """``(lineno, dotted name)`` for each absolute import statement at import time.

    Descends into every statement except function bodies, so compound
    statements and class bodies are covered. Relative imports stay inside
    the package and are skipped.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Import):
            for alias in child.names:
                yield child.lineno, alias.name
        elif isinstance(child, ast.ImportFrom):
            if child.level == 0 and child.module in _NAMESPACES:
                # ``from azure.keyvault import secrets`` imports
                # ``azure.keyvault.secrets``: check the module it names.
                for alias in child.names:
                    yield child.lineno, f"{child.module}.{alias.name}"
            elif child.level == 0 and child.module:
                yield child.lineno, child.module
        else:
            yield from _import_time_imports(child)


def _top_level(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _root(dotted: str) -> str:
    """The name an import is checked under.

    That is its top-level name, or, inside a namespace package, the name
    one component below the namespace.
    """
    parts = dotted.split(".")
    depth = 1
    while depth < len(parts) and ".".join(parts[:depth]) in _NAMESPACES:
        depth += 1
    return ".".join(parts[:depth])


def _is_stdlib(top_level: str) -> bool:
    return top_level in sys.stdlib_module_names


def _third_party_imports() -> list[tuple[str, int, str]]:
    """``(path, lineno, root)`` for each import-time third-party import in the base."""
    paths = sorted(p for p in _SIGANTRY_CORE.rglob("*.py") if "__pycache__" not in p.parts)
    assert paths, f"no Python files under {_SIGANTRY_CORE}; this check would be vacuous"
    found: list[tuple[str, int, str]] = []
    for path in paths:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        # A file that does not parse fails here rather than being skipped.
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for lineno, dotted in _import_time_imports(tree):
            top_level = _top_level(dotted)
            if top_level == _FIRST_PARTY or _is_stdlib(top_level):
                continue
            found.append((rel, lineno, _root(dotted)))
    return found


def test_import_time_imports_are_stdlib_self_or_declared() -> None:
    """At import time, the base imports only the stdlib, itself and the permitted roots."""
    offenders = [
        f"{rel}:{lineno} imports {root!r}"
        for rel, lineno, root in _third_party_imports()
        if root not in _PERMITTED_THIRD_PARTY_ROOTS
    ]
    assert offenders == [], (
        "sigantry_core imports a package at import time that is not the standard "
        "library, sigantry_core, or a permitted third-party root. Packages built on "
        "Sigantry depend on it, never the reverse: load a plugin through its "
        "entry-point group, defer an optional import into the function that needs "
        "it, or, for a new dependency, declare it in pyproject.toml and add its "
        "root to _PERMITTED_THIRD_PARTY_ROOTS.\n  - " + "\n  - ".join(offenders)
    )


def _required_by(parent: str) -> set[str]:
    """The distributions an installed ``parent`` requires, extras excluded."""
    names: set[str] = set()
    for requirement in metadata.requires(parent) or []:
        if re.search(r"\bextra\s*==", requirement):
            continue
        match = _REQUIREMENT_NAME.match(requirement)
        if match:
            names.add(_normalise(match.group(1)))
    return names


def test_permitted_roots_are_declared_dependencies() -> None:
    """Each permitted root is backed by a dependency pyproject.toml declares.

    A root whose distribution is not declared names the declared parent that
    brings it in, and the parent's installed metadata must require it.
    """
    declared = _declared_distributions()
    undeclared = []
    for root, (distribution, parent) in sorted(_PERMITTED_THIRD_PARTY_ROOTS.items()):
        required = parent if parent is not None else distribution
        if _normalise(required) not in declared:
            undeclared.append(f"{root!r} needs {required!r}")
        elif parent is not None:
            try:
                parent_requires = _required_by(parent)
            except metadata.PackageNotFoundError:
                undeclared.append(
                    f"{root!r}: {parent!r} is not installed; cannot read what it requires"
                )
                continue
            if _normalise(distribution) not in parent_requires:
                undeclared.append(f"{root!r}: {parent!r} does not require {distribution!r}")
    assert undeclared == [], (
        "_PERMITTED_THIRD_PARTY_ROOTS names a root whose distribution pyproject.toml "
        f"does not declare: {undeclared}"
    )


def _is_namespace_package(name: str) -> bool:
    spec = importlib.util.find_spec(name)
    assert spec is not None, f"{name!r} is not installed; it cannot be checked"
    return spec.origin is None and spec.submodule_search_locations is not None


def test_namespace_list_matches_the_installed_packages() -> None:
    """``_NAMESPACES`` lists real namespace packages, and no permitted root is one.

    A namespace package is shared by several distributions, so permitting
    it would permit every distribution that installs under it. Read from the
    installed environment rather than kept by hand, so a new shared prefix
    (``azure.monitor``, ``google``) is caught without editing this test.
    """
    not_namespaces = sorted(n for n in _NAMESPACES if not _is_namespace_package(n))
    assert not_namespaces == [], f"_NAMESPACES lists regular packages: {not_namespaces}"
    namespace_roots = sorted(r for r in _PERMITTED_THIRD_PARTY_ROOTS if _is_namespace_package(r))
    assert namespace_roots == [], (
        "these permitted roots are namespace packages; permit the module one level "
        f"below instead, and add the namespace to _NAMESPACES: {namespace_roots}"
    )


def test_permitted_roots_are_all_in_use() -> None:
    """An entry no module imports any more is a stale permission."""
    in_use = {root for _, _, root in _third_party_imports()}
    stale = sorted(set(_PERMITTED_THIRD_PARTY_ROOTS) - in_use)
    assert stale == [], f"remove these entries from _PERMITTED_THIRD_PARTY_ROOTS: {stale}"


def test_import_walker_sees_import_time_imports_and_skips_deferred_ones() -> None:
    """The walker reports every absolute import outside a function body, and only those."""
    source = (
        "from __future__ import annotations\n"
        "import os\n"
        "import example_dq_lib\n"
        "from sigantry_core import config\n"
        "from . import sibling\n"
        "try:\n"
        "    import sigantry_example\n"
        "except ImportError:\n"
        "    import sigantry_example_fallback\n"
        "if TYPE_CHECKING:\n"
        "    from example_types import T\n"
        "class C:\n"
        "    import example_in_class\n"
        "def f():\n"
        "    import example_deferred\n"
        "async def g():\n"
        "    import example_deferred_async\n"
    )
    names = {dotted for _, dotted in _import_time_imports(ast.parse(source))}
    assert names == {
        "__future__",
        "os",
        "example_dq_lib",
        "sigantry_core",
        "sigantry_example",
        "sigantry_example_fallback",
        "example_types",
        "example_in_class",
    }, names
    assert _is_stdlib("os")
    assert _is_stdlib("__future__")
    assert not _is_stdlib("example_dq_lib")
    assert "example_dq_lib" not in _PERMITTED_THIRD_PARTY_ROOTS


def test_namespace_imports_are_checked_below_the_namespace() -> None:
    """A namespace alone does not name a distribution, so it is never a root."""
    assert _root("httpx._exceptions") == "httpx"
    assert _root("azure.core.exceptions") == "azure.core"
    assert _root("azure.keyvault.secrets") == "azure.keyvault.secrets"
    assert _root("azure.keyvault.keys") == "azure.keyvault.keys"
    assert _root("azure.storage.blob") == "azure.storage"
    assert _root("azure") == "azure"
    for namespace in _NAMESPACES:
        assert namespace not in _PERMITTED_THIRD_PARTY_ROOTS, namespace
    assert "azure.keyvault.keys" not in _PERMITTED_THIRD_PARTY_ROOTS
    assert "azure.storage" not in _PERMITTED_THIRD_PARTY_ROOTS


def _iter_py_files(root: Path) -> Iterator[Path]:
    """Yield every .py file under ``root`` (recursive)."""
    for path in root.rglob("*.py"):
        if path.is_file():
            yield path


def _httpx_imports(root: Path) -> list[tuple[Path, int, str]]:
    """Every import statement under ``root`` that names ``httpx``, function bodies included.

    A statement names ``httpx`` when it imports ``httpx`` or a module inside
    it, or imports the name ``httpx`` from another module.
    """
    paths = sorted(_iter_py_files(root))
    assert paths, f"no Python files under {root}; this check would be vacuous"
    offenders: list[tuple[Path, int, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
                if node.level == 0 and node.module:
                    names.append(node.module)
            else:
                continue
            if any(_top_level(name) == "httpx" for name in names):
                offenders.append((path, node.lineno, ast.unparse(node)))
    return offenders


def test_no_httpx_import_in_dq_subpackage() -> None:
    """sigantry_core.dq reaches HTTP only through sigantry_core.client."""
    offenders = _httpx_imports(_SIGANTRY_CORE / "dq")
    assert offenders == [], (
        "Direct httpx imports are banned here; use sigantry_core.client. "
        f"Offenders in sigantry_core/dq/: {offenders}."
    )


def test_no_httpx_import_in_deploy_profiles() -> None:
    """sigantry_core.deploy.profiles reaches HTTP only through sigantry_core.client."""
    offenders = _httpx_imports(_SIGANTRY_CORE / "deploy" / "profiles")
    assert offenders == [], (
        "Direct httpx imports are banned here; use sigantry_core.client. "
        f"Offenders in sigantry_core/deploy/profiles/: {offenders}."
    )
