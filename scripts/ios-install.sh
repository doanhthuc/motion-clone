#!/usr/bin/env bash
# Build the app for the connected iPhone, install it and launch it.
#
# Free provisioning expires the profile 7 days after install; once it does the app (and the
# "Save TikTok to Motion" shortcut) fails with "The request to open xyz.doanhthuc.motion failed".
# Re-running this renews it. The Keychain survives, so no secrets need re-entering.
# Needs the phone connected and unlocked, and an Apple account signed in under Xcode → Settings.
set -euo pipefail
cd "$(dirname "$0")/.."

# .env is not shell-safe (unquoted values with spaces), so read the one key instead of sourcing it.
IOS_DEVELOPMENT_TEAM=${IOS_DEVELOPMENT_TEAM:-$(grep -s '^IOS_DEVELOPMENT_TEAM=' .env | cut -d= -f2-)}
: "${IOS_DEVELOPMENT_TEAM:?set IOS_DEVELOPMENT_TEAM in .env (your personal team id)}"

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
# The device tunnel is opened on demand and closes when idle, so `list` alone reports a phone that is
# reachable over wifi ("available (paired)") as tunnelState=disconnected. Any call addressed to it wakes
# the tunnel (2026-09-30: `info details` flipped it to connected), which is what makes a phone with
# "Connect via network" ticked installable without the cable. Best effort: a phone that is off or
# asleep just stays disconnected and the filter below reports it.
xcrun devicectl list devices --json-output "$tmp" >/dev/null
for id in $(python3 -c "
import json, sys
for d in json.load(open(sys.argv[1]))['result']['devices']:
    if d['hardwareProperties'].get('reality') == 'physical' and d['connectionProperties'].get('pairingState') == 'paired':
        print(d['hardwareProperties']['udid'])" "$tmp"); do
  timeout 30 xcrun devicectl device info details --device "$id" >/dev/null 2>&1 || true
done
xcrun devicectl list devices --json-output "$tmp" >/dev/null
# Physical, connected devices only; IOS_DEVICE (name or UDID) picks one if several are plugged in.
udid=$(python3 - "$tmp" "${IOS_DEVICE:-}" <<'PY'
import json, sys
want = sys.argv[2]
devs = [d for d in json.load(open(sys.argv[1]))["result"]["devices"]
        if d["hardwareProperties"].get("reality") == "physical"
        and d["connectionProperties"].get("tunnelState") == "connected"]
if want:
    devs = [d for d in devs if want in (d["deviceProperties"]["name"], d["hardwareProperties"]["udid"])]
if len(devs) != 1:
    sys.exit("expected 1 connected iPhone, found %d (set IOS_DEVICE to pick)" % len(devs))
print(devs[0]["hardwareProperties"]["udid"])
PY
)

make ios-gen
derived=ios/.build-device
xcodebuild -project ios/MotionApp.xcodeproj -scheme MotionApp -configuration Debug \
  -destination "id=$udid" -derivedDataPath "$derived" -allowProvisioningUpdates \
  DEVELOPMENT_TEAM="$IOS_DEVELOPMENT_TEAM" -quiet build

app=$derived/Build/Products/Debug-iphoneos/Motion.app
xcrun devicectl device install app --device "$udid" "$app"
xcrun devicectl device process launch --device "$udid" --terminate-existing xyz.doanhthuc.motion
echo "installed; profile expires $(security cms -D -i "$app/embedded.mobileprovision" | plutil -extract ExpirationDate raw -o - -)"
