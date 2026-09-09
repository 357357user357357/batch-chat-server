#!/bin/sh
# Renew the Let's Encrypt certificate (short-lived profile, ~6-day lifetime)
# for the configured domain(s) and/or the server IP, then reload the container
# when it actually renewed.
# Nothing is hardcoded: SERVER_IP and TLS_DOMAINS come from the environment
# or the SERVER_IP=... / TLS_DOMAINS=... lines in the repo's .env file.
cd "$(dirname "$0")/.." || exit 1
if [ -z "${SERVER_IP:-}" ] && [ -f .env ]; then
  SERVER_IP=$(grep -E '^SERVER_IP=' .env | head -1 | cut -d= -f2 | tr -d '"')
fi
if [ -z "${TLS_DOMAINS:-}" ] && [ -f .env ]; then
  TLS_DOMAINS=$(grep -E '^TLS_DOMAINS=' .env | head -1 | cut -d= -f2 | tr -d '"')
fi
if [ -z "${SERVER_IP:-}" ] && [ -z "${TLS_DOMAINS:-}" ]; then
  echo "Neither SERVER_IP nor TLS_DOMAINS is set (define them in .env or the environment)" >&2
  exit 1
fi

# The primary name decides the certificate file names lego creates.
PRIMARY=$(echo ${TLS_DOMAINS:-} | awk '{print $1}')
[ -n "$PRIMARY" ] || PRIMARY="$SERVER_IP"
CRT=".lego/certificates/$PRIMARY.crt"

# Renew only when the current cert expires within 5 days (cron runs every 8h).
if [ -f "$CRT" ]; then
  END=$(openssl x509 -in "$CRT" -noout -enddate | cut -d= -f2)
  LEFT=$(( $(date -d "$END" +%s) - $(date +%s) ))
  if [ "$LEFT" -gt $((5 * 24 * 3600)) ]; then
    echo "Skip renewal: expires $END, renewal possible in $((LEFT / 3600))h"
    exit 0
  fi
fi

# lego v5: `run` issues and re-issues the certificate (overwrites in place).
ARGS=""
for d in $TLS_DOMAINS $SERVER_IP; do
  [ -n "$d" ] && ARGS="$ARGS --domains $d"
done
/usr/local/bin/lego run --accept-tos $ARGS --http --profile shortlived --pem 2>&1

# Publish the current cert/key under stable names (docker-compose mounts these).
if [ -f ".lego/certificates/$PRIMARY.crt" ]; then
  cat ".lego/certificates/$PRIMARY.crt" ".lego/certificates/$PRIMARY.issuer.crt" \
      > .lego/certificates/current.crt
  cp ".lego/certificates/$PRIMARY.key" .lego/certificates/current.key
  docker compose restart batch-chat
fi

