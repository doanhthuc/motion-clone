# Motion — iPhone app

SwiftUI client for the control-plane API on `motion-vps`
(`docs/superpowers/specs/2026-09-22-swiftui-app-design.md`). Talks only to the VPS tunnel, never to a pod.

## Layout

- `MotionKit/` — Swift package: models, `APIClient`, Keychain vault, stores. No SwiftUI.
  Tested on the Mac with `make ios-test` (no simulator).
- `MotionApp/` — SwiftUI views only.
- `project.yml` — XcodeGen spec. `MotionApp.xcodeproj` and `MotionApp/Info.plist` are generated and gitignored.

## Build and install

    brew install xcodegen          # once
    make ios-secrets               # .env → ios/Secrets.xcconfig (gitignored)
    make ios-test ios-build        # free gates
    make ios-audio-test            # Silent Mode playback check; needs a booted simulator
    make ios-contract              # decode the live API with the app's models (GET only)
    open ios/MotionApp.xcodeproj   # pick your iPhone, Run

Set `IOS_DEVELOPMENT_TEAM=<your personal team id>` in `.env` so a regenerated project keeps
signing; otherwise pick the team once under Signing & Capabilities.

## Free provisioning

No paid Apple account, so the app expires 7 days after install. Re-run from Xcode to reinstall.
The Keychain survives this, so the secrets do not need re-entering. The secrets are seeded only into empty
Keychain entries: an edit made in Settings is never overwritten by a rebuild.

No push notifications: Telegram reports progress and results.
