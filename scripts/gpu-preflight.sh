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
echo "Backend deploy (passed straight to the SETUP_PROFILE setup script on the pod)"
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
echo "Frontend on the pod (optional — leave FE_DOMAIN empty to keep running it locally)"
fe_domain="$(get FE_DOMAIN)"
if [ -n "$fe_domain" ]; then
  printf "  ${G}✓${X} %-22s ${D}%s${X}\n" "FE_DOMAIN" "$fe_domain"
  check FE_PORT recommended "port the Nuxt server listens on inside the pod — defaults to 2030" 1
else
  printf "  ${D}·${X} %-22s ${D}(empty — backend on the pod, frontend local via 'make dev')${X}\n" "FE_DOMAIN"
fi

echo
echo "Speed-ups (skip re-downloading 33GB of models / re-installing every pod)"
check POD_VOLUME    recommended "RunPod Network Volume mount path, e.g. /workspace — without it every pod re-downloads ~33GB of models AND loses the database. docs/gpu-pod.md#network-volume" 1
check MODELS_MIN_GB recommended "threshold below which pod-volume.sh assumes the symlink points at an empty dir" 1
check POD_VOLUME_ID recommended "RunPod volume id to attach — pod-provision.sh fills this in for you if you own exactly one" 1
check MIN_CUDA_VERSION recommended "passed to 'runpodctl pod create --min-cuda-version' — 13.0 keeps you off R570 hosts that fall back to cu128" 1
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
# FE on the pod: three ways to get a box that looks healthy and doesn't work. All free to catch here.
if [ -n "$fe_domain" ]; then
  cors="$(get CORS_ORIGINS)"
  case ",$cors," in
    *",https://$fe_domain,"*) ;;
    *)
      blocking=$((blocking + 1))
      echo -e "${R}✗ CORS_ORIGINS is missing https://$fe_domain${X}"
      echo -e "${D}   The frontend would load fine and every API call from the browser would fail CORS,${X}"
      echo -e "${D}   which looks exactly like a broken backend. Set:${X}"
      echo -e "${D}     CORS_ORIGINS=https://$fe_domain,http://localhost:2030${X}" ;;
  esac
  if [ "$fe_domain" = "$domain" ]; then
    blocking=$((blocking + 1))
    echo -e "${R}✗ FE_DOMAIN and DOMAIN are the same host — one tunnel cannot route one hostname to two ports.${X}"
  fi
  if [ -z "$cf_api" ]; then
    echo -e "${Y}! FE_DOMAIN set but no CF_API_TOKEN — the CF_TUNNEL_TOKEN path cannot create the second${X}"
    echo -e "${D}   Public Hostname. You would have to add $fe_domain → localhost:$( [ -n "$(get FE_PORT)" ] && get FE_PORT || echo 2030 ) on the Cloudflare${X}"
    echo -e "${D}   dashboard by hand. docs/gpu-pod.md#frontend-on-the-pod${X}"
  fi
fi

provider="$(get GPU_PROVIDER)"
if [ "$provider" = "runpod" ]; then
  echo -e "${Y}! GPU_PROVIDER=runpod — rewritten for runpodctl 2.8 but not yet run against a live pod.${X}"
  echo -e "${D}   Read the create command 'make gpu-provision' prints before you confirm it.${X}"
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
# runpodctl 2.8 attaches a volume at create time; older builds cannot, and would silently rent a
# pod with no volume — the exact silent-success this design exists to prevent. Gate on the flag.
if [ "$provider" = "runpod" ]; then
  if ! command -v runpodctl >/dev/null 2>&1; then
    blocking=$((blocking + 1))
    echo -e "${R}✗ runpodctl not installed — brew install runpod/runpodctl/runpodctl${X}"
  else
    if [ -n "$vol" ] && ! runpodctl pod create --help 2>&1 | grep -q -- '--network-volume-id'; then
      blocking=$((blocking + 1))
      echo -e "${R}✗ this runpodctl cannot attach a Network Volume (no --network-volume-id)${X}"
      echo -e "${D}   Upgrade:  brew upgrade runpodctl${X}"
    fi
    if ! runpodctl user -o json >/dev/null 2>&1; then
      blocking=$((blocking + 1))
      echo -e "${R}✗ runpodctl has no API key${X}"
      echo -e "${D}   Get one at runpod.io/console/user/settings, then:  runpodctl doctor${X}"
    elif [ -n "$vol" ] && [ -z "$(get POD_VOLUME_ID)" ]; then
      # "ready" has to mean the next command works. POD_VOLUME set with no volume in the account
      # means pod-provision.sh stops dead — better to say so here, for the price of one API call.
      if [ "$(runpodctl network-volume list -o json 2>/dev/null | tr -d '[:space:]')" = "[]" ]; then
        blocking=$((blocking + 1))
        echo -e "${R}✗ POD_VOLUME=$vol but this account has no Network Volume yet${X}"
        echo -e "${D}   Datacenter and size are both fixed at creation — pick a datacenter that stocks your GPU:${X}"
        echo -e "${D}     runpodctl gpu list -o json | grep -A4 '\"$(get GPU)\"'${X}"
        echo -e "${D}     runpodctl network-volume create --name motion --size 100 --data-center-id <DC>${X}"
        echo -e "${D}   Or clear POD_VOLUME to rent without one (models re-download every pod, ~33GB).${X}"
      fi
    fi
  fi
fi

if [ "$blocking" -gt 0 ]; then
  echo
  echo -e "${R}${blocking} blocking issue(s) — fix before renting. Everything above is free to fix now.${X}"
  echo
  exit 1
fi
echo -e "${G}✓ ready${X}$( [ "$pending" -gt 0 ] && echo " ${D}(${pending} field(s) still to fill in once the pod exists)${X}" )"
echo
if [ "$pending" -le 0 ]; then
  echo "  next: make gpu-up  →  make gpu-bootstrap"
else
  echo "  next: make gpu-provision"
fi
echo
