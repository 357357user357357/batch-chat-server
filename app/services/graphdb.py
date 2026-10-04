"""Tiny property-graph store with a Gremlin-flavored traversal DSL.

MIT. SQLite-backed (in-memory or file), stdlib-only — the "tinkerpop-style
traversal without Java" piece for flexchat.top. The analytics (PageRank,
weighted label-propagation communities, directed BFS paths, degree
centrality) are pure Python and comfortable at site scale (10^3–10^5 nodes);
if a graph outgrows that, python-graphblas (Apache-2.0) can back the same
methods later without changing this API.

Node identity is a caller-supplied string id. Edges are directed, kinded,
and carry an optional weight (the community detector reads it). The
traversal follows set semantics (each step de-duplicates, order preserved).

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict, deque

__all__ = ["Graph", "Traversal"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    props TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS graph_edges (
    src TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    dst TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_kind ON graph_nodes(kind);
CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON graph_edges(src);
CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON graph_edges(dst);
"""


class Traversal:
    """A lazy-ish chain of node ids; every step returns a new Traversal."""

    def __init__(self, graph: "Graph", ids):
        self._graph = graph
        self._ids = list(dict.fromkeys(ids))  # de-dup, first-seen order

    def __iter__(self):
        return iter(self._ids)

    def to_list(self) -> list[str]:
        return list(self._ids)

    def count(self) -> int:
        return len(self._ids)

    def has(self, *, label=None, **props) -> "Traversal":
        out = []
        for node_id in self._ids:
            node = self._graph.node(node_id)
            if node is None:
                continue
            if label is not None and node["label"] != label:
                continue
            if any(node["props"].get(k) != v for k, v in props.items()):
                continue
            out.append(node_id)
        return Traversal(self._graph, out)

    def out(self, kind: str | None = None) -> "Traversal":
        return Traversal(self._graph, self._graph._adjacent(self._ids, kind, "out"))

    def inn(self, kind: str | None = None) -> "Traversal":
        return Traversal(self._graph, self._graph._adjacent(self._ids, kind, "in"))

    def both(self, kind: str | None = None) -> "Traversal":
        return Traversal(self._graph, self._graph._adjacent(self._ids, kind, "both"))

    def limit(self, n: int) -> "Traversal":
        return Traversal(self._graph, self._ids[:n])

    def values(self, prop: str = "id"):
        for node_id in self._ids:
            node = self._graph.node(node_id)
            if node is None:
                continue
            if prop == "id":
                yield node_id
            elif prop == "label":
                yield node["label"]
            elif prop == "kind":
                yield node["kind"]
            else:
                yield node["props"].get(prop)


