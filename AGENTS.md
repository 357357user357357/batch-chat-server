# batch-chat-server — Agent Notes

## Project
FastAPI + SQLite backend deployed at https://flexchat.top (Docker, `/opt/batch-chat-server`, ssh root@<MAIN_IP>). Companion Expo app: `/home/nikolas/Documents/batch-chat`. Old server <OLD_SERVER_IP> kept read-only as migration fallback until DNS/devices fully moved.

## Deployment flow
Local edit → test → `git push origin main` → `ssh root@<MAIN_IP> "cd /opt/batch-chat-server && git pull && docker compose up -d --build"`. Container name: `batch-chat`.

## Testing
`.venv/bin/python -m pytest tests/ -q` (~113 tests, <10s). Tests set `APP_PASSWORD=test` + tmp sqlite via env before importing `app.main`.
Live deployment check: `SMOKE_PASSWORD=<prod pw> python3 scripts/deploy_smoke.py [BASE_URL]` — stdlib-only E2E against a running server (TLS cert, health, login, sync push/pull with a duplicate-flood payload, idempotent re-push, sync-native tombstone delete, /api/chat/models/all merge). Creates one `smoke-*` dialog and tombstones it (archive-only, invisible to devices).

## HTTPS / TLS
- uvicorn serves TLS directly on 443→8443 (no nginx). Certs via lego 5.4.1 (`/usr/local/bin/lego`), shortlived profile (~6 days), state in `/opt/batch-chat-server/.lego/`.
- Renewal: `/etc/cron.d/batch-chat-tls` runs `scripts/renew-tls.sh` every 8h → log `/var/log/batch-chat-tls.log` (logrotate: `/etc/logrotate.d/batch-chat-tls`). Script skips while >5 days remain, then `lego run` re-issues in place and restarts the container.
- Gotcha (seen on the old server Sep 2026): if the cert was issued by a DIFFERENT ACME account than the one renewing, `lego renew` fails forever with ARI 403 "requester account did not request the certificate being replaced". Fix = delete `.lego/certificates/*` and let `lego run` issue fresh (new account is fine).
- Domain certs: `TLS_DOMAINS` in `.env` is active (flexchat.top + www), cert issued Sep 17 2026 (shortlived, valid ~7 days).
- Old server <OLD_SERVER_IP>: since its cert expired Sep 16 (lego ARI account mismatch) and DNS has an 86400s TTL, it now runs an iptables DNAT relay (443+8000 → <MAIN_IP>, persisted via netfilter-persistent) so stale-DNS clients get the new valid cert. Removal steps: `/root/REMOVE-PROXY-NOTE.txt` on the old server (remove after 2026-09-18).
- App-pinning CA for the Android app lives in `certs/` (ca.crt embedded in the app; copy it when migrating servers).

## Sync duplicate-flood guard (Sep 2026 bug)
A buggy client release pushed whole dialog lists with the same dialog repeated 32×; the server appended every copy. `collapse_repeated_blocks()` (app/services/phone_sync.py) now collapses a repeated-block tail (≥3 reps) on every ingest path (sync push, phone import). Already-stored floods are repaired by `scripts/cleanup_duplicate_blocks.py --db data/batch_chat.db [--dry-run]` — it TOMBSTONES the copies (never hard-deletes) so stale device pushes can't resurrect them.

## Provider system (chat models)
- Prefix routing in `app/services/custom_provider.py` + `chat.py`: bare id → OpenRouter; `vertex:`, `bedrock:`, `custom:` → their gateways. `:flex` suffix → OpenRouter flex tier; on `custom:` gateways flex is sent as `service_tier:"flex"` with one automatic standard-tier retry on 400/422.
- `custom:` = any OpenAI-compatible gateway (FastRouter etc.), configured via `CUSTOM_API_KEY` / `CUSTOM_BASE_URL` / `CUSTOM_DEFAULT_MODEL` in `.env` (see `.env.example`).
- `/api/chat/models/all` merges OpenRouter catalog + gateway catalog (`custom:`-prefixed ids, 1h TTL cache in `custom_provider._CATALOG_CACHE`). Mapping in `custom_provider.fetch_custom_catalog()` is tolerant: OpenRouter-schema string pricing (FastRouter) and bare `{"id"}` entries (LM Studio/Ollama/vLLM) both work; gateway errors must never break the endpoint (wrapped in try/except at the router).
- Frontend (Expo) consumes only `id, name, created, prompt, completion` from the catalog — sort/search work without frontend changes.

## Gotchas
- Production `APP_PASSWORD` is in `/opt/batch-chat-server/.env` (check there before testing auth live; don't assume local values).
- OpenRouter catalog returns numeric pricing; FastRouter-style gateways return strings — always float()-convert.
- Mock test gateways: local `http.server.HTTPServer` on 127.0.0.1 with monkeypatched settings beats network mocks.
- scp of big files (lego ~68MB) through the 30s tool timeout leaves TRUNCATED binaries — verify md5 after transfer (segfault = corrupt download).
