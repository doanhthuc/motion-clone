#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
export PORT="${PORT:-2030}" HOST=0.0.0.0 NITRO_PORT="${PORT:-2030}" NITRO_HOST=0.0.0.0
exec node .output/server/index.mjs
