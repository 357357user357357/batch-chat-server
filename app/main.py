import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from app.routers import auth, batches, chat, conversations, import_conversations, settings as settings_router, sync
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

        msg_existing = {col["name"] for col in inspector.get_columns("messages")}
        if "deleted_at" not in msg_existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN deleted_at DATETIME"))
        if "keepalive_enabled" not in existing:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN keepalive_enabled BOOLEAN DEFAULT 0"))


Base.metadata.create_all(bind=engine)
_run_migrations()

_db = SessionLocal()
try:
    load_overrides(_db)
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
app.include_router(batches.router)
app.include_router(settings_router.router)
app.include_router(sync.router)

start_batch_worker()
start_cache_keeper()

# Serve the static web UI (Plain HTML/JS, no build step required)
ui_dir = Path(__file__).parent / "static"
if ui_dir.is_dir():
    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {"status": "no UI"}