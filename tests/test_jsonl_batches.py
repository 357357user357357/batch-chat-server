"""Tests for the JSONL batch flow.

Runs the real FastAPI app against a tiny mock of the OpenRouter async Batch API
so the full submit → poll → conversation flow is exercised without a key.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Point the app at a throwaway DB and the mock server BEFORE importing it
PORT = 8891
os.environ["APP_PASSWORD"] = "test"
os.environ["OPENROUTER_API_KEY"] = "test-key"
os.environ["OPENROUTER_BASE_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["DATABASE_URL"] = "sqlite:////tmp/bc_test_batch.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.jsonl_batches import JsonlParseError, parse_jsonl  # noqa: E402

BATCH_RESPONSES: dict = {}
SUBMITTED: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        SUBMITTED["payload"] = body
        n = len(body.get("requests", []))
        batch_id = f"batch_{n}"
        BATCH_RESPONSES[batch_id] = {"requests": body.get("requests", []), "status": "validating"}
        resp = {
            "id": batch_id,
            "object": "batch",
            "endpoint": "/v1/chat/completions",
            "model": body.get("model"),
            "completion_window": "24h",
            "status": "validating",
            "created_at": 0,
            "finalized_at": None,
            "request_counts": {"total": n, "completed": 0, "failed": 0},
            "usage": None,
            "results": None,
            "error": None,
        }
        self._send(resp)

    def do_GET(self):
        batch_id = self.path.split("/")[-1]
        entry = BATCH_RESPONSES.get(batch_id, {})
        requests = entry.get("requests", [])
        results = []
        failed = 0
        for i, req in enumerate(requests):
            cid = req.get("custom_id", f"req-{i + 1}")
            if cid.startswith("fail"):
                failed += 1
                results.append({"id": f"res_{cid}", "custom_id": cid, "error": "simulated failure"})
                continue
            results.append({
                "id": f"res_{cid}",
                "custom_id": cid,
                "response": {
                    "status_code": 200,
                    "request_id": f"reqid_{cid}",
                    "body": {
                        "choices": [{
                            "message": {"role": "assistant", "content": f"Answer for {cid}"},
                            "finish_reason": "stop",
                        }]
                    },
                },
                "error": None,
            })
        completed = len(results) - failed
        resp = {
            "id": batch_id,
            "object": "batch",
            "endpoint": "/v1/chat/completions",
            "model": entry.get("model", "mock"),
            "completion_window": "24h",
            "status": "completed",
            "created_at": 0,
            "finalized_at": 1,
            "request_counts": {"total": len(requests), "completed": completed, "failed": failed},
            "usage": None,
            "results": results,
            "error": None,
        }
        self._send(resp)

    def _send(self, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


MOCK = HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=MOCK.serve_forever, daemon=True).start()

client = TestClient(app)


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ---------------------------------------------------------------------------
# JSONL parser
# ---------------------------------------------------------------------------

def test_openrouter_format():
    jsonl = (
        '{"custom_id": "r1", "body": {"messages": [{"role": "user", "content": "hi"}]}}\n'
        '{"custom_id": "r2", "messages": [{"role": "user", "content": "yo"}]}\n'
        '{"prompt": "short"}\n'
    )
    reqs = parse_jsonl(jsonl)
    assert reqs[0]["custom_id"] == "r1"
    assert reqs[0]["body"]["messages"][0]["content"] == "hi"
    assert reqs[1]["body"]["messages"][0]["content"] == "yo"
    assert reqs[2]["custom_id"].startswith("short-")


def test_vertex_format():
    jsonl = (
        '{"request": {"systemInstruction": {"parts": [{"text": "be brief"}]},'
        ' "contents": [{"role": "user", "parts": [{"text": "hello "}, {"text": "world"}]}]}}\n'
    )
    reqs = parse_jsonl(jsonl)
    body = reqs[0]["body"]["messages"]
    assert body[0] == {"role": "system", "content": "be brief"}
    assert body[1] == {"role": "user", "content": "hello \nworld"}


def test_bedrock_format():
    jsonl = (
        '{"recordId": "rec-1", "modelInput": {"system": "you are terse",'
        ' "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}}\n'
    )
    reqs = parse_jsonl(jsonl)
    body = reqs[0]["body"]["messages"]
    assert reqs[0]["custom_id"] == "rec-1"
    assert body[0] == {"role": "system", "content": "you are terse"}
    assert body[1] == {"role": "user", "content": "hi"}


def test_global_system_prepended():
    reqs = parse_jsonl('{"prompt": "q"}\n', system="GLOBAL")
    assert reqs[0]["body"]["messages"][0]["role"] == "system"
    assert reqs[0]["body"]["messages"][0]["content"] == "GLOBAL"


def test_bad_line_raises_with_number():
    try:
        parse_jsonl('{"prompt": "ok"}\n{"bad json\n')
        raise AssertionError("expected JsonlParseError")
    except JsonlParseError as exc:
        assert "Line 2" in str(exc)


# ---------------------------------------------------------------------------
# Full flow: submit → poll → conversation
# ---------------------------------------------------------------------------

def test_batch_flow():
    token = login()
    headers = {"Authorization": f"Bearer {token}"}

    jsonl = (
        '{"custom_id": "req-1", "prompt": "What is 2+2?"}\n'
        '{"request": {"contents": [{"role": "user", "parts": [{"text": "Write a haiku"}]}]}}\n'
        '{"custom_id": "fail-1", "prompt": "This one fails"}\n'
    )

    resp = client.post(
        "/api/batches",
        headers={**headers, "Content-Type": "application/json"},
        json={"model": "anthropic/claude-fable-5:batch", "jsonl": jsonl},
    )
    assert resp.status_code == 201, resp.text
    job = resp.json()
    job_id = job["id"]
    assert job["external_id"] == "batch_3"
    assert job["total_items"] == 3

    resp = client.get(f"/api/batches/{job_id}", headers=headers)
    assert resp.status_code == 200
    job = resp.json()
    assert job["status"] == "completed", job
    assert job["completed_items"] == 2
    assert job["failed_items"] == 1
    assert job["conversation_id"] is not None

    conv_id = job["conversation_id"]
    conv = client.get(f"/api/conversations/{conv_id}", headers=headers).json()
    assert conv["kind"] == "batch"
    assert conv["model"] == "anthropic/claude-fable-5:batch"
    contents = [(m["role"], m["content"]) for m in conv["messages"]]
    assert ("user", "What is 2+2?") in contents
    assert ("assistant", "Answer for req-1") in contents
    assert any("simulated failure" in m[1] for m in contents)

    convs = client.get("/api/conversations", headers=headers).json()
    assert any(c["id"] == conv_id and c["kind"] == "batch" for c in convs)

    assert client.delete(f"/api/batches/{job_id}", headers=headers).status_code == 204


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL_TESTS_PASSED")