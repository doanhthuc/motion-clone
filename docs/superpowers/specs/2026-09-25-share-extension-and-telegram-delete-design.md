# Share-sheet import and deleting Telegram material — design

Date: 2026-09-25. Status: approved in chat, being implemented on `share-extension-telegram-delete`.

## Goal

1. From TikTok's share sheet ("Share → More → Motion"), download the shared video straight into
   Motion's materials, without opening the app or copying the link by hand.
2. Let the app delete **any** material, including files that arrived through the Telegram bot —
   today only `owner == "app"` material is deletable.

## What already exists (read before building)

- `POST /v1/materials/link` (`scripts/control/links.py`) downloads a TikTok video with the bot's
  yt-dlp → tikwm path and stages it as an app material. It holds the request open for the whole
  download (40 s budget per path) and keeps going if the client disconnects: the file is staged
  before the response is written.
- `MaterialsStore.importLink(_:)` (MotionKit) already calls it, including the 524 ("still running")
  and 409 `busy` handling.
- `DELETE /v1/materials/{owner}/{name}` exists; `materials.delete_material` refuses non-app owners
  with `403 forbidden` and checks only `batch/app.draft.json` plus busy manifests for `in_use`.
- Material long-press menu already has Delete, gated on `Material.canDelete` (`owner == "app"`).

So (1) is an iOS-only change and (2) is a small server rule change plus an iOS gate removal.

## Rejected alternatives for (1)

- **Hand-built Shortcut** posting to the API: no code, but the bearer token and Cloudflare Access
  secret sit in plain text inside the Shortcut, and the server holds the request 40–80 s, longer
  than Shortcuts is known to wait comfortably.
- **App Intent + one-step Shortcut**: secrets stay safe, but a background intent's time budget is
  short for a 40–80 s download. No advantage over an extension.

## Part 1 — `MotionShare` Share Extension

- New target in `ios/project.yml`: `type: app-extension`, `NSExtensionPointIdentifier =
  com.apple.share-services`, bundle id `xyz.doanhthuc.motion.share`, display name "Motion",
  embedded in `MotionApp`, depends on `MotionKit`. Activation rule: at most one web URL, or text
  (TikTok hands over `https://vt.tiktok.com/…` as a URL; some apps send it as text).
- Principal class is a `UIViewController` hosting a SwiftUI view (`UIHostingController`). No
  storyboard.
- **Credentials.** App and extension both get the entitlement
  `keychain-access-groups = [$(AppIdentifierPrefix)xyz.doanhthuc.motion]`. That is the app's
  existing default access group, so items the app already wrote stay readable by the app and
  become readable by the extension. `KeychainStorage` does not set `kSecAttrAccessGroup`; with the
  entitlement present, reads search all the process's groups and writes go to the first one.
  Fallback if free provisioning refuses the entitlement: the extension gets the same four
  `Info.plist` keys from `Secrets.xcconfig` and calls `CredentialVault.seedIfEmpty` on its own
  keychain, exactly like the app. The build decides which one ships; record the outcome in this
  spec.
- **Flow.** Pull the link from the first `NSExtensionItem`'s attachments: `public.url` first, then
  `public.plain-text`. The pure part (choose a TikTok link from a list of candidate strings) lives
  in MotionKit as `SharedLink.tiktok(in:)` so it is unit-tested with `swift test`; it reuses
  `TikTokLink.find`. Then `MaterialsStore(client:).importLink(link)`, reusing its error mapping.
- **UI states** (a compact card, dark like the app):
  - *Downloading*: spinner, "Downloading to Materials…", a secondary line "You can close this —
    the download continues.", a **Close** button. Closing early is safe because of how the server
    stages before responding.
  - *Done*: checkmark and the material's name, auto-dismiss after 1.5 s.
  - *Failed*: the store's `errorMessage` (not TikTok, busy, TikTok refused, still running) and
    **Close**.
  - *No link / no credentials*: an explanatory message and **Close**, no network call.
- The extension calls `completeRequest` on every exit path so the share sheet never hangs.
- **Main app**: `RootView`'s `scenePhase == .active` handler also refreshes `MaterialsStore`, so a
  video shared while the app was in the background is in the list when the app comes back.

Memory: the extension only holds a JSON response, far below the 120 MB share-extension limit.

## Part 2 — deleting Telegram material

Server, `scripts/control/materials.py`:

- `delete_material` drops the `owner != APP_OWNER → forbidden` branch. Traversal/symlink refusal
  stays in `resolve_material` (still `404`).
- `_in_use` scans **every** `batch/*.draft.json` (the app's `app.draft.json` and each Telegram
  chat's `tg-<chat>.draft.json`) with the same whole-path pattern, instead of only the app's
  draft. Telegram drafts store staged files as absolute path strings under `slots`, `pending` and
  `basket` (`tgbot/bot.py:_save_draft`), and are rewritten atomically after every update, so a
  file a Telegram draft still points at is refused with `409 in_use`. Busy-manifest rule
  unchanged. Reason strings: `"the app's draft uses this file"` for the app draft, `"a Telegram
  draft uses this file"` for the others.
- Known window, accepted: the bot's in-memory draft is saved at the end of each update, so a file
  attached in the same instant as a delete can slip through — the same shape of race the app-draft
  check already accepts. Telegram's own 14-day staging prune already deletes files under drafts
  with no check at all, so this is no worse than today.

iOS: `Material.canDelete` is removed; the long-press menu offers Delete for every material and the
`MaterialsStore.delete` guard goes away. The server remains the authority (409 text is shown).

Control-plane spec §5.1 (`2026-09-21-vps-control-plane-api-design.md`) is amended in place: the
DELETE row now says any owner, `409` for any draft (app or Telegram) or a busy run.

## Testing

- Python (`scripts/tests/test_batch_control_http.py`): replace `test_delete_telegram_material_is_403`
  with `..._is_204`; add `409` when a `tg-<chat>.draft.json` names the file; add "a draft naming
  `<file>.bak` does not block `<file>`". `make batch-test`.
- Swift: `SharedLink` tests in MotionKit (`make ios-test`); `make ios-build` compiles app +
  extension.
- Live, $0 (VPS only, no pod): install on the phone, share a TikTok video to Motion, confirm it
  appears in Materials; delete a Telegram-owned material from the app.

## Deploy

`scripts/**` on `main` auto-deploys and restarts `motion-bot`. Before merging, check the VPS for a
live drain / Phase A / lease / migration (`batch/*.state.json`, `.env`'s `GPU_INSTANCE_ID`,
`pgrep -af 'drain.py|batch_run.py'`).
