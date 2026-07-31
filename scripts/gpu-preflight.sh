#!/usr/bin/env bash
#
# Check root .env is complete BEFORE renting a GPU. Run: make gpu-preflight
#
# Every one of these is free to fix now and expensive to discover later — a missing SUPER_ADMIN
# means setup-motion-transfer.sh spins forever asking for it over a non-interactive SSH session
# with no stdin, and a missing CF_API_TOKEN means you rent a box, wait out the Postgres/ComfyUI
# install, and end up with no HTTPS domain to point the frontend at.
#
# Never prints a secret's value, only whether it is set — this output is safe to paste anywhere.
set -uo pipefail

G='\033[32m'; Y='\033[33m'; R='\033[31m'; D='\033[2m'; X='\033[0m'

if [ ! -f .env ]; then
  printf "${R}✗ no .env — copy .env.example to .env first${X}\n"
  exit 1
fi

get() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*#.*$//' | tr -d '"'; }

blocking=0
pending=0

check() {
  local key="$1" need="$2" why="$3" show="${4:-0}"
  local v; v="$(get "$key")"
  if [ -n "$v" ]; then
    if [ "$show" = "1" ]; then printf "  ${G}✓${X} %-22s ${D}%s${X}\n" "$key" "$v"
    else printf "  ${G}✓${X} %-22s ${D}set, %d chars${X}\n" "$key" "${#v}"; fi
    return
  fi
  case "$need" in
    after-rent)
      pending=$((pending + 1))
      printf "  ${D}·${X} %-22s ${D}(fill in after renting — make gpu-wait does this for SSH host/port)${X}\n" "$key" ;;
    recommended)
      printf "  ${Y}!${X} %-22s ${Y}missing${X} ${D}— %s${X}\n" "$key" "$why" ;;
    *)
      blocking=$((blocking + 1))
      printf "  ${R}✗${X} %-22s ${R}MISSING${X} ${D}— %s${X}\n" "$key" "$why" ;;
  esac
}

echo "Pod rental"
check GPU_PROVIDER required "vast | runpod — picks the CLI make gpu-provision/gpu-up/gpu-down use" 1
check GPU          required "vast.ai card filter, e.g. RTX_4090 — motion-transfer needs >=24GB VRAM" 1
check DISK          required "GB — DEPLOY.md minimum for the motion-transfer box is 120" 1
check MAX_DPH       required "\$/hour ceiling so a search never surprises you" 1

echo
echo "Backend deploy (passed straight to setup/setup-motion-transfer.sh on the pod)"
check DOMAIN        required "domain you control on Cloudflare — the box's HTTPS tunnel hostname" 1
check SUPER_ADMIN   required "only email that can log in as admin after deploy" 1
cf_api="$(get CF_API_TOKEN)"; cf_tunnel="$(get CF_TUNNEL_TOKEN)"
if [ -n "$cf_api" ]; then
  printf "  ${G}✓${X} %-22s ${D}set, %d chars (full auto: creates tunnel+DNS)${X}\n" "CF_API_TOKEN" "${#cf_api}"
elif [ -n "$cf_tunnel" ]; then
  printf "  ${G}✓${X} %-22s ${D}set, %d chars (reusing an EXISTING tunnel — see docs/gpu-pod.md#reusing-an-existing-tunnel-token)${X}\n" "CF_TUNNEL_TOKEN" "${#cf_tunnel}"
else
  blocking=$((blocking + 1))
  printf "  ${R}✗${X} %-22s ${R}MISSING${X} ${D}— need CF_API_TOKEN (Account·Tunnel:Edit + Zone·DNS:Edit + Zone·Zone:Read) or CF_TUNNEL_TOKEN — see docs/gpu-pod.md${X}\n" "CF_API_TOKEN/CF_TUNNEL_TOKEN"
fi
check GMAIL_USER    recommended "OTP login email — without it you must wire up SMTP another way"
check GMAIL_APP_PASSWORD recommended "pairs with GMAIL_USER (App Password, not the Gmail password)"
check CORS_ORIGINS  recommended "defaults to allow-all if unset — fine for a first deploy, not for prod" 1

echo
echo "After rent (make gpu-provision / gpu-wait fill these in for you)"
check GPU_INSTANCE_ID after-rent "make gpu-up / gpu-down / gpu-destroy" 1
check GPU_SSH_HOST    after-rent "make gpu-bootstrap" 1
check GPU_SSH_PORT    after-rent "make gpu-bootstrap" 1

echo

domain="$(get DOMAIN)"
if [ "$domain" = "motion-transfer.yourdomain.com" ]; then
  blocking=$((blocking + 1))
  echo -e "${R}✗ DOMAIN is still the .env.example placeholder — set your real domain${X}"
fi
admin="$(get SUPER_ADMIN)"
if [ "$admin" = "you@example.com" ]; then
  blocking=$((blocking + 1))
  echo -e "${R}✗ SUPER_ADMIN is still the .env.example placeholder — set your real email${X}"
fi
provider="$(get GPU_PROVIDER)"
if [ "$provider" = "runpod" ]; then
  echo -e "${Y}! GPU_PROVIDER=runpod — this path is less tested than vast (see docs/gpu-pod.md#runpod).${X}"
fi

if [ "$blocking" -gt 0 ]; then
  echo
  echo -e "${R}${blocking} blocking issue(s) — fix before renting. Everything above is free to fix now.${X}"
  echo
  exit 1
fi
echo -e "${G}✓ ready${X}$( [ "$pending" -gt 0 ] && echo " ${D}(${pending} field(s) still to fill in once the pod exists)${X}" )"
echo
echo "  next: $( [ "$pending" -gt 0 ] && echo "make gpu-provision" || echo "make gpu-up  →  make gpu-bootstrap" )"
echo
