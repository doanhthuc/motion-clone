#!/usr/bin/env bash
set -euo pipefail

api="https://api.cloudflare.com/client/v4"
domain="${1:-${DOMAIN:-}}"
token="${CF_API_TOKEN:-}"

if [ -z "$domain" ]; then
  printf 'Domain cần check (vd minhtri-2-server.datools.info): '
  read -r domain
fi
domain="${domain#http://}"
domain="${domain#https://}"
domain="${domain%%/*}"
domain="${domain%.}"

if [ -z "$token" ]; then
  printf 'Cloudflare API Token: '
  read -rs token
  echo
fi

ok() { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
bad() { printf '\033[1;31m✗ %s\033[0m\n' "$*"; }

json_success() {
  python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin).get("success") else 1)' 2>/dev/null
}

json_error() {
  python3 -c 'import sys,json; e=json.load(sys.stdin).get("errors") or []; print(e[0].get("message","?") if e else "?")' 2>/dev/null
}

zone_lookup() {
  local host="$1" resp out
  while [ -n "$host" ] && [[ "$host" == *.* ]]; do
    resp="$(curl -s -G -H "Authorization: Bearer $token" --data-urlencode "name=$host" "$api/zones")"
    out="$(printf '%s' "$resp" | python3 -c '
import sys,json
try:
    r=(json.load(sys.stdin).get("result") or [])
    z=r[0] if r else {}
    print(z.get("id",""), (z.get("account") or {}).get("id",""), z.get("name",""))
except Exception:
    print("", "", "")
' 2>/dev/null)"
    [ -n "$(printf '%s' "$out" | awk '{print $1}')" ] && { printf '%s\n' "$out"; return 0; }
    host="${host#*.}"
  done
  return 1
}

echo "== Cloudflare token check =="
echo "Domain: $domain"

verify="$(curl -s -H "Authorization: Bearer $token" "$api/user/tokens/verify")"
if printf '%s' "$verify" | json_success; then
  ok "Token verify OK"
else
  bad "Token không verify được: $(printf '%s' "$verify" | json_error)"
  exit 1
fi

if ! zinfo="$(zone_lookup "$domain")"; then
  bad "Không tìm thấy zone cho '$domain'"
  warn "Token phải include domain gốc, ví dụ datools.info hoặc All zones."
  warn "Các zone token hiện nhìn thấy:"
  curl -s -H "Authorization: Bearer $token" "$api/zones?per_page=100" | python3 -c '
import sys,json
try:
    zones=(json.load(sys.stdin).get("result") or [])
    if not zones:
        print("  (không có zone nào)")
    for z in zones:
        print("  -", z.get("name","?"), "| account:", (z.get("account") or {}).get("name","?"))
except Exception as e:
    print("  (không đọc được danh sách zone:", e, ")")
'
  exit 1
fi

zid="$(printf '%s' "$zinfo" | awk '{print $1}')"
acc="$(printf '%s' "$zinfo" | awk '{print $2}')"
zname="$(printf '%s' "$zinfo" | awk '{print $3}')"
ok "Zone match: $domain -> $zname"

dns="$(curl -s -H "Authorization: Bearer $token" "$api/zones/$zid/dns_records?per_page=1")"
if printf '%s' "$dns" | json_success; then
  ok "Zone DNS permission OK"
else
  bad "Không đọc được DNS records: $(printf '%s' "$dns" | json_error)"
  warn "Cần quyền Zone · DNS · Edit trên zone $zname."
  exit 1
fi

tunnels="$(curl -s -H "Authorization: Bearer $token" "$api/accounts/$acc/cfd_tunnel?per_page=1&is_deleted=false")"
if printf '%s' "$tunnels" | json_success; then
  ok "Cloudflare Tunnel permission OK"
else
  bad "Không truy cập được Cloudflare Tunnel: $(printf '%s' "$tunnels" | json_error)"
  warn "Cần quyền Account · Cloudflare Tunnel · Edit đúng account chứa $zname."
  exit 1
fi

ok "Token dùng được cho setup auto tunnel."
