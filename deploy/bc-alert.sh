#!/bin/bash
# bc-alert.sh TAG "message" — append an ops alert to /var/log/bc-alerts.log and,
# when /etc/bc-alert.env defines TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, push it
# to Telegram. Deployed identically on the main (Turkey) and relay (RU) servers.
# The env file itself is NOT in git (contains secrets if/when Telegram is set up).
set -u
TAG="${1:-ops}"
shift || true
MSG="$*"
echo "[$(date '+%F %T')] ${TAG}: ${MSG}" >> /var/log/bc-alerts.log
if [ -f /etc/bc-alert.env ]; then
    # shellcheck disable=SC1091
    . /etc/bc-alert.env
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sm 10 -o /dev/null \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=[${TAG}] ${MSG}" || true
    fi
fi
