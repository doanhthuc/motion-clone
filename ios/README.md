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

## Materials and uploads

The Material tab lists the global VPS library, including files uploaded through Telegram. Thumbnails
use the same Cloudflare Access and bearer headers as the rest of the app. Only materials whose owner
is `app` can be deleted; the server keeps an in-use material and shows its `409` explanation.

Add accepts one image or video from Photos or Files. Uploads are serial and foreground-only. The app
copies the provider file into Application Support, sends server-sized chunks without reading the whole
file into memory, and checkpoints the active upload. If the network drops or the app is terminated,
open the app again to resume only the missing chunks. A probe warning remains visible until the process
ends; the current material-list API does not return old warnings after a cold launch.

This flow uses only the VPS material/upload routes and never rents a GPU pod.

### Phase 2 phone smoke

1. Open Material and confirm existing global items and thumbnails load.
2. Add a small image from Photos, then delete its app-owned card.
3. Add a video larger than 32 MiB. After at least one chunk, interrupt the network or terminate the
   app, reopen it, and confirm the upload resumes instead of restarting.
4. Confirm the completed video thumbnail and any amber warning, then verify Runs and Output still load.

Do not commit selected media, screenshots containing personal names, or upload checkpoint contents.
