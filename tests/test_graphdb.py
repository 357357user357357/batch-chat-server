"""Graph engine checks: traversal DSL, PageRank, communities, paths."""

import sqlite3

from app.services.graphdb import Graph


def build_sample() -> Graph:
    g = Graph.open()
    g.add_node("a", "person", "Alice", age=30)
    g.add_node("b", "person", "Bob", age=40)
    g.add_node("c", "person", "Carol", age=25)
    g.add_node("d", "post", "Hello")
    g.add_edge("a", "b", "knows")
    g.add_edge("b", "c", "knows")
    g.add_edge("c", "a", "knows")
    g.add_edge("a", "d", "wrote")
    g.add_edge("b", "d", "wrote")
    return g


def test_traversal_steps():
    g = build_sample()
    assert sorted(g.v(kind="person").values("id")) == ["a", "b", "c"]
    assert list(g.v(id="a").out("knows").values("id")) == ["b"]
    assert sorted(g.v(id="d").inn("wrote").values("id")) == ["a", "b"]
    # two hops: Alice -knows-> Bob -knows-> Carol
    assert list(g.v(id="a").out("knows").out("knows").values("id")) == ["c"]
    assert g.v(kind="person").count() == 3
    assert list(g.v(id="a").out("knows").out("knows").out("knows").values("id")) == ["a"]


def test_has_filters():
    g = build_sample()
    assert list(g.v(kind="person").has(label="Bob").values("id")) == ["b"]
    assert list(g.v(kind="person").has(age=25).values("id")) == ["c"]
    assert g.v(kind="person").has(label="Nobody").count() == 0


def test_node_crud_and_counts():
    g = build_sample()
    assert g.node_count() == 4
    assert g.edge_count() == 5
    assert g.node("a")["props"]["age"] == 30
    g.add_node("a", "person", "Alice", age=31)  # upsert
    assert g.node("a")["props"]["age"] == 31
    assert g.node_count() == 4
    assert g.node("missing") is None


def test_pagerank_finds_the_hub():
    g = Graph.open()
    g.add_node("x", "t", "x")
    g.add_node("hub", "t", "hub")
    for i in range(1, 5):
        g.add_node(f"h{i}", "t", f"h{i}")
        g.add_edge(f"h{i}", "hub", "l")
    g.add_edge("x", "h1", "l")
    ranks = g.pagerank()
    assert abs(sum(ranks.values()) - 1.0) < 1e-6
    assert max(ranks, key=ranks.get) == "hub"


def test_communities_two_weighted_cliques():
    g = Graph.open()
    for i in range(1, 7):
        g.add_node(f"p{i}", "p", f"P{i}")
    clique1 = [(1, 2), (1, 3), (2, 3)]
    clique2 = [(4, 5), (4, 6), (5, 6)]
    for x, y in clique1 + clique2:
        g.add_edge(f"p{x}", f"p{y}", "knows", weight=2.0)
        g.add_edge(f"p{y}", f"p{x}", "knows", weight=2.0)
    g.add_edge("p3", "p4", "knows", weight=1.0)  # single weak bridge
    g.add_edge("p4", "p3", "knows", weight=1.0)
    labels = g.communities()
    groups: dict[str, set] = {}
    for node, label in labels.items():
        groups.setdefault(label, set()).add(node)
    assert sorted(map(sorted, groups.values())) == [
        ["p1", "p2", "p3"],
        ["p4", "p5", "p6"],
    ]


def test_shortest_path_directed():
    g = build_sample()
    assert g.shortest_path("a", "c") == ["a", "b", "c"]
    assert g.shortest_path("a", "a") == ["a"]
    # c -> a -> d exists (Carol knows Alice, Alice wrote d)
    assert g.shortest_path("c", "d") == ["c", "a", "d"]
    assert g.shortest_path("d", "a") is None  # d has no outgoing edges


def test_degree_centrality():
    g = build_sample()
    deg = g.degree_centrality()
    assert deg["a"] == 1.0  # a->b, a->d, c->a: 3 of 3 possible
    assert abs(deg["d"] - 2 / 3) < 1e-9  # a->d, b->d only