class Graph:
    """SQLite-backed property graph with traversal + analytics."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(_SCHEMA)

    @classmethod
    def open(cls, path: str = ":memory:") -> "Graph":
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return cls(conn)

    # ------------------------------------------------------------ mutation

    def add_node(self, node_id: str, kind: str, label: str, **props) -> None:
        self.conn.execute(
            "INSERT INTO graph_nodes (id, kind, label, props) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, label=excluded.label, "
            "props=excluded.props",
            (node_id, kind, label, json.dumps(props, sort_keys=True)),
        )

    def add_edge(self, src: str, dst: str, kind: str, weight: float = 1.0) -> None:
        self.conn.execute(
            "INSERT INTO graph_edges (src, dst, kind, weight) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(src, dst, kind) DO UPDATE SET weight=excluded.weight",
            (src, dst, kind, weight),
        )

    def commit(self) -> None:
        self.conn.commit()

    # ---------------------------------------------------------------- reads

    def node(self, node_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM graph_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "label": row["label"],
            "props": json.loads(row["props"]),
        }

    def v(self, kind: str | None = None, id: str | None = None) -> Traversal:
        """Start a traversal: all nodes, one kind, or a single node id."""
        if id is not None:
            return Traversal(self, [id] if self.node(id) else [])
        if kind is None:
            rows = self.conn.execute("SELECT id FROM graph_nodes ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id FROM graph_nodes WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        return Traversal(self, [r["id"] for r in rows])

    def node_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]

    def edge_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

    def _adjacent(self, ids, kind, direction) -> list[str]:
        out: list[str] = []
        for node_id in ids:
            if direction in ("out", "both"):
                sql = "SELECT dst FROM graph_edges WHERE src = ?"
                args: tuple = (node_id,)
                if kind:
                    sql += " AND kind = ?"
                    args = (node_id, kind)
                out += [r["dst"] for r in self.conn.execute(sql, args).fetchall()]
            if direction in ("in", "both"):
                sql = "SELECT src FROM graph_edges WHERE dst = ?"
                args = (node_id,)
                if kind:
                    sql += " AND kind = ?"
                    args = (node_id, kind)
                out += [r["src"] for r in self.conn.execute(sql, args).fetchall()]
        return out

    def _node_ids(self) -> list[str]:
        return [
            r["id"]
            for r in self.conn.execute("SELECT id FROM graph_nodes ORDER BY id")
        ]

    def _edge_rows(self):
        return self.conn.execute(
            "SELECT src, dst, kind, weight FROM graph_edges"
        ).fetchall()

    # ------------------------------------------------------------ analytics

    def pagerank(self, damping: float = 0.85, iterations: int = 50, tol: float = 1e-10):
        """Power-iteration PageRank, dangling mass redistributed."""
        nodes = self._node_ids()
        if not nodes:
            return {}
        idx = {node: i for i, node in enumerate(nodes)}
        n = len(nodes)
        out_adj: list[list[int]] = [[] for _ in range(n)]
        for row in self._edge_rows():
            s, d = idx.get(row["src"]), idx.get(row["dst"])
            if s is not None and d is not None:
                out_adj[s].append(d)
        ranks = [1.0 / n] * n
        for _ in range(iterations):
            dangling = sum(ranks[i] for i in range(n) if not out_adj[i])
            base = (1 - damping) / n + damping * dangling / n
            new = [base] * n
            for s, outs in enumerate(out_adj):
                if outs:
                    share = damping * ranks[s] / len(outs)
                    for d in outs:
                        new[d] += share
            delta = sum(abs(new[i] - ranks[i]) for i in range(n))
            ranks = new
            if delta < tol:
                break
        return {nodes[i]: ranks[i] for i in range(n)}

    def communities(self, iterations: int = 8) -> dict[str, str]:
        """Weighted label propagation on the undirected view (deterministic:
        async updates in sorted node order, ties broken to the lexicographically
        smallest label). Returns {node_id: community_label}."""
        nodes = self._node_ids()
        adj: dict[str, dict[str, float]] = defaultdict(dict)
        for row in self._edge_rows():
            s, d, w = row["src"], row["dst"], row["weight"]
            adj[s][d] = adj[s].get(d, 0.0) + w
            adj[d][s] = adj[d].get(s, 0.0) + w
        labels = {node: node for node in nodes}
        for _ in range(iterations):
            changed = False
            for node in nodes:
                neighbors = adj.get(node)
                if not neighbors:
                    continue
                scores: Counter = Counter()
                for neighbor, weight in neighbors.items():
                    scores[labels[neighbor]] += weight
                best = max(scores.values())
                candidate = min(l for l, sc in scores.items() if sc == best)
                if candidate != labels[node]:
                    labels[node] = candidate
                    changed = True
            if not changed:
                break
        return labels

    def shortest_path(self, src: str, dst: str, kind: str | None = None):
        """Directed BFS shortest path as [src, ..., dst], or None."""
        if src == dst:
            return [src]
        prev = {src: None}
        queue = deque([src])
        while queue:
            current = queue.popleft()
            for nxt in self._adjacent([current], kind, "out"):
                if nxt in prev:
                    continue
                prev[nxt] = current
                if nxt == dst:
                    path = [dst]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                queue.append(nxt)
        return None

    def degree_centrality(self) -> dict[str, float]:
        nodes = self._node_ids()
        n = len(nodes)
        if n <= 1:
            return {node: 0.0 for node in nodes}
        deg: Counter = Counter()
        for row in self._edge_rows():
            deg[row["src"]] += 1
            deg[row["dst"]] += 1
        return {node: deg.get(node, 0) / (n - 1) for node in nodes}

    # ----------------------------------------------------------- export

    def to_json(self, node_ids=None) -> dict:
        """{'nodes': [...], 'edges': [...]} — all of it, or the induced
        subgraph on the given node ids (edges kept only inside the set)."""
        keep = None if node_ids is None else set(node_ids)
        nodes = []
        for row in self.conn.execute(
            "SELECT * FROM graph_nodes ORDER BY id"
        ).fetchall():
            if keep is not None and row["id"] not in keep:
                continue
            nodes.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "label": row["label"],
                    "props": json.loads(row["props"]),
                }
            )
        ids = {n["id"] for n in nodes}
        edges = []
        for row in self.conn.execute(
            "SELECT src, dst, kind, weight FROM graph_edges ORDER BY src, dst, kind"
        ).fetchall():
            if row["src"] in ids and row["dst"] in ids:
                edges.append(
                    {
                        "src": row["src"],
                        "dst": row["dst"],
                        "kind": row["kind"],
                        "weight": row["weight"],
                    }
                )
        return {"nodes": nodes, "edges": edges}
