# Share-sheet TikTok import + deleting Telegram material — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Share a TikTok video to "Motion" from the iOS share sheet and have it land in Materials; let the app delete any material, including Telegram-owned ones, unless a draft or a busy run uses it.

**Architecture:** Server: `scripts/control/materials.py` drops the owner check and scans every `batch/*.draft.json`. iOS: a new `MotionShare` share extension (xcodegen target) reuses `MotionKit`'s `CredentialVault` and `MaterialsStore.importLink`; a pure `SharedLink.tiktok(in:)` picks the link. `Material.canDelete` goes away.

**Tech Stack:** Python 3 stdlib + unittest; Swift 6, SwiftUI, Swift Testing, xcodegen, iOS 26.

**Spec:** `docs/superpowers/specs/2026-09-25-share-extension-and-telegram-delete-design.md`

## Global Constraints

- Write in English (code, comments, commits). No `# #region ALD` markers.
- Repo is public: `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit. Never commit `ios/Secrets.xcconfig` or `.env`.
- iOS deployment target 26.0, `SWIFT_VERSION: "6.0"`, bundle id prefix `xyz.doanhthuc`; extension bundle id `xyz.doanhthuc.motion.share`.
- Keychain access group: `$(AppIdentifierPrefix)xyz.doanhthuc.motion` on both app and extension.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015rT3BsX7nZvMELYdKkEjT3
  ```
- Work on branch `share-extension-telegram-delete` (already checked out). Do not push.

## Review Focus

1. A Telegram draft naming `<file>.bak` must not block deleting `<file>` (whole-path match) — test in Task 1.
2. A Telegram draft that lists the file under `pending`/`basket` (not `slots`) still blocks — test in Task 1 (the scan is over the whole file text).
3. Shared text with prose around the link ("Check this out https://vt.tiktok.com/ZS…/ #fyp") → the link is extracted — test in Task 2.
4. A share with no TikTok link (e.g. a YouTube URL) → no network call, a clear message — test in Task 2 (`SharedLink` returns nil) and handled in Task 4's model.
5. The extension must call `completeRequest` on every exit (Close in each state, auto-dismiss on success) so the share sheet never hangs — reviewed in Task 4.

---

### Task 1: Server — delete any owner, refuse when any draft uses the file

**Files:**
- Modify: `scripts/control/materials.py` (`_in_use` ~line 253, `delete_material` ~line 289)
- Modify: `scripts/tests/test_batch_control_materials.py` (`TestDelete`, `TestDeleteVsAppDraft`)
- Modify: `scripts/tests/test_batch_control_http.py` (`test_delete_telegram_material_is_403` ~line 808)
- Modify: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` (§5.1 DELETE row, line ~157)

**Interfaces:**
- Produces: `DELETE /v1/materials/{owner}/{name}` → `204` for any owner; `409 in_use` with message `"a Telegram draft uses this file"` when a `batch/tg-*.draft.json` names it.

- [ ] **Step 1: Write the failing tests**

In `scripts/tests/test_batch_control_materials.py`, replace `test_telegram_material_is_forbidden` in `TestDelete` with:

```python
    def test_deletes_telegram_material(self):
        # 2026-09-25: the app can delete a file that arrived through the bot,
        # as long as no draft or busy run still names it.
        b = self.staging / "12345" / "b.png"
        materials.delete_material(self.staging, self.batch, "12345", "b.png")
        self.assertFalse(b.exists())
