from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.database import Base, engine
from app.routers import auth, chat, conversations, import_conversations


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


Base.metadata.create_all(bind=engine)
_run_migrations()

app = FastAPI(
    title="Batch Chat Server",
    version="1.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(import_conversations.router)

# Serve the static web UI (Plain HTML/JS, no build step required)
ui_dir = Path(__file__).parent / "static"
if ui_dir.is_dir():
    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {"status": "no UI"}