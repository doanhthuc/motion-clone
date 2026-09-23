#!/usr/bin/env bash
# Run the live Phase 3 smoke on an available iPhone Simulator and leave the VPS draft empty.
set -euo pipefail
cd "$(dirname "$0")/.."

get_env() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }

api_url="$(get_env CONTROL_API_URL)"
access_id="$(get_env CF_ACCESS_CLIENT_ID)"
access_secret="$(get_env CF_ACCESS_CLIENT_SECRET)"
api_token="$(get_env CONTROL_API_TOKEN)"
for value in api_url access_id access_secret api_token; do
  [ -n "${!value}" ] || { echo "missing $value in .env" >&2; exit 2; }
done

clear_draft() {
  curl -fsS -o /dev/null -X POST \
    -H "CF-Access-Client-Id: $access_id" \
    -H "CF-Access-Client-Secret: $access_secret" \
    -H "Authorization: Bearer $api_token" \
    "$api_url/v1/draft/clear"
}

devices="$(xcrun simctl list devices available)"
simulator_id="${IOS_SIMULATOR_ID:-}"
if [ -n "$simulator_id" ]; then
  if ! printf '%s\n' "$devices" | grep -F "$simulator_id" >/dev/null; then
    echo "IOS_SIMULATOR_ID is not an available simulator: $simulator_id" >&2
    exit 2
  fi
else
  simulator_id="$(printf '%s\n' "$devices" | awk -F '[()]' '/iPhone/ && /Booted/ { print $2; exit }')"
  if [ -z "$simulator_id" ]; then
    simulator_id="$(printf '%s\n' "$devices" | awk -F '[()]' '/iPhone/ { print $2; exit }')"
  fi
fi
[ -n "$simulator_id" ] || {
  echo "no available iPhone Simulator; install an iOS runtime in Xcode Settings" >&2
  exit 2
}
device_line="$(printf '%s\n' "$devices" | grep -F "$simulator_id" || true)"

booted_here=0
cleanup() {
  status=$?
  trap - EXIT
  if ! clear_draft; then
    echo "failed to clear the live draft after UI testing" >&2
    [ "$status" -ne 0 ] || status=1
  fi
  if [ "$booted_here" -eq 1 ]; then
    xcrun simctl shutdown "$simulator_id" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

if [[ "$device_line" != *"(Booted)"* ]]; then
  xcrun simctl boot "$simulator_id"
  booted_here=1
fi
xcrun simctl bootstatus "$simulator_id" -b

clear_draft
xcodebuild -project ios/MotionApp.xcodeproj -scheme MotionApp \
  -destination "platform=iOS Simulator,id=$simulator_id" \
  -collect-test-diagnostics never -quiet test
