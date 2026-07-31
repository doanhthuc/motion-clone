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
check GPU          required "card filter — vast uses RTX_5090, RunPod uses 'NVIDIA GeForce RTX 5090'. Needs >=24GB VRAM" 1
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
echo "Speed-ups (skip re-downloading 33GB of models / re-installing every pod)"
check POD_VOLUME    recommended "RunPod Network Volume mount path, e.g. /workspace — without it every pod re-downloads ~33GB of models AND loses the database. docs/gpu-pod.md#network-volume" 1
check MODELS_MIN_GB recommended "threshold below which pod-volume.sh assumes the symlink points at an empty dir" 1
check MTC_PREBUILT  recommended "1 = pod image ships /opt/mtc-prebuilt, skips ~20-35 min of installing (needs worker-image/Dockerfile)" 1

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

vol="$(get POD_VOLUME)"
if [ -n "$vol" ] && [ "$provider" != "runpod" ]; then
  blocking=$((blocking + 1))
  echo -e "${R}✗ POD_VOLUME=$vol but GPU_PROVIDER=$provider — Network Volumes are RunPod-only.${X}"
  echo -e "${D}   vast.ai storage dies with the instance, so models would still re-download every rent.${X}"
  echo -e "${D}   Set GPU_PROVIDER=runpod, or clear POD_VOLUME.${X}"
elif [ -z "$vol" ] && [ "$provider" = "runpod" ]; then
  echo -e "${Y}! POD_VOLUME empty on RunPod — you are leaving the biggest win on the table${X}"
  echo -e "${D}   (~33GB model re-download + database loss on every pod). docs/gpu-pod.md#network-volume${X}"
fi
if [ -n "$vol" ] && [ "$provider" = "runpod" ]; then
  echo -e "${Y}! POD_VOLUME set: create the pod on the DASHBOARD, not 'make gpu-provision'${X}"
  echo -e "${D}   runpodctl cannot attach a Network Volume, and it cannot be attached after creation.${X}"
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
