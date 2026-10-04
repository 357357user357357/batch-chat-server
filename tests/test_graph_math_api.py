"""API checks for the graph + math capability endpoints."""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_graphmath.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import math_modular as mm  # noqa: E402

client = TestClient(app)


def auth_headers() -> dict:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# --------------------------------------------------------------------- math


def test_math_forms_metadata_is_public_but_hecke_is_gated():
    # Static discovery metadata is public, like /api/chat/models; the
    # compute endpoint stays behind auth like every user-data route.
    r = client.get("/api/math/forms")
    assert r.status_code == 200
    ids = {f["id"] for f in r.json()["forms"]}
    assert {"delta", "eisenstein"} <= ids
    assert client.post("/api/math/hecke", json={"form": "delta", "precision": 10, "n": 1}).status_code == 401


def test_hecke_delta_matches_module_exactly():
    headers = auth_headers()
    r = client.post(
        "/api/math/hecke", headers=headers,
        json={"form": "delta", "precision": 24, "n": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    expected = mm.hecke(mm.delta(24), 2).as_ints()
    assert body["coefficients"] == expected
    assert body["eigenvalue"] == -24  # tau(2)
    assert body["expected_eigenvalue"] is None  # no Eisenstein expectation for cusp forms


def test_hecke_eisenstein_eigenvalue_is_sigma():
    headers = auth_headers()
    r = client.post(
        "/api/math/hecke", headers=headers,
        json={"form": "eisenstein", "weight": 8, "precision": 20, "n": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["eigenvalue"] == mm.sigma(3, 7)  # 1 + 3^7
    assert body["is_eigen"] is True


def test_hecke_identity_n1():
    headers = auth_headers()
    body = client.post(
        "/api/math/hecke", headers=headers,
        json={"form": "delta", "precision": 16, "n": 1},
    ).json()
    assert body["coefficients"] == mm.delta(16).as_ints()
    assert body["eigenvalue"] == 1


def test_hecke_rejects_bad_input():
    headers = auth_headers()
    assert (
        client.post("/api/math/hecke", headers=headers,
                    json={"form": "eisenstein", "weight": 5, "precision": 10, "n": 1}).status_code
        == 422
    )
    assert (
        client.post("/api/math/hecke", headers=headers,
                    json={"form": "junk", "precision": 10, "n": 1}).status_code
        == 422
    )
    assert (
        client.post("/api/math/hecke", headers=headers,
                    json={"form": "delta", "precision": 10_000, "n": 1}).status_code
        == 422
    )


# -------------------------------------------------------------------- graph


def test_graph_overview_builds_from_conversations():
    headers = auth_headers()
    conv = client.post(
        "/api/conversations", headers=headers,
        json={"title": "Hecke operators and modular forms"},
    ).json()
    r = client.get("/api/graph/overview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {n["id"] for n in body["nodes"]}
    assert f"conv:{conv['id']}" in ids
    assert "topic:hecke" in ids  # title keyword became a topic node
    assert any(i.startswith("topic:") for i in ids)
    assert body["stats"]["nodes"] >= 3
    assert {"hub:topics", "hub:models"} <= ids  # viz spine
    assert isinstance(body["communities"], dict)
    assert len(body["top_nodes"]) >= 1


def test_graph_node_egocentric():
    headers = auth_headers()
    conv = client.post(
        "/api/conversations", headers=headers,
        json={"title": "Graph theory chat"},
    ).json()
    node_id = f"conv:{conv['id']}"
    r = client.get(f"/api/graph/node/{node_id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["focus"] == node_id
    labels = {n["label"] for n in body["nodes"]}
    assert "Graph theory chat" in labels
    # the conversation mentions at least one topic -> outgoing traversal works
    assert any(i.startswith("topic:") for i in body["out"])


def test_graph_node_404():
    r = client.get("/api/graph/node/topic:does-not-exist", headers=auth_headers())
    assert r.status_code == 404


def test_bernoulli_cache_is_bounded():
    """The bernoulli() lru_cache is capped (64 entries) so rare large-m
    requests cannot grow the process without bound; values stay correct."""
    assert mm.bernoulli.cache_parameters()["maxsize"] == 64
    assert mm.bernoulli(20) == mm.Fraction(-174611, 330)
