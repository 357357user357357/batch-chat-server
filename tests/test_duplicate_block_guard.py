"""Regression tests for the duplicate-message flood (Sep 2026).

A buggy client release pushed whole dialog lists with the same dialog
repeated dozens of times; the server appended every copy (32 identical
Q/A pairs in one conversation on production). Covers:

- collapse_repeated_blocks — the ingest flood guard,
- /api/sync/push storing one copy instead of the flood,
- /api/import/phone skipping the flood,
- scripts/cleanup_duplicate_blocks.py tombstoning already-stored copies
  so stale device pushes cannot resurrect them.
"""

import os
import subprocess
import sys

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Conversation, Message, MessageTombstone  # noqa: E402
from app.services.phone_sync import collapse_repeated_blocks  # noqa: E402

client = TestClient(app)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "cleanup_duplicate_blocks.py")
DB_PATH = os.environ["DATABASE_URL"]
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH[len("sqlite:///"):]


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {login()}"}


def _pair(q: str = "Historical setup", a: str = "Dehn's Lemma") -> list[dict]:
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


# ---------------------------------------------------------------------------
# collapse_repeated_blocks (the ingest flood guard)
# ---------------------------------------------------------------------------

def test_collapse_keeps_single_pair():
    assert collapse_repeated_blocks(_pair()) == _pair()


def test_collapse_drops_flood_tail():
    msgs = [{"role": "system", "content": "be brief"}] + _pair() * 32
    assert collapse_repeated_blocks(msgs) == [{"role": "system", "content": "be brief"}] + _pair()


def test_collapse_keeps_two_identical_repeats():
    # A user may legitimately send the exact same question twice.
    msgs = _pair() * 2
    assert collapse_repeated_blocks(msgs) == msgs


def test_collapse_requires_full_periodicity():
    msgs = _pair() + [{"role": "user", "content": "and a follow-up"}]
    assert collapse_repeated_blocks(msgs) == msgs


def test_collapse_identical_message_flood():
    msgs = [{"role": "user", "content": "test"}] * 5
    assert collapse_repeated_blocks(msgs) == [{"role": "user", "content": "test"}]


def test_collapse_empty_and_small_lists():
    assert collapse_repeated_blocks([]) == []
    assert collapse_repeated_blocks(_pair() * 2 + _pair("q3")) == _pair() * 2 + _pair("q3")


# ---------------------------------------------------------------------------
# /api/sync/push and /api/import/phone must not store floods
# ---------------------------------------------------------------------------

def test_sync_push_collapses_repeated_block_flood():
    headers = auth_headers()
    ext = "flood-dialog-1"
    body = {
        "dialogs": [{
            "id": ext,
            "title": "Flood",
            "model": "openrouter/test-model",
            "messages": _pair() * 32,
        }],
        "batches": [],
        "deleted_external_ids": [],
    }
    resp = client.post("/api/sync/push", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    conv = next(c for c in pulled if c["external_id"] == ext)
    assert [m["content"] for m in conv["messages"]] == ["Historical setup", "Dehn's Lemma"]

    # A stale device pushing the same flood again must not append anything.
    resp = client.post("/api/sync/push", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1
    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    conv = next(c for c in pulled if c["external_id"] == ext)
    assert len(conv["messages"]) == 2


def test_phone_import_collapses_repeated_block_flood():
    headers = auth_headers()
    resp = client.post("/api/import/phone", headers=headers, json={
        "dialogs": [{
            "id": "flood-import-1",
            "title": "Flood import",
            "model": "openrouter/test-model",
            "messages": _pair() * 32,
        }],
        "batches": [],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["messages_created"] == 2


# ---------------------------------------------------------------------------
# scripts/cleanup_duplicate_blocks.py (repairs already-stored floods)
# ---------------------------------------------------------------------------

def _seed_flood(external_id: str, copies: int) -> int:
    """Insert a stored flood the old buggy way (bypassing the ingest guard)."""
    db = SessionLocal()
    conv = Conversation(external_id=external_id, kind="chat", title="Dup flood")
    db.add(conv)
    db.flush()
    for _ in range(copies):
        for m in _pair():
            db.add(Message(conversation_id=conv.id, role=m["role"], content=m["content"]))
    db.commit()
    conv_id = conv.id
    db.close()
    return conv_id


def _live_contents(conversation_id: int) -> list[str]:
    db = SessionLocal()
    rows = db.scalars(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.deleted_at.is_(None),
        )
    ).all()
    db.close()
    return [m.content for m in rows]


def test_cleanup_script_tombstones_stored_flood_and_blocks_resurrection():
    conv_id = _seed_flood("cleanup-dup-1", 32)

    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", DB_PATH],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "conversation" in result.stdout

    # One copy of the pair survives; the rest are tombstoned (archived).
    assert _live_contents(conv_id) == ["Historical setup", "Dehn's Lemma"]
    db = SessionLocal()
    assert db.scalar(
        select(Message.id).where(
            Message.conversation_id == conv_id, Message.deleted_at.is_(None)
        ).limit(1)
    ) is not None
    tombstones = db.scalars(
        select(MessageTombstone).where(MessageTombstone.conversation_id == conv_id)
    ).all()
    assert len(tombstones) == 62
    db.close()

    # A stale device still carrying the flood cannot resurrect the copies:
    # the guard collapses the push to one pair, which already exists.
    headers = auth_headers()
    resp = client.post("/api/sync/push", headers=headers, json={
        "dialogs": [{
            "id": "cleanup-dup-1",
            "title": "Dup flood",
            "model": "openrouter/test-model",
            "messages": _pair() * 32,
        }],
        "batches": [],
        "deleted_external_ids": [],
    })
    assert resp.status_code == 200, resp.text
    assert _live_contents(conv_id) == ["Historical setup", "Dehn's Lemma"]


def test_cleanup_script_dry_run_changes_nothing():
    conv_id = _seed_flood("cleanup-dup-2", 6)

    result = subprocess.run(
        [sys.executable, SCRIPT, "--db", DB_PATH, "--dry-run"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _live_contents(conv_id) == (["Historical setup", "Dehn's Lemma"] * 6)
    assert "would clean" in result.stdout