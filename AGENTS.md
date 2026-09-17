# batch-chat-server — Agent Notes

## Project
FastAPI + SQLite backend deployed at http://194.36.85.208:8000/ (Docker, `/opt/batch-chat-server`, ssh root@194.36.85.208). Companion Expo app: `/home/nikolas/Documents/batch-chat`.

## Deployment flow
Local edit → test → `git push origin main` → `ssh root@194.36.85.208 "cd /opt/batch-chat-server && git pull && docker compose up -d --build"`. Container name: `batch-chat`.

## Testing
`.venv/bin/python -m pytest tests/ -q` (~100 tests, <10s). Tests set `APP_PASSWORD=test` + tmp sqlite via env before importing `app.main`.

## Provider system (chat models)
- Prefix routing in `app/services/custom_provider.py` + `chat.py`: bare id → OpenRouter; `vertex:`, `bedrock:`, `custom:` → their gateways. `:flex` suffix → OpenRouter flex tier.
- `custom:` = any OpenAI-compatible gateway (FastRouter etc.), configured via `CUSTOM_API_KEY` / `CUSTOM_BASE_URL` / `CUSTOM_DEFAULT_MODEL` in `.env` (see `.env.example`).
- `/api/chat/models/all` merges OpenRouter catalog + gateway catalog (`custom:`-prefixed ids, 1h TTL cache in `custom_provider._CATALOG_CACHE`). Mapping in `custom_provider.fetch_custom_catalog()` is tolerant: OpenRouter-schema string pricing (FastRouter) and bare `{"id"}` entries (LM Studio/Ollama/vLLM) both work; gateway errors must never break the endpoint (wrapped in try/except at the router).
- Frontend (Expo) consumes only `id, name, created, prompt, completion` from the catalog — sort/search work without frontend changes.

## Gotchas
- Production `APP_PASSWORD` is in `/opt/batch-chat-server/.env` (check there before testing auth live; don't assume local values).
- OpenRouter catalog returns numeric pricing; FastRouter-style gateways return strings — always float()-convert.
- Mock test gateways: local `http.server.HTTPServer` on 127.0.0.1 with monkeypatched settings beats network mocks.
