# Batch-chat server — nightly backups & uptime monitoring

Deployed on the main (Turkey) server and mirrored here for version control.
The companion scripts for the RU relay (Nextcloud) live in the `nextcloud`
repo under `ops/`.

## What runs (cron: `/etc/cron.d/bc-ops`)

| Schedule | Script | What it does |
|---|---|---|
| daily 03:17 | `bc-nightly-dump.sh` | consistent `sqlite3 .backup` of the batch-chat master DB inside the container (safe while serving), integrity-checked, gzipped and shipped to the relay (`/var/backups/from-main/`), 14 generations kept |
| every 10 min | `bc-uptime.sh` | checks flexchat.top (this host) **and** Nextcloud on the relay; 2 consecutive failures → alert, recovery → RECOVERED alert |

`bc-alert.sh` is the shared alert sink (identical copy on both servers):
appends to `/var/log/bc-alerts.log` and, if `/etc/bc-alert.env` defines
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, also pushes to Telegram. The env
file is not in git.

## Setup applied on the main server

- SSH key `/root/.ssh/id_ed25519_bc` (comment `bc-backup-turkey`) → authorized
  on the relay; the relay's `bc-backup-relay` key → `/root/.ssh/authorized_keys`
  here. Both directions tested.
- A copy of the latest snapshot always sits at
  `/opt/batch-chat-server/data/snapshot.db` (also what the laptop-side
  `~/bin/bc-snapshot` produces).

## Restore

1. Stop writes: `docker compose stop` (or accept live-restore risk).
2. `gunzip -c batch_chat-YYYYMMDD.db.gz > /opt/batch-chat-server/data/batch_chat.db`
3. `docker compose up -d`, then check `/api` health and run a sync from a client.

## Verify it is alive

```bash
tail /var/log/bc-nightly.log /var/log/bc-uptime.log /var/log/bc-alerts.log
ls -lh /var/backups/from-main/   # on the relay
```
