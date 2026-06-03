#!/bin/sh
set -eu

PROJECT_DIR=/opt/fund-estimator
CERT_DIR="$PROJECT_DIR/deploy/certs/letsencrypt"
CF_CREDS="$PROJECT_DIR/deploy/certs/cloudflare.ini"
CERTBOT_IMAGE=certbot/dns-cloudflare:latest

docker run --rm \
  -v "$CERT_DIR:/etc/letsencrypt" \
  -v "$CF_CREDS:/cloudflare.ini:ro" \
  "$CERTBOT_IMAGE" renew \
  --dns-cloudflare \
  --dns-cloudflare-credentials /cloudflare.ini \
  --quiet

cd "$PROJECT_DIR"
docker compose kill -s HUP fund-tools-proxy >/dev/null 2>&1 || docker compose restart fund-tools-proxy
