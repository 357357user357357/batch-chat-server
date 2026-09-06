#!/bin/sh
# Renew the Let's Encrypt IP certificate (short-lived profile, ~6-day lifetime)
# and reload the container when it actually renewed.
cd /opt/batch-chat-server || exit 1
# lego v5: `run` both issues and renews (renew when <4 days remain, or via ARI)
/usr/local/bin/lego run --accept-tos --domains 194.36.85.208 --http --profile shortlived --pem --renew-days 4 2>&1
if find .lego/certificates -name "194.36.85.208.crt" -mmin -3 | grep -q .; then
  cat .lego/certificates/194.36.85.208.crt .lego/certificates/194.36.85.208.issuer.crt > .lego/certificates/194.36.85.208.fullchain.crt
  docker compose restart batch-chat
fi
