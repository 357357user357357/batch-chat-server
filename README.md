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
| LLM API | **OpenRouter** (+ direct Google Vertex AI / AWS Bedrock) | Already the API your Android app uses. The server keeps the API key in its `.env` (never in the browser). Vertex AI and Bedrock can be called directly too, once you add their credentials. |

## Features

- 🔐 Password-protected web UI (single-user login, session tokens)
- 💬 Conversations stored in **SQLite on the server** → survive app reinstalls, readable from PC/phone/anything with a browser
- 🤖 **Batch send** — one message to many models in parallel (OpenRouter, and optionally Vertex AI / Bedrock), answers stored separately per model
- 📦 **JSONL batch jobs** — run async batches exactly like GCP Vertex AI / AWS Bedrock: paste a `.jsonl` (OpenAI/OpenRouter, Vertex, or Bedrock line formats), get answers in a conversation
- 📥 **Import API** so you can migrate your existing Android dialogues into the server
- ✍️ Messages render **Markdown + LaTeX** (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`), and every message has a **Copy** button that copies the raw source (LaTeX included), not the rendered HTML
- ⚙️ **Settings modal** in the web UI to set OpenRouter/Vertex/Bedrock credentials — saved to the server DB and applied immediately, no SSH or restart needed
- 🗑️ **Delete any question/answer inside a dialogue** from the web — the text stays archived in the server DB, and every synced device (including the phone) drops it on the next sync
- 🔁 **Key sync** — OpenRouter/Tavily keys are unified between phone and server (gaps filled both ways, server-first)
- ⚡ **Flex processing tier** — append `:flex` to any model (e.g. `openai/gpt-6-astra:flex`) for the cheaper `service_tier="flex"` processing; if the provider doesn't serve flex for that model, the server falls back to the standard tier automatically (or run it through the Batch API)
- 🔥 **Cache keep-alive** — after the last chat, near-empty pings (every 45 min) refresh the 1-hour prompt cache at ~10% of input cost, keeping replies cheap for 2–24 hours instead of just one (Settings → Cache keep-alive; 0 = off)
- ⚙️ Pure vanilla JS UI, works on mobile and desktop

## Multiple providers (OpenRouter / Vertex AI / Bedrock)

By default every model is called through **OpenRouter**. You can also call
**Google Vertex AI** or **AWS Bedrock** directly (no OpenRouter markup, uses
your own cloud billing) by prefixing the model name in the "Models" picker
(the custom-model box already supports free text):

| Prefix | Example | Needs |
|---|---|---|
| *(none)* | `anthropic/claude-sonnet-4.5` | `OPENROUTER_API_KEY` |
| *(none)* + `:flex` | `openai/gpt-6-astra:flex` — Flex processing tier (`service_tier="flex"`, cheaper/slower); auto-falls-back to standard tier when the provider rejects it | `OPENROUTER_API_KEY` |
| `vertex:` | `vertex:gemini-2.5-flash`, `vertex:claude-sonnet-4-5@20250929` | `GOOGLE_PROJECT_ID` + service-account key |
| `bedrock:` | `bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0` | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |

See `.env.example` for exact steps to obtain each credential. `GET /api/health`
reports which providers are currently configured (`vertex_configured`,
`bedrock_configured`). Note: the async **`/api/batches` JSONL endpoint still
submits through OpenRouter's Batch API** — real Vertex/Bedrock batch jobs need
a GCS/S3 bucket, which is out of scope for a single cheap VPS; the `vertex:`
and `bedrock:` prefixes work with `/api/chat/send` (regular + multi-model
compare) today.


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
| POST | `/api/batches` | Submit a JSONL batch (async, ~50% cost) |
| GET | `/api/batches` | List batch jobs |
| GET | `/api/batches/{id}` | Batch job detail (+ results) |
| POST | `/api/batches/{id}/poll` | Force-poll a batch job |
| DELETE | `/api/batches/{id}` | Delete a batch job |

### JSONL batch (like GCP Vertex AI / AWS Bedrock — but via OpenRouter)

Yes, **Claude (e.g. `anthropic/claude-fable-5.1`) is available on Google Vertex AI, AWS Bedrock AND OpenRouter** — this server uses the OpenRouter async Batch API, which is the simplest (no GCS/S3, no IAM) and ~50% cheaper. The server understands the `.jsonl` line formats of all of them:

```bash
curl -X POST http://localhost:8000/api/batches \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "anthropic/claude-fable-5.1:batch",
    "jsonl": "{\"custom_id\": \"req-1\", \"prompt\": \"Write a haiku\"}\n{\"request\": {\"contents\": [{\"role\": \"user\", \"parts\": [{\"text\": \"Explain REST\"}]}]}}\n{\"recordId\": \"rec-3\", \"modelInput\": {\"messages\": [{\"role\": \"user\", \"content\": \"Say hi\"}]}}\n"
  }'
```

Supported line formats (mixed freely in one file):

| Provider style | Example line |
|---|---|
| OpenRouter / OpenAI | `{"custom_id":"r1","body":{"messages":[{"role":"user","content":"…"}]}}` |
| Shorthand | `{"custom_id":"r1","messages":[{"role":"user","content":"…"}]}` |
| Simple prompt | `{"custom_id":"r1","prompt":"…"}` |
| Google Vertex AI (Gemini) | `{"request":{"systemInstruction":{"parts":[{"text":"…"}]},"contents":[{"role":"user","parts":[{"text":"…"}]}]}}` |
| AWS Bedrock (Anthropic) | `{"recordId":"r1","modelInput":{"system":"…","messages":[{"role":"user","content":[{"type":"text","text":"…"}]}]}}` |

How it works:
1. The server parses the JSONL into `{custom_id, body}` requests and calls
   `POST /api/beta/batches` on OpenRouter (`endpoint` + `model` sent before `requests`).
2. A background worker polls `GET /api/beta/batches/{id}` every 30s until the
   job reaches a terminal status (`completed`/`failed`/`expired`/`cancelled`).
3. On completion each line's answer is resolved from
   `results[].response.body.choices[0].message.content` and a
   `kind="batch"` **conversation** is created (prompt → answer pairs), so the
   results appear in the normal web UI chat list.
4. The web UI has an **⚡ Batch** button for pasting JSONL and watching progress.

> ⚠️ Use a Claude `:batch` model for the async Batch API (`anthropic/claude-fable-5.1:batch`
> or another `…:batch` model) — this is the ~50%-off path which GCP/AWS roughly
> correspond to. Regular model ids still work for synchronous chat via `/api/chat/send`.

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
- The login endpoint has brute-force protection: after 5 failed attempts the
  client IP is locked out (30s, doubling up to 15 minutes).
- The API key never leaves the server.
- For production, put the app behind a reverse proxy with HTTPS (Caddy is the easiest:
  it auto-provisions Let's Encrypt certificates).
- Token-based auth: log in once, the browser stores the token in `localStorage`.
- CORS is configurable via `CORS_ORIGINS` (comma-separated); default `*` keeps
  phone/PC clients working over LAN IPs — tighten it when behind a real domain.
- The web UI and API are served over plain HTTP on :8000 by default; sync
  traffic (dialogs, keys) is only as private as the network between you and
  the VPS, so the reverse proxy + HTTPS is strongly recommended.

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