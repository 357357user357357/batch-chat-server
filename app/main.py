import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.config import settings
from app.database import Base, SessionLocal, engine

# Make app-level INFO logs (cache keeper pings, batch worker, …) visible in
# `docker logs` alongside uvicorn's own output.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
from app.routers import auth, batches, chat, conversations, export_conversations, import_conversations, settings as settings_router, stats, sync
from app.services import cache_keeper
from app.services.batch_worker import start_batch_worker
from app.services.cache_keeper import start_cache_keeper
from app.services.settings_store import load_overrides


def _run_migrations() -> None:
    """Lightweight SQLite migration for columns added after first release."""
    inspector = inspect(engine)
    if not inspector.has_table("conversations"):
        return
    existing = {col["name"] for col in inspector.get_columns("conversations")}
    with engine.begin() as conn:
        if "external_id" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN external_id VARCHAR(255)"))
        if "kind" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN kind VARCHAR(16) DEFAULT 'chat'"))
        if "model" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN model VARCHAR(255)"))
        if "deleted_at" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN deleted_at DATETIME"))
        if "account_id" not in existing:
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN account_id VARCHAR(64)")
            )

        if "keepalive_enabled" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN keepalive_enabled BOOLEAN DEFAULT 0"))
        # Audit-trail columns: whose record it was / who modified / who deleted.
        if "origin_device" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN origin_device VARCHAR(64)"))
        if "modified_by" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN modified_by VARCHAR(64)"))
        if "deleted_by" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN deleted_by VARCHAR(64)"))

        msg_existing = {col["name"] for col in inspector.get_columns("messages")}
        if "deleted_at" not in msg_existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN deleted_at DATETIME"))
        if "deleted_by" not in msg_existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN deleted_by VARCHAR(64)"))
        # Positional order (🔄 retry inserts answers between existing ones).
        # The backfill keys old rows by id, so the previous display order
        # (id order) is preserved exactly.
        if "sort_index" not in msg_existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN sort_index FLOAT"))
            conn.execute(
                text("UPDATE messages SET sort_index = id WHERE sort_index IS NULL")
            )
        # Per-message OpenRouter metadata (reasoning effort, provider, usage).
        for col_name, col_type in (
            ("reasoning", "VARCHAR(32)"),
            ("provider", "VARCHAR(128)"),
            ("gen_id", "VARCHAR(128)"),
            ("tokens_prompt", "INTEGER"),
            ("tokens_cached", "INTEGER"),
            ("tokens_completion", "INTEGER"),
            ("total_tokens", "INTEGER"),
            ("cost", "FLOAT"),
        ):
            if col_name not in msg_existing:
                conn.execute(text(f"ALTER TABLE messages ADD COLUMN {col_name} {col_type}"))
        if inspector.has_table("message_tombstones"):
            tomb_existing = {col["name"] for col in inspector.get_columns("message_tombstones")}
            if "deleted_by" not in tomb_existing:
                conn.execute(text("ALTER TABLE message_tombstones ADD COLUMN deleted_by VARCHAR(64)"))

        if inspector.has_table("auth_tokens"):
            tok_existing = {col["name"] for col in inspector.get_columns("auth_tokens")}
            if "account_id" not in tok_existing:
                conn.execute(text("ALTER TABLE auth_tokens ADD COLUMN account_id VARCHAR(64)"))

        if inspector.has_table("batch_jobs"):
            job_existing = {col["name"] for col in inspector.get_columns("batch_jobs")}
            if "account_id" not in job_existing:
                conn.execute(text("ALTER TABLE batch_jobs ADD COLUMN account_id VARCHAR(64)"))
        if inspector.has_table("accounts"):
            acc_existing = {col["name"] for col in inspector.get_columns("accounts")}
            if "email" not in acc_existing:
                conn.execute(text("ALTER TABLE accounts ADD COLUMN email VARCHAR(255)"))
            if "email_confirmed" not in acc_existing:
                conn.execute(
                    text("ALTER TABLE accounts ADD COLUMN email_confirmed BOOLEAN DEFAULT 0")
                )
            if "confirm_token" not in acc_existing:
                conn.execute(text("ALTER TABLE accounts ADD COLUMN confirm_token VARCHAR(64)"))
            if "confirm_token_expires" not in acc_existing:
                conn.execute(
                    text("ALTER TABLE accounts ADD COLUMN confirm_token_expires DATETIME")
                )


Base.metadata.create_all(bind=engine)
_run_migrations()

