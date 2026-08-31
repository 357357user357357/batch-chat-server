# Batch Chat Server

A small self-hosted **web version of your Android batch-chat app**: send one prompt to
several OpenRouter models at once, compare answers, and keep all conversations in a
server-side database — readable from any device and never lost when you reinstall
or uninstall the phone app.

## Why this stack

You asked which tools to use — here is the reasoning behind what's in this repo:

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python + FastAPI** | You're comfortable with Python. FastAPI is the most popular modern Python web framework, async-ready, with automatic OpenAPI docs (`/api/docs`). |
| Database | **SQLite (SQLAlchemy)** | Perfect for the cheapest VPS: zero configuration, one file, no separate DB process eating RAM. SQLAlchemy lets you switch to PostgreSQL later with minimal changes. |
| Deployment | **Docker + docker-compose** | One command to deploy on any rented machine; reproducible, no dependency drift. |
| Web UI | **Plain JavaScript (no framework, no build step)** | You said React is unfamiliar. This UI is a single HTML/CSS/JS page — no Node.js, no npm, nothing to compile. Easy to read and modify. |
| LLM API | **OpenRouter** | Already the API your Android app uses. The server keeps the API key in its `.env` (never in the browser). |

## Features

- 🔐 Password-protected web UI (single-user login, session tokens)
- 💬 Conversations stored in **SQLite on the server** → survive app reinstalls, readable from PC/phone/anything with a browser
- 🤖 **Batch send** — one message to many OpenRouter models in parallel, answers stored separately per model
- 📥 **Import API** so you can migrate your existing Android dialogues into the server
- ⚙️ Pure vanilla JS UI, works on mobile and desktop

## Quick start (on any rented VPS)

### 1. Install Docker on the server

```bash
curl -fsSL https://get.docker.com | sh
```

### 2. Get the code & configure

```bash
git clone https://github.com/357357user357357/batch-chat-server.git
cd batch-chat-server

cp .env.example .env
nano .env     # set APP_PASSWORD and OPENROUTER_API_KEY
```

### 3. Run

```bash
docker compose up -d --build
```

That's it. The web UI is at **http://YOUR_SERVER_IP:8000**.

> ⚠️ Add a reverse proxy (Caddy/Caddyfile or Nginx) + HTTPS/Let's Encrypt if you expose
> it to the public internet. The bundled UI is served over plain HTTP on port 8000.

### Re-deploy after server change (migration)

```bash
# On the OLD server: back up the database
#   docker compose down
#   cp -r data ./data-backup

# On the NEW server, after cloning the repo:
chmod +x scripts/import_backup.py
python3 scripts/import_backup.py data-backup/batch_chat.db
# or simply replace the ./data folder with the backup before starting
```

If you only need the conversations, the simplest migration is copying the
`data/batch_chat.db` file from the old server to the new one.

## Local development (no Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export APP_PASSWORD=test123         # required
export OPENROUTER_API_KEY=your_key  # optional for full chat

uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, log in, enjoy.

## API overview

Interactive docs are available at `/api/docs`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/login` | Log in with the app password → returns a token |
| GET | `/api/health` | Server health + whether OpenRouter is configured |
| GET | `/api/conversations` | List conversations |
| POST | `/api/conversations` | Create a conversation |
| GET | `/api/conversations/{id}` | Get conversation with all messages |
| PATCH | `/api/conversations/{id}` | Rename conversation |
| DELETE | `/api/conversations/{id}` | Delete conversation |
| POST | `/api/conversations/{id}/messages` | Manually append a message |
| POST | `/api/chat/send` | Send one message to N models in parallel (stores results) |
| GET | `/api/chat/models` | List default model suggestions |
| POST | `/api/import` | Bulk-import conversations (generic JSON) |
| POST | `/api/import/phone` | Import Android-app AsyncStorage JSON (`dialogs` + `batches`) |

### Example: import old dialogues from the phone

The web UI has an **⬆ Import** button in the sidebar — paste the data and it just works.
The endpoint accepts:

- A **whole AsyncStorage dump**: `{"openrouter.dialogs.v1": [...], "openrouter.batches.history.v1": [...]}`
- The **lists separately** (`{dialogs: [...], batches: [...]}`)
- The old **generic** format below

```bash
curl -X POST http://localhost:8000/api/import/phone \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"openrouter.dialogs.v1": [{"id":"d1","title":"My chat","model":"deepseek/deepseek-v4-flash-0731","messages":[{"role":"user","content":"Hello"},{"role":"assistant","content":"Hi"}]}]}'
```

These are the exact storage keys used by the `batch-chat` Android app
(React Native/Expo, `@react-native-async-storage/async-storage`):

| Phone key | Contents |
|---|---|
| `openrouter.dialogs.v1` | chat dialogs: `{id, title, model, messages[], createdAt, updatedAt}` |
| `openrouter.batches.history.v1` | batch runs: `{id, model, prompts[], batch{results[]}, createdAt}` |
| `openrouter.active-dialog.v1` | last opened dialog id (no need to import) |
| `openrouter.batches.selected.v1` | last opened batch id (no need to import) |

Import notes:
- Chat dialogs map 1:1 to server conversations (`kind = "chat"`).
- Batch items are flattened to prompt→answer pairs (`kind = "batch"`); each
  prompt becomes a `user` message and its result from `req-{n}` becomes the
  `assistant` message. Failed jobs are stored as `[error] …`.
- The app's dialog/batch `id` is kept in `external_id`, so **re-importing the
  same export never duplicates anything**.

### Example: batch chat via API

```bash
curl -X POST http://localhost:8000/api/chat/send \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "Explain REST APIs in one sentence.",
    "models": ["deepseek/deepseek-chat", "openai/gpt-4o-mini"]
  }'
```

## Security notes

- Change `APP_PASSWORD` to something strong.
- The API key never leaves the server.
- For production, put the app behind a reverse proxy with HTTPS (Caddy is the easiest:
  it auto-provisions Let's Encrypt certificates).
- Token-based auth: log in once, the browser stores the token in `localStorage`.

## Project layout

```
app/
  main.py                    # FastAPI app entry
  config.py                  # settings from .env
  database.py                # SQLAlchemy engine/session
  models.py                  # Conversation, Message, AuthToken
  schemas.py                 # Pydantic request/response models
  security.py                # password + bearer-token auth
  routers/
    auth.py                  # /api/auth/*, /api/health
    conversations.py         # /api/conversations CRUD
    chat.py                  # /api/chat/send (batch to OpenRouter)
    import_conversations.py  # /api/import
  services/openrouter.py     # OpenRouter HTTP client
  static/                    # Web UI (HTML/CSS/JS, no build step)
Dockerfile
docker-compose.yml
```