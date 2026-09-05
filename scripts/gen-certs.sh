#!/usr/bin/env bash
# Generates a private CA + server certificate for HTTPS (TLS) on the VPS.
# The CA cert (ca.crt) is embedded into the Android app for cert pinning;
# ca.key and server.key must NEVER leave the server.
set -euo pipefail
cd "$(dirname "$0")/../certs"
mkdir -p .
IP="${1:?Usage: gen-certs.sh <server-ip> [extra SAN...]}"
SAN="IP:${IP}"
shift || true
for s in "$@"; do SAN="$SAN,IP:${s}"; done

if [ -f server.crt ] && [ -f ca.crt ]; then echo "certs already exist, skipping (delete ./certs to regenerate)"; exit 0; fi

openssl ecparam -name prime256v1 -genkey -noout -out ca.key
openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=BatchChat Root CA" -out ca.crt
openssl ecparam -name prime256v1 -genkey -noout -out server.key
openssl req -new -key server.key -subj "/CN=${IP}" -out server.csr
printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n" "$SAN" > ext.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 1825 -sha256 -out server.crt -extfile ext.cnf
rm -f server.csr ext.cnf ca.srl
chmod 600 ca.key server.key
echo "OK: ca.crt / server.crt / server.key generated (SAN: ${SAN})"