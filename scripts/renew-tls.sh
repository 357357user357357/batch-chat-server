#!/bin/sh
# Renew the Let's Encrypt certificate for the server IP (short-lived profile,
# ~6-day lifetime) and reload the container when it actually renewed.
# The IP is NOT hardcoded: it comes from $SERVER_IP (environment) or the
# SERVER_IP=... line in the repo's .env file.
cd "$(dirname "$0")/.." || exit 1
if [ -z "${SERVER_IP:-}" ] && [ -f .env ]; then
  SERVER_IP=$(grep -E '^SERVER_IP=' .env | head -1 | cut -d= -f2 | tr -d '"'"'"'')
fi
if [ -z "${SERVER_IP:-}" ]; then
  echo "SERVER_IP is not set (define it in .env or the environment)" >&2
  exit 1
fi

# lego v5: `run` both issues and renews (renew when <4 days remain, or via ARI)
/usr/local/bin/lego run --accept-tos --domains "$SERVER_IP" --http --profile shortlived --pem --renew-days 4 2>&1

# Publish the current cert/key under stable names (docker-compose mounts these),
# right after a renewal — or on the very first run when they don't exist yet.
if [ ! -f .lego/certificates/current.crt ] || \
   find .lego/certificates -name "$SERVER_IP.crt" -mmin -3 | grep -q .; then
  cat ".lego/certificates/$SERVER_IP.crt" ".lego/certificates/$SERVER_IP.issuer.crt" \
      > .lego/certificates/current.crt
  cp ".lego/certificates/$SERVER_IP.key" .lego/certificates/current.key
  docker compose restart batch-chat
fi

