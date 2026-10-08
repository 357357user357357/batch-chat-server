#!/bin/bash
# bc-nightly-dump.sh — nightly snapshot of the batch-chat master SQLite DB,
# shipped to the RU relay (62.109.10.170). Runs on the main (Turkey) server.
#   Cron: /etc/cron.d/bc-ops          Logs: /var/log/bc-nightly.log
# Uses the sqlite3 .backup API inside the container, so it is safe while the
# server is serving. Keeps KEEP generations on the relay.
set -uo pipefail

RELAY="root@62.109.10.170"
KEY="/root/.ssh/id_ed25519_bc"   # bc-backup-turkey key, installed on the relay
KEEP=14
TAG="[bc-dump $(date '+%F %T')]"

exec 9>/var/lock/bc-dump.lock
flock -n 9 || { echo "$TAG another run in progress"; exit 0; }
echo "$TAG start"

# 1. Consistent snapshot inside the container.
if ! docker exec batch-chat python -c \
    "import sqlite3; sqlite3.connect('/app/data/batch_chat.db').backup(sqlite3.connect('/tmp/snapshot.db'))"; then
    /usr/local/bin/bc-alert.sh bc-dump "ERROR: sqlite .backup inside container failed"
    exit 1
fi
docker cp batch-chat:/tmp/snapshot.db /opt/batch-chat-server/data/snapshot.db >/dev/null
docker exec batch-chat rm -f /tmp/snapshot.db

# 2. Integrity-check the host copy before shipping it.
IV=$(sqlite3 /opt/batch-chat-server/data/snapshot.db "PRAGMA integrity_check;" 2>&1)
if [ "$IV" != "ok" ]; then
    /usr/local/bin/bc-alert.sh bc-dump "ERROR: integrity_check: ${IV}"
    exit 1
fi

# 3. Compress and ship.
STAMP=$(date +%Y%m%d)
GZ="/tmp/batch_chat-${STAMP}.db.gz"
gzip -c /opt/batch-chat-server/data/snapshot.db > "$GZ"
if ! gzip -t "$GZ"; then
    /usr/local/bin/bc-alert.sh bc-dump "ERROR: gzip check failed"
    exit 1
fi
if ! ssh -i "$KEY" -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new \
        "$RELAY" "mkdir -p /var/backups/from-main"; then
    /usr/local/bin/bc-alert.sh bc-dump "ERROR: ssh to relay failed"
    exit 1
fi
if ! scp -qi "$KEY" -o ConnectTimeout=30 "$GZ" "$RELAY:/var/backups/from-main/"; then
    /usr/local/bin/bc-alert.sh bc-dump "ERROR: scp to relay failed"
    exit 1
fi
rm -f "$GZ"

# 4. Prune old generations on the relay and confirm the fresh copy landed.
ssh -i "$KEY" "$RELAY" \
    "cd /var/backups/from-main && ls -1t batch_chat-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f"
R=$(ssh -i "$KEY" "$RELAY" "[ -s /var/backups/from-main/batch_chat-${STAMP}.db.gz ] && echo remote-ok || echo remote-missing")
if [ "$R" != "remote-ok" ]; then
    /usr/local/bin/bc-alert.sh bc-dump "ERROR: dump missing on relay after scp"
    exit 1
fi
echo "$TAG done: batch_chat-${STAMP}.db.gz shipped to relay (integrity ok)"