def test_to_json_induced_subgraph():
    g = build_sample()
    sub = g.to_json(["a", "b", "d"])
    assert {n["id"] for n in sub["nodes"]} == {"a", "b", "d"}
    # the c-edges drop out; a->b, a->d, b->d remain
    assert {(e["src"], e["dst"]) for e in sub["edges"]} == {("a", "b"), ("a", "d"), ("b", "d")}


def test_roundtrip_file_db(tmp_path):
    path = str(tmp_path / "graph.sqlite")
    g = Graph.open(path)
    g.add_node("a", "person", "Alice")
    g.add_node("b", "person", "Bob")
    g.add_edge("a", "b", "knows")
    g.commit()
    g2 = Graph.open(path)
    assert g2.node_count() == 2
    assert list(g2.v(id="a").out("knows").values("id")) == ["b"]


# ---------------------------------------------------------------- kernel

def _force_python(monkeypatch):
    import app.services.graphdb as gmod
    monkeypatch.setattr(gmod, "_KERNEL", None)
    monkeypatch.setattr(gmod, "_KERNEL_TRIED", True)


def _kernel_available() -> bool:
    import app.services.graphdb as gmod
    return gmod._kernel() is not None


def test_betweenness_known_values_python(monkeypatch):
    _force_python(monkeypatch)
    g = Graph.open()
    for nid in ("0", "1", "2", "3"):
        g.add_node(nid, "x", nid)
    for s, d in (("0", "1"), ("1", "2"), ("2", "3")):
        g.add_edge(s, d, "e")
    bt = g.betweenness_centrality()
    assert bt == {"0": 0.0, "1": 2.0, "2": 2.0, "3": 0.0}


def test_betweenness_kernel_matches_python(monkeypatch):
    if not _kernel_available():
        import pytest
        pytest.skip("libgraphkern.so not built")
    g = Graph.open()
    for nid in ("a", "b", "c", "d", "e", "f"):
        g.add_node(nid, "x", nid)
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("b", "d"), ("d", "e"),
             ("e", "b"), ("a", "f"), ("f", "c")]
    for s, d in edges:
        g.add_edge(s, d, "e")
    _force_python(monkeypatch)
    py_bt = g.betweenness_centrality()
    py_pr = g.pagerank()
    py_ppr = g.personalized_pagerank(["a"])
    monkeypatch.undo()
    import app.services.graphdb as gmod
    assert gmod.kernel_name() == "rust"
    for name, got, want in (
        ("betweenness", g.betweenness_centrality(), py_bt),
        ("pagerank", g.pagerank(), py_pr),
        ("ppr", g.personalized_pagerank(["a"]), py_ppr),
    ):
        for k in want:
            assert abs(got[k] - want[k]) < 1e-9, (name, k, got[k], want[k])


def test_ppr_mass_and_related(monkeypatch):
    g = Graph.open()
    for nid in ("a", "b", "c", "d"):
        g.add_node(nid, "x", nid)
    for s, d in (("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "b")):
        g.add_edge(s, d, "e")
    scores = g.personalized_pagerank(["a"])
    assert abs(sum(scores.values()) - 1.0) < 1e-9
    # b is the confluence: it takes flow from a AND from the d-cycle back-edge,
    # so PPR legitimately ranks it above the seed itself.
    assert scores["b"] == max(scores.values())
    assert scores["b"] > scores["a"] > scores["c"] > scores["d"]
    rel = g.related("a", k=2)
    assert rel == ["b", "c"]
    assert "a" not in g.related("a")
    assert g.personalized_pagerank(["missing"]) == {}
    assert g.related("missing") == []


def test_pagerank_kernel_matches_python_dangling(monkeypatch):
    if not _kernel_available():
        import pytest
        pytest.skip("libgraphkern.so not built")
    g = Graph.open()
    for nid in ("a", "b", "c", "hub"):
        g.add_node(nid, "x", nid)
    for s, d in (("a", "b"), ("a", "c"), ("b", "hub"), ("c", "hub")):
        g.add_edge(s, d, "e")  # hub is dangling
    _force_python(monkeypatch)
    py = g.pagerank()
    monkeypatch.undo()
    rk = g.pagerank()
    assert abs(sum(rk.values()) - 1.0) < 1e-9
    for k in py:
        assert abs(rk[k] - py[k]) < 1e-9, k
