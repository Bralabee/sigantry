"""sigantry_core.deploy.dependency - pre-flight dependency validator (DEPLOY-04).

fabric-cicd orders items internally using ``item_type_in_scope``. The toolkit
adds ONLY:
  1. Cycle detection across ``.platform`` + item-body ``$items.<Type>.<Name>``
     references (Kahn's algorithm).
  2. DOT-format artefact emission for CI debugging.

Does NOT reorder. Does NOT call ``publish_all_items``. Upstream owns ordering;
the toolkit catches cycles BEFORE upstream would produce a cryptic publish
error.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger("sigantry_core.deploy.dependency")

_DEFAULT_DOT_DIR = Path("./deploy-artefacts")

# Only these four item types get body-file reference extraction. Others are
# recorded as nodes (so the DOT is complete) but get no outgoing edges —
# extending to Warehouse / SemanticModel / Report is a post-Phase-4 task.
_CANONICAL_TYPES: frozenset[str] = frozenset(
    {"Lakehouse", "Environment", "Notebook", "DataPipeline"}
)

# Matches both $items.<Type>.<Name> and $items.<Type>.<Name>.$id forms.
_ITEM_REF_RE = re.compile(
    r"\$items\.(?P<type>[A-Za-z][A-Za-z0-9]*)"
    r"\.(?P<name>[A-Za-z0-9_\-]+)(?:\.\$id)?"
)

_BODY_SUFFIXES: frozenset[str] = frozenset({".py", ".json", ".yml", ".yaml", ".pbism"})

_Node = tuple[str, str]
_Edge = tuple[_Node, _Node]


class DependencyCycleError(RuntimeError):
    """Raised when the ``.platform`` + body-file graph contains a cycle."""


def validate_order(
    *,
    repository_directory: str | Path,
    item_type_in_scope: Iterable[str],
    dot_output_dir: str | Path = _DEFAULT_DOT_DIR,
) -> str:
    """Walk repo tree; detect cycles; emit DOT artefact.

    Args:
        repository_directory: Git working tree containing ``.platform`` items.
        item_type_in_scope: fabric-cicd's scope list (passed through — the
            toolkit doesn't filter nodes but records the set for traceability).
        dot_output_dir: Directory for the dep-graph DOT file. Created if
            absent.

    Returns:
        Path (str) to the written DOT file.

    Raises:
        DependencyCycleError: on any cycle.
    """
    repo = Path(repository_directory)
    # item_type_in_scope is retained for future node-filtering and to keep
    # the signature stable with deploy_workspace's call site.
    _types_in_scope = frozenset(item_type_in_scope)
    nodes, edges = _scan_tree(repo)
    _check_cycles(nodes, edges)
    dot_dir = Path(dot_output_dir)
    dot_dir.mkdir(parents=True, exist_ok=True)
    dot_path = dot_dir / "dep-graph.dot"
    _write_dot_atomic(dot_path, nodes, edges)
    logger.info(
        "dep_graph_emitted",
        extra={
            "event": "dep_graph_emitted",
            "nodes": len(nodes),
            "edges": len(edges),
            "dot_path": str(dot_path),
            "types_in_scope": sorted(_types_in_scope),
        },
    )
    return str(dot_path)


def _scan_tree(repo: Path) -> tuple[set[_Node], set[_Edge]]:
    """Return (nodes, edges); node = (type, name); edge = (src, dst)."""
    nodes: set[_Node] = set()
    edges: set[_Edge] = set()
    for platform_path in repo.rglob(".platform"):
        try:
            doc = json.loads(platform_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata = doc.get("metadata") or {}
        item_type = metadata.get("type")
        item_name = metadata.get("displayName")
        if not item_type or not item_name:
            continue
        this_node: _Node = (item_type, item_name)
        nodes.add(this_node)

        # Non-canonical types get a node but no body scan.
        # TODO(phase-4-plus): extend reference extraction to Warehouse /
        # SemanticModel / Report once their body schemas are pinned.
        if item_type not in _CANONICAL_TYPES:
            continue
        item_dir = platform_path.parent
        for body in item_dir.rglob("*"):
            if body.is_dir() or body == platform_path:
                continue
            if body.suffix not in _BODY_SUFFIXES:
                continue
            try:
                text = body.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _ITEM_REF_RE.finditer(text):
                target: _Node = (m.group("type"), m.group("name"))
                if target == this_node:
                    continue  # self-ref; ignored
                edges.add((this_node, target))
    return nodes, edges


def _check_cycles(nodes: set[_Node], edges: set[_Edge]) -> None:
    """Kahn's algorithm: residual nodes after topological sort indicate cycles."""
    in_deg: dict[_Node, int] = defaultdict(int)
    adj: dict[_Node, list[_Node]] = defaultdict(list)
    all_nodes: set[_Node] = set(nodes)
    for src, dst in edges:
        all_nodes.add(src)
        all_nodes.add(dst)
        adj[src].append(dst)
        in_deg[dst] += 1
    for n in all_nodes:
        in_deg.setdefault(n, 0)

    queue: deque[_Node] = deque(n for n in all_nodes if in_deg[n] == 0)
    visited: set[_Node] = set()
    while queue:
        n = queue.popleft()
        visited.add(n)
        for m in adj[n]:
            in_deg[m] -= 1
            if in_deg[m] == 0:
                queue.append(m)
    remaining = all_nodes - visited
    if remaining:
        raise DependencyCycleError(
            "Cycle detected among items: " + ", ".join(f"{t}/{n}" for (t, n) in sorted(remaining))
        )


def _write_dot_atomic(dot_path: Path, nodes: set[_Node], edges: set[_Edge]) -> None:
    """Write the DOT file atomically (temp + rename)."""
    lines: list[str] = ["digraph deploy {"]
    for t, n in sorted(nodes):
        lines.append(f'  "{t}/{n}";')
    for src, dst in sorted(edges):
        lines.append(f'  "{src[0]}/{src[1]}" -> "{dst[0]}/{dst[1]}";')
    lines.append("}")
    tmp = dot_path.with_suffix(".dot.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(dot_path)