```

(`MaterialsBase.setUp` creates `self.b = self.staging / "12345" / "b.png"`; `self.batch` is the tmp root.)

Append to `TestDeleteVsAppDraft`:

```python
    def _staged(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        batch, staging = tmp / "batch", tmp / "batch" / "tg-staging"
        (staging / "777").mkdir(parents=True)
        used, free = staging / "777" / "t.png", staging / "777" / "t.png.bak"
        used.write_bytes(b"x"); free.write_bytes(b"x")
        return batch, staging, used, free

    def test_delete_refuses_material_a_telegram_draft_uses(self):
        batch, staging, used, free = self._staged()
        (batch / "tg-777.draft.json").write_text(
            json.dumps({"slots": {"character": str(used.resolve())}}, indent=2))
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(staging, batch, "777", "t.png")
        self.assertEqual(cm.exception.code, "in_use")
        self.assertEqual(cm.exception.message, "a Telegram draft uses this file")
        self.assertTrue(used.exists())
        materials.delete_material(staging, batch, "777", "t.png.bak")   # whole-path match only
        self.assertFalse(free.exists())

    def test_a_telegram_draft_blocks_from_pending_and_basket_too(self):
        # bot.py's _save_draft also writes staged paths under "pending" and
        # "basket"; the scan is over the whole file, not just "slots".
        batch, staging, used, _ = self._staged()
        (batch / "tg-777.draft.json").write_text(json.dumps(
            {"slots": {}, "pending": [[str(used.resolve()), {"kind": "image"}]], "basket": []}, indent=2))
        with self.assertRaises(materials.MaterialError) as cm:
            materials.delete_material(staging, batch, "777", "t.png")
        self.assertEqual(cm.exception.code, "in_use")
```

In `scripts/tests/test_batch_control_http.py`, replace `test_delete_telegram_material_is_403` with:

```python
    def test_delete_telegram_material_is_204(self):
        resp, body = self.send("DELETE", "/v1/materials/99/t.png")
        self.assertEqual((resp.status, body), (204, b""))
        self.assertFalse((self.batch / "tg-staging" / "99" / "t.png").exists())

    def test_delete_refused_while_a_telegram_draft_uses_it(self):
        staged = (self.batch / "tg-staging" / "99" / "t.png").resolve()
        (self.batch / "tg-99.draft.json").write_text(json.dumps({"slots": {"outfit": str(staged)}}))
        resp, body = self.send("DELETE", "/v1/materials/99/t.png")
        self.assertEqual((resp.status, json.loads(body)["error"]["code"]), (409, "in_use"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_materials.py' && python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py'`
Expected: FAIL — `forbidden` raised for Telegram owners / 403 instead of 204.

- [ ] **Step 3: Implement**

In `scripts/control/materials.py`, replace the draft block at the end of `_in_use` (the `draft = batch_dir / f"{APP_OWNER}.draft.json"` try/except) with a loop over every draft, and update the docstring's first sentence to "A busy run's manifest, or any draft (the app's or a Telegram chat's), names this file.":

```python
    # Drafts name files they have not run yet; deleting one would leave the
    # draft pointing at nothing. Not a manifest, so not gated on busy(): a
    # draft is always "in use". The app's draft is app.draft.json; each
    # Telegram chat's is tg-<chat>.draft.json (tgbot/bot.py:_save_draft, which
    # rewrites it after every update and lists paths under slots, pending and
    # basket — the pattern scans the whole text, so all three count).
    for draft in sorted(batch_dir.glob("*.draft.json")):
        try:
            if pattern.search(draft.read_text(encoding="utf-8", errors="replace")):
                return ("the app's draft uses this file" if draft.name == f"{APP_OWNER}.draft.json"
                        else "a Telegram draft uses this file")
        except OSError:
            continue
    return None
```

In `delete_material`, delete the block:

```python
    if owner != APP_OWNER:
        # Telegram's /clear and /wipe own the chat directories; deleting a file
        # a Telegram draft points at would break that draft with no message.
        raise MaterialError("forbidden", "only material uploaded from the app can be deleted here")
```

and put this comment in its place:

```python
    # Any owner since 2026-09-25: the Telegram-draft risk that used to justify
    # refusing non-app owners is now covered by _in_use scanning every draft.
```

Leave `test_owner_is_stripped_before_the_app_check` as is (it still passes).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make batch-test`
Expected: all OK.

- [ ] **Step 5: Amend the control-plane spec**

In `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.1 replace the row
`| \`DELETE /v1/materials/{id}\` | \`409\` if a draft or a live run references it |` with:

```
| `DELETE /v1/materials/{id}` | `409` if a draft or a live run references it. *Amended 2026-09-25:* any owner, not just `app` — `409 in_use` now covers every `batch/*.draft.json`, the app's and each Telegram chat's, which was the reason non-app owners used to be `403` |
```

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/materials.py scripts/tests/test_batch_control_materials.py scripts/tests/test_batch_control_http.py docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md
git commit -m "Materials: delete any owner, refused while any draft uses the file"   # plus the trailer lines
```

---

### Task 2: MotionKit — `SharedLink`, and delete for every material

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Models/Materials.swift` (remove `canDelete`; add `SharedLink` next to `TikTokLink` ~line 150)
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/MaterialsStore.swift` (`delete(_:)` guard)
- Modify: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift` (~line 149–151)
- Modify: `ios/MotionKit/Tests/MotionKitTests/MaterialsStoreTests.swift` (`deleteHonorsOwnershipAndServerOutcomes` ~line 178)
- Create: `ios/MotionKit/Tests/MotionKitTests/SharedLinkTests.swift`

**Interfaces:**
- Produces: `public enum SharedLink { public static func tiktok(in candidates: [String]) -> String? }` — first candidate (in order) containing a TikTok link, via `TikTokLink.find`.
- Produces: `Material` no longer has `canDelete`; `MaterialsStore.delete(_:)` always sends `DELETE`.

- [ ] **Step 1: Write the failing tests**

Create `ios/MotionKit/Tests/MotionKitTests/SharedLinkTests.swift`:

```swift
import Testing
@testable import MotionKit

@Suite struct SharedLinkTests {
    @Test func picksTheURLAttachment() {
        #expect(SharedLink.tiktok(in: ["https://vt.tiktok.com/ZSabc123/"]) == "https://vt.tiktok.com/ZSabc123/")
    }

    @Test func findsTheLinkInsideSharedProse() {
        let text = "Check this out https://vt.tiktok.com/ZSabc123/ #fyp"
        #expect(SharedLink.tiktok(in: [text]) == "https://vt.tiktok.com/ZSabc123/")
    }

    @Test func skipsCandidatesWithoutALink() {
        #expect(SharedLink.tiktok(in: ["Sốt Cà Chua", "https://www.tiktok.com/@a/video/1"])
                == "https://www.tiktok.com/@a/video/1")
    }

    @Test func nilWhenNothingIsTikTok() {
        #expect(SharedLink.tiktok(in: ["https://youtu.be/xyz", ""]) == nil)
        #expect(SharedLink.tiktok(in: []) == nil)
    }
}
```

In `ModelsTests.decodesMaterialsAndKeepsUnknownKinds`, delete the two `canDelete` expectations.

Replace `deleteHonorsOwnershipAndServerOutcomes` in `MaterialsStoreTests.swift` with:

```swift
    @Test func deleteSendsForEveryOwner() async throws {
        StubURLProtocol.install { request in
            if request.httpMethod == "DELETE" { return (204, [:], Data()) }
            return TestSupport.json(list)
        }
        let store = MaterialsStore(client: TestSupport.client())
        await store.refresh()
        let owned = try #require(store.materials.first)
        await store.delete(owned)
        #expect(store.materials.map(\.id) == ["42/driver.mp4"])

        // A Telegram chat's material: the server decides (2026-09-25), so the
        // request goes out and the row goes on a 204.
        let foreign = try #require(store.materials.first)
        await store.delete(foreign)
        #expect(store.materials.isEmpty)
        #expect(StubURLProtocol.requests.contains {
            $0.httpMethod == "DELETE" && $0.url?.path.hasSuffix("/v1/materials/42/driver.mp4") == true })
        #expect(store.errorMessage == nil)
    }
```

(If `StubURLProtocol.requests` is reset by `install`, keep the single `install` above so both DELETEs are recorded — do not re-install between the two deletes.)

- [ ] **Step 2: Run to verify failure**

Run: `make ios-test`
Expected: compile failure — `SharedLink` not found.

- [ ] **Step 3: Implement**

In `Materials.swift` delete `public var canDelete: Bool { owner == "app" }` and add after `TikTokLink`:

```swift
/// What a share sheet hands the extension, reduced to the one TikTok link in
/// it. TikTok shares a URL; other apps send text with prose around the link,
/// so every candidate goes through `TikTokLink.find`, in the order given.
public enum SharedLink {
    public static func tiktok(in candidates: [String]) -> String? {
        candidates.lazy.compactMap(TikTokLink.find(in:)).first
    }
}
```

In `MaterialsStore.delete(_:)` remove the `guard material.canDelete else { … }` block.

Then fix any remaining `canDelete` references in `ios/MotionApp` minimally so the app still compiles is Task 3's job — but `make ios-test` builds only MotionKit, so it passes here.

- [ ] **Step 4: Run to verify pass**

Run: `make ios-test`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit
git commit -m "MotionKit: SharedLink for the share extension; delete any material"   # plus trailers
```

---

### Task 3: App — Delete on every tile, refresh Materials on foreground

**Files:**
- Modify: `ios/MotionApp/Materials/MaterialTile.swift:31-33`
- Modify: `ios/MotionApp/RootView.swift:73-79`
- Modify: `ios/MotionApp/MotionApp.swift` (add `refreshMaterials()` to `AppModel`)

**Interfaces:**
- Consumes: `Material` without `canDelete` (Task 2).
- Produces: `AppModel.refreshMaterials()`.

- [ ] **Step 1: Implement**

`MaterialTile.swift`: replace

```swift
            if material.canDelete {
                Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
            }
```

with

```swift
            Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
```

`grep -rn canDelete ios/` must return nothing afterwards; fix any other hit the same way.

`MotionApp.swift`, in `AppModel` next to `resumeMaterialsUpload()`:

```swift
    /// A video shared to the Motion extension lands on the server while the
    /// app is in the background; re-reading on return puts it in the list.
    func refreshMaterials() {
        guard let materials else { return }
        Task { await materials.refresh() }
    }
```

`RootView.swift`, inside `if phase == .active {`, add `model.refreshMaterials()` after `model.resumeMaterialsUpload()`.

- [ ] **Step 2: Verify**

Run: `make ios-build`
Expected: `** BUILD SUCCEEDED **` (quiet mode prints nothing on success; exit code 0).

- [ ] **Step 3: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp
git commit -m "Materials: Delete on every tile; refresh the list when the app returns"   # plus trailers
```

---

### Task 4: `MotionShare` share extension

**Files:**
- Modify: `ios/project.yml` (new target; app entitlements + embed dependency)
- Create: `ios/MotionApp/MotionApp.entitlements` (generated by xcodegen from `entitlements.properties`; commit it)
- Create: `ios/MotionShare/ShareViewController.swift`
- Create: `ios/MotionShare/ShareImportView.swift`
- Create: `ios/MotionShare/MotionShare.entitlements` (generated; commit it)
- Create: `ios/MotionShare/Info.plist` (generated by xcodegen from `info.properties`; commit it, like `ios/MotionApp/Info.plist`)
- Modify: `ios/README.md` (a short "Share to Motion" section)

**Interfaces:**
- Consumes: `SharedLink.tiktok(in:)` (Task 2), `CredentialVault`, `KeychainStorage`, `APIClient(credentials:)`, `MaterialsStore(client:)`, `MaterialsStore.importLink(_:) -> Material?`, `MaterialsStore.errorMessage`.

- [ ] **Step 1: Add the target to `ios/project.yml`**

Under `targets.MotionApp`, add to `dependencies`:

```yaml
      - target: MotionShare
```

and add to the `MotionApp` target (sibling of `info:`):

```yaml
    entitlements:
      path: MotionApp/MotionApp.entitlements
      properties:
        # The app's default access group, spelled out so MotionShare can
        # read the same Keychain items (credentials written by Settings).
        keychain-access-groups: ["$(AppIdentifierPrefix)xyz.doanhthuc.motion"]
```

Add a new target under `targets:`:

```yaml
  MotionShare:
    type: app-extension
    platform: iOS
    sources:
      - path: MotionShare
        excludes: ["Info.plist"]
    configFiles:
      Debug: Secrets.xcconfig
      Release: Secrets.xcconfig
    dependencies:
      - package: MotionKit
        product: MotionKit
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: xyz.doanhthuc.motion.share
        PRODUCT_NAME: MotionShare
        SWIFT_VERSION: "6.0"
        TARGETED_DEVICE_FAMILY: "1"
        CODE_SIGN_STYLE: Automatic
    entitlements:
      path: MotionShare/MotionShare.entitlements
      properties:
        keychain-access-groups: ["$(AppIdentifierPrefix)xyz.doanhthuc.motion"]
    info:
      path: MotionShare/Info.plist
      properties:
        CFBundleDisplayName: Motion
        UIUserInterfaceStyle: Dark
        # Fallback for free provisioning: if the shared access group is not
        # granted, the extension seeds its own Keychain from these, like the app.
        CONTROL_API_URL: $(CONTROL_API_URL)
        CF_ACCESS_CLIENT_ID: $(CF_ACCESS_CLIENT_ID)
        CF_ACCESS_CLIENT_SECRET: $(CF_ACCESS_CLIENT_SECRET)
        CONTROL_API_TOKEN: $(CONTROL_API_TOKEN)
        NSExtension:
          NSExtensionPointIdentifier: com.apple.share-services
          NSExtensionPrincipalClass: $(PRODUCT_MODULE_NAME).ShareViewController
          NSExtensionAttributes:
            NSExtensionActivationRule:
              NSExtensionActivationSupportsWebURLWithMaxCount: 1
              NSExtensionActivationSupportsText: true
```

- [ ] **Step 2: Write `ios/MotionShare/ShareViewController.swift`**

```swift
import MotionKit
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// The share sheet's "Motion": hands the shared TikTok link to the same
/// `POST /v1/materials/link` the New Job paste field uses. The server stages
/// the file before it answers, so closing this early never loses the video.
final class ShareViewController: UIViewController {
    private let model = ShareImportModel()

    override func viewDidLoad() {
        super.viewDidLoad()
        model.onFinish = { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
        let host = UIHostingController(rootView: ShareImportView(model: model))
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)

        let items = extensionContext?.inputItems.compactMap { $0 as? NSExtensionItem } ?? []
        Task {
            let candidates = await Self.candidates(from: items)
            await model.start(candidates: candidates)
        }
    }

    /// URL attachments first (TikTok shares one), then plain text, then the
    /// item's own text — `SharedLink` picks the first TikTok link among them.
    private static func candidates(from items: [NSExtensionItem]) async -> [String] {
        var urls: [String] = []
        var texts: [String] = []
        for item in items {
            if let text = item.attributedContentText?.string { texts.append(text) }
            for provider in item.attachments ?? [] {
                if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
                   let url = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL {
                    urls.append(url.absoluteString)
                } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier),
                          let text = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) as? String {
                    texts.append(text)
                }
            }
        }
        return urls + texts
    }
}
```

If Swift 6 strict concurrency rejects `loadItem(...) as? URL` across the actor boundary, wrap the load in a `nonisolated static func` returning `String?` (convert to `String` inside it) — the goal is only that `Sendable` strings cross back.

- [ ] **Step 3: Write `ios/MotionShare/ShareImportView.swift`**

```swift
import MotionKit
import SwiftUI

@MainActor @Observable
final class ShareImportModel {
    enum Phase: Equatable {
        case working
        case done(name: String)
        case failed(String)
    }

    private(set) var phase: Phase = .working
    var onFinish: () -> Void = {}

    /// Close is offered while working and after a failure; success dismisses itself.
    var isDone: Bool {
        if case .done = phase { return true }
        return false
    }

    func start(candidates: [String]) async {
        guard let link = SharedLink.tiktok(in: candidates) else {
            phase = .failed("There is no TikTok link in what was shared.")
            return
        }
        let vault = CredentialVault(storage: KeychainStorage())
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        guard let credentials = vault.load() else {
            phase = .failed("Open Motion and fill in Settings first.")
            return
        }
        let store = MaterialsStore(client: APIClient(credentials: credentials))
        if let material = await store.importLink(link) {
            phase = .done(name: material.name)
            try? await Task.sleep(for: .seconds(1.5))
            onFinish()
        } else {
            phase = .failed(store.errorMessage ?? "The download failed.")
        }
    }
}

struct ShareImportView: View {
    let model: ShareImportModel

    var body: some View {
        VStack(spacing: 16) {
            switch model.phase {
            case .working:
                ProgressView().controlSize(.large)
                Text("Downloading to Materials…").font(.headline)
                Text("You can close this — the download continues.")
                    .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            case .done(let name):
                Image(systemName: "checkmark.circle.fill").font(.system(size: 44)).foregroundStyle(.green)
                Text("Added to Materials").font(.headline)
                Text(name).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            case .failed(let message):
                Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 44)).foregroundStyle(.orange)
                Text(message).font(.subheadline).multilineTextAlignment(.center)
            }
            if !model.isDone {
                Button("Close", action: model.onFinish).buttonStyle(.bordered)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}
```


- [ ] **Step 4: Generate and build**

Run: `make ios-build`
Expected: exit 0. Then confirm the extension is embedded:
`find ~/Library/Developer/Xcode/DerivedData -path '*Motion.app/PlugIns/MotionShare.appex' -maxdepth 8 | head -1` prints a path.
Also `git status` should show `ios/MotionShare/Info.plist`, both `.entitlements` files, and `ios/MotionApp.xcodeproj/project.pbxproj` changes — commit the plist/entitlements the way `ios/MotionApp/Info.plist` is committed. `ios/MotionApp.xcodeproj` is NOT tracked (generated) — do not add it.

- [ ] **Step 5: README**

Add to `ios/README.md` (near the Free provisioning section):

```markdown
## Share to Motion

The `MotionShare` extension adds **Motion** to the share sheet. In TikTok: Share → More (…) →
Motion. It posts the link to `POST /v1/materials/link`, so the video lands in Materials without
opening the app; closing the card early is safe (the server stages before it answers). It reads
the app's Keychain through the shared access group `$(AppIdentifierPrefix)xyz.doanhthuc.motion`,
and falls back to the baked `Secrets.xcconfig` values if free provisioning does not grant it.
First use: in the share sheet's app row tap More and enable Motion (or pin it to the top).
```

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/project.yml ios/MotionShare ios/MotionApp/MotionApp.entitlements ios/README.md
git commit -m "iOS: Share to Motion — import a TikTok link from the share sheet"   # plus trailers
```
