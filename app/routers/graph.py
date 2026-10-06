"""Knowledge-graph API: the account's conversations as a property graph.

Derives a Graph (nodes: conversation / model / topic; edges: uses / mentions,
with hub nodes as the viz spine) from the account's live conversations and
serves it through the tiny engine in app/services/graphdb.py — abi-style
graphs on flexchat.top with a Gremlin-flavored traversal underneath, and no
Java anywhere.

The graph is derived read-only per request (cheap at site scale, bounded by
caps) and never mutates the conversation tables.
"""

from __future__ import annotations

import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Conversation, Message
from app.security import get_account_id
from app.services.graphdb import Graph, kernel_name

router = APIRouter(prefix="/api/graph", tags=["graph"])

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")

_STOPWORDS = frozenset(
    """
    a an and are as at be been but by can could did do does for from had has
    have how i in into is it its just like may me might must my no not of on
    or our out over said say says she should so some such than that the their
    them then there these they this those to too under up was we were what
    when where which while who why will with would you your about after
    before between during please thanks thank ok okay hey hi hello yeah yes
    tell give make want need know think good great really very much many
    also more most other
    """.split()
)

_MAX_CONVERSATIONS = 300
_MAX_MESSAGES = 40
_TOPICS_PER_CONVERSATION = 6
_TOPICS_OVERALL = 40


def _topics_for(db: Session, conv: Conversation) -> Counter:
    """Keyword weights for one conversation: title x3 + recent user text x1."""
    words: Counter = Counter()
    if conv.title:
        for w in _WORD_RE.findall(conv.title.lower()):
            if w not in _STOPWORDS:
                words[w] += 3
    rows = db.execute(
        select(Message.content)
        .where(Message.conversation_id == conv.id, Message.role == "user")
        .order_by(Message.id.desc())
        .limit(_MAX_MESSAGES)
    ).scalars()
    for content in rows:
        for w in _WORD_RE.findall((content or "").lower()[:4000]):
            if w not in _STOPWORDS:
                words[w] += 1
    return words


def build_account_graph(db: Session, account_id: str) -> Graph:
    """The account's conversation knowledge graph (fresh, read-only)."""
    g = Graph.open()
    convs = (
        db.execute(
            select(Conversation)
            .where(
                Conversation.account_id == account_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(_MAX_CONVERSATIONS)
        )
        .scalars()
        .all()
    )

    conv_topics: dict[int, Counter] = {}
    topic_total: Counter = Counter()
    for conv in convs:
        words = _topics_for(db, conv)
        conv_topics[conv.id] = words
        topic_total.update(words)
    keep = {w for w, _ in topic_total.most_common(_TOPICS_OVERALL)}

    # Hub nodes give the visualization a spine (abi-ontology-flavored).
    g.add_node("hub:topics", "hub", "Topics")
    g.add_node("hub:models", "hub", "Models")

    for conv in convs:
        node_id = f"conv:{conv.id}"
        g.add_node(
            node_id,
            "conversation",
            conv.title or f"Conversation {conv.id}",
            kind_flag=conv.kind,
            updated=str(conv.updated_at) if conv.updated_at else None,
        )
        if conv.model:
            model_id = f"model:{conv.model}"
            g.add_node(model_id, "model", conv.model)
            g.add_edge(node_id, model_id, "uses", weight=1.0)
            g.add_edge(model_id, "hub:models", "in", weight=1.0)
        for word, count in conv_topics[conv.id].most_common(_TOPICS_PER_CONVERSATION):
            if word not in keep:
                continue
            topic_id = f"topic:{word}"
            g.add_node(topic_id, "topic", word, total=topic_total[word])
            g.add_edge(node_id, topic_id, "mentions", weight=float(count))
            g.add_edge(topic_id, "hub:topics", "in", weight=1.0)
    return g


@router.get("/overview")
def graph_overview(
    limit: int = Query(400, ge=1, le=1200),
    account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> dict:
    """The whole derived graph (bounded) plus PageRank + community labels."""
    g = build_account_graph(db, account_id)
    data = g.to_json()
    if len(data["nodes"]) > limit:
        data["nodes"] = data["nodes"][:limit]
        ids = {n["id"] for n in data["nodes"]}
        data["edges"] = [e for e in data["edges"] if e["src"] in ids and e["dst"] in ids]
    ranks = g.pagerank()
    top = sorted(ranks.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    data["top_nodes"] = [{"id": node_id, "score": round(score, 6)} for node_id, score in top]
    data["communities"] = g.communities()
    bt = g.betweenness_centrality()
    data["hubs"] = [
        {"id": node_id, "score": round(score, 6)}
        for node_id, score in sorted(bt.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        if score > 0
    ]
    data["kernel"] = kernel_name()
    data["stats"] = {"nodes": g.node_count(), "edges": g.edge_count()}
    return data


@router.get("/node/{node_id}")
def graph_node(
    node_id: str,
    account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> dict:
    """Egocentric view: the node, its neighborhood, and traversal paths."""
    g = build_account_graph(db, account_id)
    if g.node(node_id) is None:
        raise HTTPException(status_code=404, detail="node not found in your graph")
    outgoing = g.v(id=node_id).out().limit(150).to_list()
    incoming = g.v(id=node_id).inn().limit(150).to_list()
    sub = g.to_json(set([node_id, *outgoing, *incoming]))
    sub["focus"] = node_id
    sub["out"] = outgoing
    sub["in"] = incoming
    sub["betweenness"] = round(g.betweenness_centrality().get(node_id, 0.0), 6)
    related = g.related(node_id, 8)
    sub["related"] = [
        {"id": rid, "label": (g.node(rid) or {}).get("label", rid), "kind": (g.node(rid) or {}).get("kind", "")}
        for rid in related
    ]
    return sub
