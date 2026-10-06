"""CLI print sites in the modules below hand Rich no data as markup.

Rich reads ``[...]`` in a ``str`` it prints (or puts in a table cell, a
title or a column header) as markup and ``:name:`` as an emoji code. Data
that reaches such a ``str`` -- an item name, a release id, a path, an
exception message -- can raise ``MarkupError`` (``[/old]``) or lose text
(``[draft]``). A :class:`rich.text.Text` is not parsed, so data goes to
Rich wrapped in ``Text(...)``, ``Text.assemble(...)`` or a helper that
returns one, or escaped with ``rich.markup.escape``.

This test reads the modules' syntax trees. The sinks it checks are:

* the positional arguments of ``print`` on any receiver, of ``log``,
  ``rule``, ``status`` and ``input`` on a ``Console``, and of ``print``
  imported from ``rich``;
* the positional arguments and the ``footer=`` keyword of ``add_row`` and
  ``add_column``;
* the positional arguments and the ``title=``, ``caption=``, ``subtitle=``,
  ``header=`` and ``footer=`` keywords of ``Table``, ``Panel``, ``Tree`` and
  ``Rule`` called by those bare names;
* the positional arguments of ``Text.from_markup``.

A call passing ``markup=False`` is skipped. Other spellings are not checked,
among them ``Tree.add``, ``add_column(header=...)`` and
``Table.grid(title=...)``.

Every argument a checked sink receives must be one of:

* a string literal (literal markup such as ``"[green]OK[/green]"`` is fine);
* an f-string, ``%``-format, ``.format()`` call or ``+`` concatenation whose
  every interpolated value is a literal, provably an ``int`` (an int
  literal, ``len(...)``, arithmetic on those) or escaped;
* a ``Text`` (or a helper returning one); a ``Table``, ``Panel``, ``Tree``
  or ``Rule``, whose arguments are checked as sinks as listed above; or a
  ``Syntax``, ``JSON`` or ``Console``, which Rich does not read as markup;
* a local name every assignment of which is one of the above.

Anything else -- an attribute, a parameter, a loop variable, a call
result, another renderable such as ``Columns`` or ``Group`` -- is reported
with its line.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Module -> the functions checked in it (``None``: the whole module).
SCOPE: dict[str, frozenset[str] | None] = {
    "sigantry_core/diff_cli.py": None,
    "sigantry_core/release/cli.py": None,
    "sigantry_core/deploy/cli.py": None,
    "sigantry_core/sync/cli.py": frozenset({"pull_cmd", "_emit_preview_warning_once"}),
}

#: Console methods whose positional arguments Rich reads as markup.
_CONSOLE_SINKS = frozenset({"print", "log", "rule", "status", "input"})
#: Methods on any receiver whose positional arguments Rich reads as markup.
_TABLE_SINKS = frozenset({"add_row", "add_column"})
#: Constructors whose positional arguments and these keywords Rich reads as markup.
_RENDERABLE_SINKS = frozenset({"Table", "Panel", "Tree", "Rule"})
_MARKUP_KEYWORDS = frozenset({"title", "caption", "subtitle", "header", "footer"})
#: Calls that return a value Rich does not parse.
_TEXT_CALLS = frozenset({"Text", "_data", "escape"})
_TEXT_METHODS = frozenset({"assemble", "from_ansi"})  # Text.assemble / Text.from_ansi
_RENDERABLES = _RENDERABLE_SINKS | frozenset({"Syntax", "JSON", "Console"})


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    sink: str
    source: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.sink} receives data as markup: {self.source}"


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class _Checker:
    def __init__(self, tree: ast.Module, path: str, functions: frozenset[str] | None) -> None:
        self.tree = tree
        self.path = path
        self.functions = functions
        self.rich_print = any(
            isinstance(n, ast.ImportFrom)
            and n.module == "rich"
            and any(a.name == "print" for a in n.names)
            for n in ast.walk(tree)
        )
        self.module_assigns = self._assignments(tree.body)

    # -- name resolution -------------------------------------------------

    @staticmethod
    def _assignments(body: list[ast.stmt]) -> dict[str, list[ast.expr | None]]:
        """Values bound to each simple name in ``body`` (``None``: bound by a
        construct whose value is unknown -- loop, with, except, import)."""
        found: dict[str, list[ast.expr | None]] = {}
        holder = ast.Module(body=body, type_ignores=[])
        for node in ast.walk(holder):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found.setdefault(target.id, []).append(node.value)
                    else:
                        for sub in ast.walk(target):
                            if isinstance(sub, ast.Name):
                                found.setdefault(sub.id, []).append(None)
            elif isinstance(node, ast.AnnAssign | ast.AugAssign) and isinstance(
                node.target, ast.Name
            ):
                value = node.value if isinstance(node, ast.AnnAssign) else None
                found.setdefault(node.target.id, []).append(value)
            elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                for sub in ast.walk(node.target):
                    if isinstance(sub, ast.Name):
                        found.setdefault(sub.id, []).append(None)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                for sub in ast.walk(node.optional_vars):
                    if isinstance(sub, ast.Name):
                        found.setdefault(sub.id, []).append(None)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                found.setdefault(node.name, []).append(None)
            elif isinstance(node, ast.NamedExpr):
                found.setdefault(node.target.id, []).append(node.value)
        return found

    def _values_of(self, name: str, func: ast.FunctionDef | None) -> list[ast.expr | None]:
        if func is not None:
            params = {
                a.arg
                for a in (
                    *func.args.posonlyargs,
                    *func.args.args,
                    *func.args.kwonlyargs,
                    *([func.args.vararg] if func.args.vararg else []),
                    *([func.args.kwarg] if func.args.kwarg else []),
                )
            }
            if name in params:
                return [None]
            local = self._assignments(func.body)
            if name in local:
                return local[name]
        return self.module_assigns.get(name, [None])

    # -- classification --------------------------------------------------

    def is_int(self, node: ast.expr, func: ast.FunctionDef | None, seen: frozenset[str]) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, int) and not isinstance(node.value, bool)
        if isinstance(node, ast.Call) and _call_name(node.func) == "len":
            return isinstance(node.func, ast.Name)
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, ast.Add | ast.Sub | ast.Mult | ast.FloorDiv | ast.Mod
        ):
            return self.is_int(node.left, func, seen) and self.is_int(node.right, func, seen)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            return self.is_int(node.operand, func, seen)
        if isinstance(node, ast.Name) and node.id not in seen:
            values = self._values_of(node.id, func)
            return all(v is not None and self.is_int(v, func, seen | {node.id}) for v in values)
        return False

    def _is_escaped(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and _call_name(node.func) == "escape"

    def _interpolated_ok(
        self, node: ast.expr, func: ast.FunctionDef | None, seen: frozenset[str]
    ) -> bool:
        """A value placed inside a markup string: a literal, an int, or escaped."""
        if isinstance(node, ast.Constant):
            return True
        return self.is_int(node, func, seen) or self._is_escaped(node)

    def is_safe(self, node: ast.expr, func: ast.FunctionDef | None, seen: frozenset[str]) -> bool:
        """True if Rich can be handed ``node`` without reading data as markup."""
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.JoinedStr):
            return all(
                isinstance(part, ast.Constant)
                or (
                    isinstance(part, ast.FormattedValue)
                    and self._interpolated_ok(part.value, func, seen)
                )
                for part in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.is_safe(node.left, func, seen) and self.is_safe(node.right, func, seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            args = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            return isinstance(node.left, ast.Constant) and all(
                self._interpolated_ok(a, func, seen) for a in args
            )
        if isinstance(node, ast.IfExp):
            return self.is_safe(node.body, func, seen) and self.is_safe(node.orelse, func, seen)
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if isinstance(node.func, ast.Attribute) and name == "format":
                return isinstance(node.func.value, ast.Constant) and all(
                    self._interpolated_ok(a, func, seen)
                    for a in (*node.args, *(k.value for k in node.keywords))
                )
            if name == "from_markup":  # Text.from_markup parses its argument
                return all(self.is_safe(a, func, seen) for a in node.args)
            if isinstance(node.func, ast.Name) and name in _TEXT_CALLS | _RENDERABLES:
                return True
            if (
                isinstance(node.func, ast.Attribute)
                and name in _TEXT_METHODS
                and _call_name(node.func.value) == "Text"
            ):
                return True
            if name == "str" and isinstance(node.func, ast.Name) and len(node.args) == 1:
                return self.is_int(node.args[0], func, seen)
            return False
        if isinstance(node, ast.Name) and node.id not in seen:
            values = self._values_of(node.id, func)
            return all(v is not None and self.is_safe(v, func, seen | {node.id}) for v in values)
        return False

    # -- sinks -----------------------------------------------------------

    def _is_console(
        self, node: ast.expr, func: ast.FunctionDef | None, seen: frozenset[str]
    ) -> bool:
        if isinstance(node, ast.Call):
            return _call_name(node.func) == "Console"
        if isinstance(node, ast.IfExp):
            return self._is_console(node.body, func, seen) and self._is_console(
                node.orelse, func, seen
            )
        if isinstance(node, ast.Name) and node.id not in seen:
            values = self._values_of(node.id, func)
            return all(
                v is not None and self._is_console(v, func, seen | {node.id}) for v in values
            )
        return False

    def _sink_args(
        self, call: ast.Call, func: ast.FunctionDef | None
    ) -> tuple[str, list[ast.expr]] | None:
        name = _call_name(call.func)
        if name is None:
            return None
        if any(
            k.arg == "markup" and isinstance(k.value, ast.Constant) and k.value.value is False
            for k in call.keywords
        ):
            return None
        if isinstance(call.func, ast.Attribute):
            if name in _CONSOLE_SINKS and (
                name == "print" or self._is_console(call.func.value, func, frozenset())
            ):
                return name, list(call.args)
            if name in _TABLE_SINKS:
                return name, [*call.args, *(k.value for k in call.keywords if k.arg == "footer")]
            if name == "from_markup":
                return name, list(call.args)
            return None
        if name == "print" and self.rich_print:
            return name, list(call.args)
        if name in _RENDERABLE_SINKS:
            keywords = [k.value for k in call.keywords if k.arg in _MARKUP_KEYWORDS]
            return name, [*call.args, *keywords]
        return None

    def _functions(self) -> Iterator[tuple[ast.FunctionDef | None, list[ast.AST]]]:
        """Each checked function with the nodes it owns (nested ones excluded)."""
        if self.functions is None:
            module_nodes: list[ast.AST] = []
            for stmt in self.tree.body:
                if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    module_nodes.extend(ast.walk(stmt))
            yield None, module_nodes
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                if self.functions is not None and node.name not in self.functions:
                    continue
                yield node, list(ast.walk(node))

    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for func, nodes in self._functions():
            for node in nodes:
                if not isinstance(node, ast.Call):
                    continue
                sink = self._sink_args(node, func)
                if sink is None:
                    continue
                name, args = sink
                for arg in args:
                    if not self.is_safe(arg, func, frozenset()):
                        out.append(Finding(self.path, arg.lineno, name, ast.unparse(arg)))
        return sorted(set(out), key=lambda f: (f.path, f.line, f.source))


def check_source(
    source: str, path: str = "<src>", functions: frozenset[str] | None = None
) -> list[Finding]:
    return _Checker(ast.parse(source), path, functions).findings()


def check_tree(root: Path) -> list[Finding]:
    found: list[Finding] = []
    for rel, functions in SCOPE.items():
        source = (root / rel).read_text(encoding="utf-8")
        checker = _Checker(ast.parse(source), rel, functions)
        if functions is not None:
            defined = {n.name for n in ast.walk(checker.tree) if isinstance(n, ast.FunctionDef)}
            assert functions <= defined, f"{rel}: {sorted(functions - defined)} not found"
        found.extend(checker.findings())
    return found


def test_cli_modules_hand_rich_no_data_as_markup() -> None:
    findings = check_tree(ROOT)
    assert findings == [], "\n".join(str(f) for f in findings)


# -- the checker itself: it reports what it must, and only that ---------------

_FLAGGED = [
    ('console = Console()\ndef f(x):\n    console.print(f"[red]failed[/red]: {x}")\n', ["print"]),
    ('console = Console()\ndef f(r):\n    console.print("id " + r.release_id)\n', ["print"]),
    ('console = Console()\ndef f(x):\n    console.print("[b]%s[/b]" % x)\n', ["print"]),
    ('console = Console()\ndef f(x):\n    console.print("[b]{}[/b]".format(x))\n', ["print"]),
    ("console = Console()\ndef f(x):\n    console.print(x)\n", ["print"]),
    ("def f(table, r):\n    table.add_row(r.release_id, str(r.count))\n", ["add_row", "add_row"]),
    ('def f(rows):\n    for r in rows:\n        msg = f"{r}"\n        c.print(msg)\n', ["print"]),
    ('def f(x):\n    t = Table(title=f"releases for {x}")\n', ["Table"]),
    ('def f(x):\n    c.print(Text.from_markup(f"[b]{x}[/b]"))\n', ["print", "from_markup"]),
    ("errs = Console(stderr=True)\ndef f(x):\n    errs.log(x)\n", ["log"]),
    ('from rich import print\ndef f(x):\n    print(f"{x}")\n', ["print"]),
    ("def f(x):\n    c.print(str(x))\n", ["print"]),
    ("def f(x):\n    c.print(Columns([x]))\n", ["print"]),
    ("def f(x):\n    c.print(Group(x))\n", ["print"]),
]

_CLEAN = [
    'def f(x):\n    c.print("[green]OK[/green] done")\n',
    'def f(xs):\n    c.print(f"[red]{len(xs)} failed[/red]")\n',
    'def f(xs):\n    n = len(xs) + 1\n    c.print(f"[y]{n}[/y] item(s)")\n',
    'def f(x):\n    c.print(Text.assemble(("failed", "red"), f": {x}"))\n',
    'def f(x):\n    c.print(Text(f"  + {x}", style="green"))\n',
    "def f(x):\n    c.print(_data(x))\n",
    'def f(x):\n    c.print(f"[b]{escape(x)}[/b]")\n',
    "def f(x):\n    c.print(x, markup=False)\n",
    'def f(table, r):\n    table.add_row(Text(r.release_id), str(len(r.items)), "-")\n',
    'def f(rows):\n    t = Table(title=f"{len(rows)} shown")\n    c.print(t)\n',
    "def f(x):\n    print(x)\n",  # builtin print: Rich is not involved
    "def f(x):\n    logger.log(10, x)\n",  # not a Console
    "def f(x):\n    c.print_json(data=x)\n",
]


@pytest.mark.parametrize(("source", "sinks"), _FLAGGED)
def test_checker_reports_data_reaching_a_markup_sink(source: str, sinks: list[str]) -> None:
    findings = check_source(source)
    assert sorted(f.sink for f in findings) == sorted(sinks), findings


@pytest.mark.parametrize("source", _CLEAN)
def test_checker_accepts_literal_markup_ints_and_text(source: str) -> None:
    assert check_source(source) == []


def test_checker_scope_limits_to_named_functions() -> None:
    source = textwrap.dedent(
        """
        c = Console()
        def checked(x):
            c.print(f"{x}")
        def other(x):
            c.print(f"{x}")
        """
    )
    findings = check_source(source, functions=frozenset({"checked"}))
    assert [f.line for f in findings] == [4]
