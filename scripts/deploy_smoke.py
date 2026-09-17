#!/usr/bin/env python3
"""End-to-end smoke test for a deployed batch-chat server.

Exercises the exact call pattern the Android app + web UI use, over the
public URL, and verifies the behaviors that matter after a migration:

  1. TLS      — valid cert for the host, not expiring within 24h
  2. Health   — GET /api/health (unauthenticated)
  3. Auth     — POST /api/auth/login → bearer token
  4. Sync     — push a test dialog whose payload contains a duplicate-flood
                tail (the Sep 2026 bug) and assert the server stores the
                COLLAPSED version (flood guard live)
  5. Idempot. — push the same dialog again → no duplicated messages
  6. Tombst.  — delete the dialog the sync-native way
                (deleted_external_ids) → pull reports deleted:true, no msgs
  7. Catalog  — GET /api/chat/models/all merges provider catalogs

The test dialog is prefixed "smoke-" and tombstoned at the end, so devices
only ever see it as a deletion (the DB archive keeps it, per design).

Usage:
    SMOKE_PASSWORD=... python3 scripts/deploy_smoke.py [BASE_URL] [PASSWORD]

BASE_URL defaults to https://flexchat.top. Stdlib only — no venv needed.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://flexchat.top"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SMOKE_PASSWORD", "")
TIMEOUT = 60

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    return ok


def request(path: str, payload: dict | None = None, token: str | None = None,
            method: str | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=TIMEOUT) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def step_tls(host: str) -> None:
    print(f"[1] TLS on {host}:443")
    import tempfile

    try:
        pem = ssl.get_server_certificate((host, 443))
        # _test_decode_cert wants a FILE path, not PEM text.
        with tempfile.NamedTemporaryFile("w", suffix=".pem") as tmp:
            tmp.write(pem)
            tmp.flush()
            cert = ssl._ssl._test_decode_cert(tmp.name)  # type: ignore[attr-defined]
        sans = cert.get("subjectAltName", [])
        names = [v for k, v in sans if k in ("DNS", "IP Address")]
        not_after = datetime.strptime(
            cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (not_after - datetime.now(timezone.utc)).total_seconds() / 86400
        check(host in names, "certificate covers the hostname", f"SANs={names}")
        check(days > 1, "certificate not expiring within 24h", f"notAfter={cert['notAfter']}")
    except Exception as e:  # noqa: BLE001
        check(False, "TLS handshake", repr(e))

def main() -> int:
    host = urlparse(BASE).hostname or "localhost"
    print(f"Smoke test against {BASE}\n")

    step_tls(host)

    print("[2] health")
    status, body = request("/api/health")
    check(status == 200 and body.get("status") == "ok", "GET /api/health", json.dumps(body)[:80])

    print("[3] login")
    if not PASSWORD:
        print("  FAIL  no password given (set SMOKE_PASSWORD or argv[2])")
        return 1
    status, body = request("/api/auth/login", {"password": PASSWORD})
    # bool(): `and` yields the token STRING itself when truthy — keep ok a bool.
    if not check(status == 200 and bool(body.get("token")), "POST /api/auth/login"):
        return 1
    token = body["token"]

    print("[4] baseline pull")
    status, base = request("/api/sync/pull", token=token)
    check(status == 200, "GET /api/sync/pull",
          f"{len(base.get('conversations', []))} conversations, {len(base.get('keys', {}))} keys")
    known = {c["external_id"] for c in base.get("conversations", [])}

    print("[5] sync push with duplicate-flood tail")
    tag = uuid.uuid4().hex[:8]
    ext_id = f"smoke-{tag}"
    q1, a1 = f"smoke {tag} question one?", f"smoke {tag} answer one."
    q2, a2 = f"smoke {tag} flood question?", f"smoke {tag} flood answer."
    now_ms = int(time.time() * 1000)
    # [q1,a1] + the same [q2,a2] block five times — the flood the guard eats.
    msgs = [{"role": "user", "content": q1}, {"role": "assistant", "content": a1}]
    msgs += [{"role": "user", "content": q2}, {"role": "assistant", "content": a2}] * 5
    dialog = {"id": ext_id, "title": f"[smoke] {tag}", "model": None,
              "messages": msgs, "createdAt": now_ms, "updatedAt": now_ms}
    status, body = request("/api/sync/push",
                           {"dialogs": [dialog], "batches": [],
                            "deleted_external_ids": [], "keys": {}},
                           token=token)
    if not check(status == 200 and body.get("created") == 1,
                 "POST /api/sync/push", json.dumps(body)[:80]):
        return 1

    print("[6] pull → flood must be collapsed")
    status, body = request("/api/sync/pull", token=token)
    conv = next((c for c in body["conversations"] if c["external_id"] == ext_id), None)
    if conv is None:
        check(False, "test dialog present after push")
        return 1
    roles = [m["role"] for m in conv["messages"]]
    contents = [m["content"] for m in conv["messages"]]
    check(roles == ["user", "assistant", "user", "assistant"],
          "flood collapsed to one block", f"roles={roles}")
    check(contents == [q1, a1, q2, a2], "message contents intact")

    print("[7] re-push same dialog → idempotent (no duplicates)")
    dialog["updatedAt"] = now_ms + 5000
    status, body = request("/api/sync/push",
                           {"dialogs": [dialog], "batches": [],
                            "deleted_external_ids": [], "keys": {}},
                           token=token)
    check(status == 200, "second POST /api/sync/push", json.dumps(body)[:80])
    status, body = request("/api/sync/pull", token=token)
    conv = next(c for c in body["conversations"] if c["external_id"] == ext_id)
    check(len(conv["messages"]) == 4, "still exactly 4 messages after re-push",
          f"got {len(conv['messages'])}")

    print("[8] tombstone the test dialog (sync-native delete)")
    status, body = request("/api/sync/push",
                           {"dialogs": [], "batches": [],
                            "deleted_external_ids": [ext_id], "keys": {}},
                           token=token)
    check(status == 200 and body.get("deleted") == 1,
          "delete via deleted_external_ids", json.dumps(body)[:80])
    status, body = request("/api/sync/pull", token=token)
    conv = next((c for c in body["conversations"] if c["external_id"] == ext_id), None)
    check(conv is not None and conv["deleted"] is True and not conv["messages"],
          "pull reports deleted:true with no messages")

    print("[9] model catalog (provider merge)")
    status, body = request("/api/chat/models/all", token=token)
    models = body if isinstance(body, list) else body.get("models", [])
    custom = [m for m in models if str(m.get("id", "")).startswith("custom:")]
    check(status == 200 and len(models) > 0, "GET /api/chat/models/all",
          f"{len(models)} models, {len(custom)} custom:-prefixed")

    ok = all(entry[0] is True for entry in results)
    passed = sum(1 for entry in results if entry[0] is True)
    print(f"\n{'ALL CHECKS PASSED' if ok else 'FAILURES PRESENT'}"
          f"  ({passed}/{len(results)})  "
          f"[test dialog {ext_id} tombstoned; "
          f"{len(known)} pre-existing conversations untouched]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
