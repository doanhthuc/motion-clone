#!/usr/bin/env bash
# scripts/vps/api-smoke.sh — prove both auth layers on the phone API, through the real tunnel.
# Expects, in order: 403 from Cloudflare Access without its headers; 401 from the API with Access
# headers but no bearer; 200 with both. Reads CONTROL_API_URL, CF_ACCESS_CLIENT_ID,
# CF_ACCESS_CLIENT_SECRET and CONTROL_API_TOKEN from the root .env.
set -euo pipefail
cd "$(dirname "$0")/../.."
get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
URL="$(get CONTROL_API_URL)"; ID="$(get CF_ACCESS_CLIENT_ID)"
SECRET="$(get CF_ACCESS_CLIENT_SECRET)"; TOKEN="$(get CONTROL_API_TOKEN)"
for v in URL ID SECRET TOKEN; do
  [ -n "${!v}" ] || { echo "missing $v in .env" >&2; exit 2; }
done
code() { curl -s -o /dev/null -w '%{http_code}' "$@" "$URL/v1/health"; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1 → $2"; else echo "FAIL $1 → $2 (want $3)"; fail=1; fi; }
check "no Access headers"       "$(code)" 403
check "Access, no bearer"       "$(code -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET")" 401
check "Access + bearer"         "$(code -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" -H "Authorization: Bearer $TOKEN")" 200
exit $fail
