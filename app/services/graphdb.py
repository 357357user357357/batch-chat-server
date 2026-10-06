"""Tiny property-graph store with a Gremlin-flavored traversal DSL.

MIT. SQLite-backed (in-memory or file), stdlib-only — the "tinkerpop-style
traversal without Java" piece for flexchat.top. The analytics run on a
Rust CPU kernel (graphkern/, cugraph-style CSR, rayon-parallel, C ABI via
ctypes) when the shared library is present, with an equivalent pure-Python
path as fallback — same semantics either way, deterministically ordered:
PageRank (dangling mass redistributed), personalized PageRank (recommend-
ations), Brandes betweenness, weighted label-propagation communities,
directed BFS paths, degree centrality — comfortable at site scale
(10^3–10^5 nodes) either way.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import ctypes
import json
import os
import sqlite3
from array import array
from collections import Counter, defaultdict, deque

__all__ = ["Graph", "Traversal"]

# ------------------------------------------------------------ Rust kernel

_KERNEL = None
_KERNEL_TRIED = False


def _kernel():
    """The graphkern C ABI (lazy, cached), or None when unavailable."""
    global _KERNEL, _KERNEL_TRIED
    if _KERNEL_TRIED:
        return _KERNEL
    _KERNEL_TRIED = True
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("GRAPHKERN_PATH"),
        os.path.join(here, "libgraphkern.so"),
        os.path.join(here, "..", "..", "graphkern", "target", "release", "libgraphkern.so"),
        "/app/libgraphkern.so",
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            lib = ctypes.CDLL(path)
            U, D = ctypes.c_uint, ctypes.c_double
            SZ = ctypes.c_size_t
            lib.gk_pagerank.argtypes = [SZ, ctypes.POINTER(U), SZ, ctypes.POINTER(U), SZ, D, U, D, ctypes.POINTER(D)]
            lib.gk_ppr.argtypes = [SZ, ctypes.POINTER(U), SZ, ctypes.POINTER(U), SZ, ctypes.POINTER(U), SZ, D, U, D, ctypes.POINTER(D)]
            lib.gk_betweenness.argtypes = [SZ, ctypes.POINTER(U), SZ, ctypes.POINTER(U), SZ, ctypes.POINTER(D)]
            lib.gk_pagerank.restype = lib.gk_ppr.restype = lib.gk_betweenness.restype = ctypes.c_int
            _KERNEL = lib
            break
        except OSError:
            continue
    return _KERNEL

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


def kernel_name() -> str:
    """'rust' when the compiled kernel is loaded, else 'python'."""
    return "rust" if _kernel() is not None else "python"


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

    def _csr(self, dedup: bool = False):
        """(nodes, offsets, targets) — out-CSR over the edge table, node
        order sorted. With dedup=True parallel (src,dst) pairs collapse
        (BFS semantics); otherwise multiplicities are kept (weight-like
        semantics, matching the historical Python PageRank)."""
        nodes = self._node_ids()
        idx = {node: i for i, node in enumerate(nodes)}
        adj: list[list[int]] = [[] for _ in nodes]
        for row in self._edge_rows():
            s, d = idx.get(row["src"]), idx.get(row["dst"])
            if s is not None and d is not None:
                adj[s].append(d)
        if dedup:
            adj = [sorted(set(a)) for a in adj]
        offsets = array("I", [0])
        targets = array("I")
        for a in adj:
            targets.extend(a)
            offsets.append(len(targets))
        return nodes, offsets, targets

    @staticmethod
    def _cbuf(a: array):
        t = ctypes.c_uint if a.typecode == "I" else ctypes.c_double
        return (t * len(a)).from_buffer(a) if len(a) else None

    def pagerank(self, damping: float = 0.85, iterations: int = 50, tol: float = 1e-10):
        """Power-iteration PageRank, dangling mass redistributed."""
        nodes, offsets, targets = self._csr()
        if not nodes:
            return {}
        lib = _kernel()
        if lib is not None:
            try:
                ob = array("d", bytes(8 * len(nodes)))
                rc = lib.gk_pagerank(
                    len(nodes), self._cbuf(offsets), len(offsets),
                    self._cbuf(targets), len(targets),
                    damping, max(1, iterations), tol, self._cbuf(ob),
                )
                if rc == 0:
                    return dict(zip(nodes, ob))
            except Exception:
                pass  # fall through to the pure-Python path
        n = len(nodes)
        out_adj = [list(targets[offsets[i] : offsets[i + 1]]) for i in range(n)]
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

    def personalized_pagerank(
        self,
        seeds,
        damping: float = 0.85,
        iterations: int = 50,
        tol: float = 1e-10,
    ):
        """PPR from a seed set: teleport mass spread uniformly over the
        seeds, dangling mass returned to the seed distribution. Scores sum
        to 1 (when any seed exists in the graph)."""
        nodes, offsets, targets = self._csr()
        idx = {node: i for i, node in enumerate(nodes)}
        seed_idx = sorted({idx[s] for s in seeds if s in idx})
        if not nodes or not seed_idx:
            return {}
        lib = _kernel()
        if lib is not None:
            try:
                sb = array("I", seed_idx)
                ob = array("d", bytes(8 * len(nodes)))
                rc = lib.gk_ppr(
                    len(nodes), self._cbuf(offsets), len(offsets),
                    self._cbuf(targets), len(targets),
                    self._cbuf(sb), len(seed_idx),
                    damping, max(1, iterations), tol, self._cbuf(ob),
                )
                if rc == 0:
                    return dict(zip(nodes, ob))
            except Exception:
                pass
        n = len(nodes)
        out_adj = [list(targets[offsets[i] : offsets[i + 1]]) for i in range(n)]
        teleport = [0.0] * n
        for i in seed_idx:
            teleport[i] += 1.0 / len(seed_idx)
        dangling_seed = sum(teleport[i] for i in range(n) if not out_adj[i])
        p = teleport[:]
        for _ in range(iterations):
            dangling = sum(p[i] for i in range(n) if not out_adj[i])
            new = [
                (1 - damping) * teleport[i]
                + damping * dangling_seed * teleport[i]
                + damping * dangling * teleport[i]
                for i in range(n)
            ]
            for s, outs in enumerate(out_adj):
                if outs:
                    share = damping * p[s] / len(outs)
                    for d in outs:
                        new[d] += share
            delta = sum(abs(new[i] - p[i]) for i in range(n))
            p = new
            if delta < tol:
                break
        return {nodes[i]: p[i] for i in range(n)}

    def related(self, node_id: str, k: int = 8) -> list[str]:
        """Nodes most associated with node_id by personalized PageRank,
        excluding the node itself — recommendation-style 'more like this'."""
        scores = self.personalized_pagerank([node_id])
        scores.pop(node_id, None)
        return [n for n, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:max(0, k)]]

    def betweenness_centrality(self) -> dict[str, float]:
        """Raw (unnormalized) Brandes betweenness on the directed deduped
        graph — how often a node sits on shortest paths between others."""
        nodes, offsets, targets = self._csr(dedup=True)
        if not nodes:
            return {}
        lib = _kernel()
        if lib is not None:
            try:
                ob = array("d", bytes(8 * len(nodes)))
                rc = lib.gk_betweenness(
                    len(nodes), self._cbuf(offsets), len(offsets),
                    self._cbuf(targets), len(targets), self._cbuf(ob),
                )
                if rc == 0:
                    return dict(zip(nodes, ob))
            except Exception:
                pass
        # Pure-Python Brandes fallback.
        n = len(nodes)
        out_adj = [sorted(set(targets[offsets[i] : offsets[i + 1]])) for i in range(n)]
        cb = [0.0] * n
        for s in range(n):
            dist = [-1] * n
            sigma = [0.0] * n
            delta = [0.0] * n
            pred: list[list[int]] = [[] for _ in range(n)]
            dist[s] = 0
            sigma[s] = 1.0
            order = [s]
            queue = deque([s])
            while queue:
                u = queue.popleft()
                for v in out_adj[u]:
                    if dist[v] < 0:
                        dist[v] = dist[u] + 1
                        queue.append(v)
                        order.append(v)
                    if dist[v] == dist[u] + 1:
                        sigma[v] += sigma[u]
                        pred[v].append(u)
            for w in reversed(order):
                for v in pred[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != s:
                    cb[w] += delta[w]
        return {nodes[i]: cb[i] for i in range(n)}

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