_db = SessionLocal()
try:
    load_overrides(_db)
    # Multi-account: create the accounts table identity (owner migrated from
    # the legacy app_settings rows — same id + key, so existing pair codes
    # keep working), then stamp every pre-existing row with the owner's id.
    from app.services.account import ensure_owner_account

    _owner = ensure_owner_account(_db)
    _db.execute(
        text("UPDATE conversations SET account_id = :aid WHERE account_id IS NULL"),
        {"aid": _owner.id},
    )
    _db.execute(
        text("UPDATE batch_jobs SET account_id = :aid WHERE account_id IS NULL"),
        {"aid": _owner.id},
    )
    _db.execute(
        text("UPDATE auth_tokens SET account_id = :aid WHERE account_id IS NULL"),
        {"aid": _owner.id},
    )
    _db.commit()
    # Restore the 🔥 Cache keep-alive toggles the user enabled before a restart.
    try:
        import json as _json

        from sqlalchemy import select as _select

        from app.models import AppSetting

        _row = _db.get(AppSetting, "keepalive_conversation_ids")
        if _row and _row.value:
            cache_keeper.restore_enabled(_json.loads(_row.value))
    except Exception:
        pass  # a lost toggle only means cold caches, never broken startup
finally:
    _db.close()

app = FastAPI(
    title="Batch Chat Server",
    version="1.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(import_conversations.router)
app.include_router(export_conversations.router)
app.include_router(batches.router)
app.include_router(settings_router.router)
app.include_router(stats.router)
app.include_router(sync.router)

start_batch_worker()
start_cache_keeper()

# /health must be registered BEFORE the "/" StaticFiles mount below: a mount
# at "/" matches every path, so any route added after it would be shadowed.
@app.get("/health")
def health():
    return {"ok": True}


# ---------- /links page + /downloads (APK hosting) ----------
# Both live BEFORE the "/" StaticFiles mount below for the same reason: the
# "/" mount swallows every path that has no earlier route or mount.
# /downloads hosts build artifacts (the Android APK); /links is the page to
# share with new devices — it lists the app, both repos and the APK.
DOWNLOADS_DIR = Path(__file__).resolve().parent.parent / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
app.mount("/downloads", StaticFiles(directory=DOWNLOADS_DIR), name="downloads")

_LINKS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batch Chat — links</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0e1116; color: #e6e8eb;
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 24px;
  }
  .card { width: 100%; max-width: 560px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #9aa3ad; margin: 0 0 24px; font-size: 14px; }
  a.tile {
    display: flex; align-items: center; gap: 14px;
    background: #171c24; border: 1px solid #2a323d; border-radius: 14px;
    padding: 14px 16px; margin-bottom: 12px; text-decoration: none; color: inherit;
    transition: border-color .15s, background .15s;
  }
  a.tile:hover { border-color: #4f8cff; background: #1b2230; }
  .icon { flex: 0 0 40px; height: 40px; display: flex; align-items: center; justify-content: center;
          background: #232c3a; border-radius: 10px; font-size: 20px; }
  .icon-img { width: 34px; height: 34px; border-radius: 8px; display: block; }
  .icon-svg { width: 26px; height: 26px; display: block; color: #e6e8eb; }
  .t { font-weight: 600; }
  .d { color: #9aa3ad; font-size: 13px; overflow-wrap: anywhere; }
  footer { color: #6b7480; font-size: 12px; margin-top: 18px; text-align: center; }
  footer a { color: #6b7480; }
</style>
</head>
<body>
<main class="card">
  <h1>🧊 Batch Chat</h1>
  <p class="sub">Everything for this instance — flexchat.top</p>

  <a class="tile" href="/downloads/batch-chat.apk" download>
    <span class="icon"><img class="icon-img" src="/icon-192.png" alt="Batch Chat app icon"></span>
    <span><span class="t">Download the Android app (APK)</span><br>
    <span class="d">flexchat.top/downloads/batch-chat.apk</span></span>
  </a>

  <a class="tile" href="https://github.com/357357user357357/batch-chat" target="_blank" rel="noopener">
    <span class="icon"><svg class="icon-svg" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></span>
    <span><span class="t">Phone app — source (GitHub)</span><br>
    <span class="d">github.com/357357user357357/batch-chat</span></span>
  </a>

  <a class="tile" href="https://github.com/357357user357357/batch-chat-server" target="_blank" rel="noopener">
    <span class="icon"><svg class="icon-svg" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg></span>
    <span><span class="t">Server — source (GitHub)</span><br>
    <span class="d">github.com/357357user357357/batch-chat-server</span></span>
  </a>

  <a class="tile" href="/">
    <span class="icon">💬</span>
    <span><span class="t">Open the web UI</span><br>
    <span class="d">flexchat.top</span></span>
  </a>

  <a class="tile" href="/api/docs">
    <span class="icon">🛠️</span>
    <span><span class="t">API docs (OpenAPI)</span><br>
    <span class="d">flexchat.top/api/docs</span></span>
  </a>

  <footer>Self-hosted · FastAPI + SQLite · the APK is built from the app repo above</footer>
</main>
</body>
</html>
"""


@app.get("/links", include_in_schema=False)
def links():
    """Public landing page with the app/repo/APK links (no auth)."""
    return HTMLResponse(_LINKS_HTML)

# Serve the static web UI (Plain HTML/JS, no build step required)
ui_dir = Path(__file__).parent / "static"
if ui_dir.is_dir():
    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {"status": "no UI"}