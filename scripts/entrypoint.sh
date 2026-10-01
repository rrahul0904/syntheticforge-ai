#!/bin/sh
set -eu

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips "${SYNTHETICFORGE_TRUSTED_PROXY_IPS:-127.0.0.1,::1}" \
  --no-server-header \
  --timeout-keep-alive 15 \
  --limit-max-requests 10000
