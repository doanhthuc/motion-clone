# SwiftUI App Phase 6 (Batch and Library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the iPhone app cross-build a batch (1 character × N outfits), seed jobs from saved try-ons, drop a bad try-on after Phase A, show compact batch progress, and manage the try-on library — with one read-only server field and no spend.

**Architecture:** Two new `@Observable @MainActor` stores in `MotionKit` — `TryonLibraryStore` and `BatchComposer` — both driving the existing `DraftStore`, which gains a combined slots+seed patch. `RunFlow` gains drop-after-Phase-A. A pure `BatchSummary` backs the run-detail list. The server's draft view reports each job's `tryon_seed` id. SwiftUI views stay thin; views never call `APIClient`.

**Tech Stack:** Swift 6 (strict concurrency), SwiftUI on iOS 26, swift-testing with `StubURLProtocol`, XCUITest, XcodeGen; Python `unittest` for the server field.

**Spec:** `docs/superpowers/specs/2026-09-23-swiftui-app-phase-6-design.md` (parent: `docs/superpowers/specs/2026-09-22-swiftui-app-design.md`; API: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.2, §5.3, §5.10).

## Global Constraints

- The only `scripts/**` change is Task 1 (`_view` reports `tryon_seed`). It auto-deploys `motion-bot` when merged, so the controller checks the VPS for a drain, Phase A, pod lease and migration before the merge. The app decodes `tryonSeed` as optional so either order of deploy works.
- Views never call `APIClient`; every network call lives in a `MotionKit` store.
- Nothing in Phase 6 spends: no `SpendGate`, no `Idempotency-Key`, no Phase A / regen / confirm / resume is sent by any new code or test.
- Every cross-build `PATCH` sends `tryon_seed` explicitly (an id or JSON `null`) together with `slots.outfit`; after the last outfit one `PATCH {"slots":{"outfit":null}}` (no `tryon_seed` key) leaves the edited job incomplete.
- Cross build: at most `BatchComposer.maxOutfits = 12` outfits; steps run sequentially; the first failure stops; `422 duplicate` from `add-to-batch` counts as done.
- Drop after Phase A: only when the batch has ≥ 2 entries, Phase A is not running, the phase is `.previews` or `.rentPanel`, and `RunFlow.canSpend` is true. Sequence: fresh `GET /v1/draft` → (edited job equals the entry ⇒ `PATCH` its clear role to `null`) → `DELETE /v1/draft/batch/{digest}` → `POST /v1/draft/validate` (timeout 95 s) → refresh previews (and the rent panel when on it).
- Library matching compares an entry's `material_ids` with the job's filled slots minus `driver`, exactly (dictionary equality); newest `saved_at` first.
- Draft-slot and validate calls use timeout 95 s (`DraftStore.slowDraftTimeout`).
- Zero-spend gates before every commit: `make ios-test`; plus `make ios-build` for any task touching `ios/MotionApp`; `make batch-test` for Task 1; `motions-studio/setup/scrub-secrets.sh --check` exits 0. Stage exact paths only. Never commit `ios/Secrets.xcconfig`, `.env`, `ios/MotionApp.xcodeproj` or live payloads.
- Live calls (`make ios-contract`, `make ios-ui-test`) are run by the controller, not implementers. `make ios-ui-test` must run outside the command sandbox.
- UI smoke uses only reads and free draft mutations; it never taps Preview try-on, Rent, Confirm, Kill, Migrate or a GPU row.
- Commit messages in English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings made while planning (recorded, binding on implementers)

- **Matching generalises the spec's `matches(character:outfit:background:)`** to `matches(slots:)` over every non-`driver` role, because the server's `tryon_save_info` records every non-driver input (it would include `mask`). Same result for the spec's three roles. Cost if wrong: a mask-bearing job gets no default seed.
- **`TryonLibraryEntry.savedAt` is `Double`** (epoch), matching every other timestamp model in MotionKit, not `Date`. The existing `TryonLibraryRecord` (Keep's response) stays as is.
- **`BatchComposer` and `TryonLibraryStore.use` go through `DraftStore`**, whose `mutate` now returns `Bool` (`@discardableResult`), so the busy guard, 404-material refresh and error text stay in one place. The composer reads `DraftStore.error` to recognise `422 duplicate`.
- **Batch mode's shared slots are the edited job's own slots** (assigned with the existing slot rows); the composer patches only `outfit` + `tryon_seed` per step. The spec's "PATCH shared + outfit" is therefore satisfied by the shared slots already being on the draft.
- **After a successful cross build the outfit selection is cleared**; after a failure it is kept so **Continue** re-plans.
- **The reuse hint (spec §5)** already exists verbatim in `RunFlowView`'s `.choiceRequired` case ("Reusing it spends no Gemini/Qwen quota…"); no copy change.
- **Batch elapsed** in run detail sums finished stages' `elapsed_sec` (a running stage reports `null`).

## File structure

| File | Responsibility |
|---|---|
| `scripts/control/drafts.py` (modify) | `_seed_id`, `tryon_seed` in `_view` |
| `scripts/tests/test_batch_control_drafts.py` (modify) | view reports the seed id |
| `ios/MotionKit/Sources/MotionKit/Models/Drafts.swift` (modify) | `Draft.tryonSeed`, `DraftBatchEntry.tryonSeed`, `filledSlots`, `DraftPatch` |
| `ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift` (modify) | `TryonLibraryEntry`, `TryonLibraryResponse` |
| `ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift` (modify) | `apply(_:)`, `Bool` results |
| `ios/MotionKit/Sources/MotionKit/Stores/TryonLibraryStore.swift` (create) | library list/image/delete/use/matches |
| `ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift` (create) | cross build |
| `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift` (modify) | drop after Phase A |
| `ios/MotionKit/Sources/MotionKit/Models/Runs.swift` (modify) | `BatchSummary`, `JobProgress.finishedSec` |
| `ios/MotionKit/Tests/MotionKitTests/*` | tests per task |
| `ios/MotionKit/Sources/motion-contract/main.swift` (modify) | `GET /v1/tryon-library` |
| `ios/MotionApp/MotionApp.swift`, `RootView.swift` (modify) | wiring, Material tab wrapper |
| `ios/MotionApp/Materials/SavedTryonsView.swift` (create) | library grid |
| `ios/MotionApp/Materials/MaterialTabView.swift` (create) | Materials | Saved try-ons |
| `ios/MotionApp/NewJob/NewJobView.swift` (modify) | Single | Batch |
| `ios/MotionApp/NewJob/BatchComposerSection.swift` (create) | batch composer UI + outfit multi-picker |
| `ios/MotionApp/RunFlow/TryonPreviewCard.swift` (modify) | drop button, seed badge |
| `ios/MotionApp/Runs/RunDetailView.swift` (modify) | batch progress list |
| `ios/MotionAppUITests/Phase6SmokeTests.swift` (create) | zero-spend batch smoke |
| docs (modify) | handoff, spec status, README |

---

### Task 1: Server — the draft view reports `tryon_seed`

**Files:**
- Modify: `scripts/control/drafts.py` (`_view`, around lines 299–327)
- Test: `scripts/tests/test_batch_control_drafts.py` (class `TestView`, line 156)

**Interfaces:**
- Produces: `GET /v1/draft` (and every draft mutation's response) gains top-level `"tryon_seed": str | None` and `batch[i]["tryon_seed"]: str | None` — the try-on library id.

- [ ] **Step 1: Write the failing tests** — append to `class TestView(StoreCase)`:

```python
    def test_fresh_view_has_no_seed(self):
        self.assertIsNone(self.store.view()["tryon_seed"])

    def test_view_reports_the_seed_as_a_library_id(self):
        # The phone shows a "saved try-on" badge from this and must not guess
        # it locally (Phase 6 spec §3). The id, never a path.
        self.fill()
        entry_id = self.saved_seed()
        v = self.store.patch({"tryon_seed": entry_id})
        self.assertEqual(v["tryon_seed"], entry_id)
        v = self.store.add_to_batch()
        self.assertEqual(v["batch"][0]["tryon_seed"], entry_id)
        v = self.store.patch({"tryon_seed": None})
        self.assertIsNone(v["tryon_seed"])
        self.assertEqual(v["batch"][0]["tryon_seed"], entry_id)
        self.assertNotIn(str(self.tmp), json.dumps(v))

    def test_a_deleted_entry_still_reports_its_id(self):
        self.fill()
        entry_id = self.saved_seed()
        self.store.patch({"tryon_seed": entry_id})
        self.library.delete(entry_id)
        self.assertEqual(self.store.view()["tryon_seed"], entry_id)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_drafts.py'`
Expected: 3 errors, `KeyError: 'tryon_seed'`.

- [ ] **Step 3: Implement** — in `scripts/control/drafts.py`, add above `class DraftStore`:

```python
def _seed_id(seed: Path | None) -> str | None:
    """The try-on library id a job's seed points at, for the draft view.

    TryonLibrary.save names every image `{id}{ext}`, so the stem is the id.
    Reported even when the entry was deleted since — the phone then says the
    saved try-on is gone instead of silently showing an ordinary job.
    """
    return seed.stem if seed else None
```

and in `_view`, add `"tryon_seed": _seed_id(job.tryon_seed),` after `"validated": d.validated,`, and in the `batch` comprehension add `"tryon_seed": _seed_id(b.tryon_seed),` after `"provider": b.provider,`.

- [ ] **Step 4: Run the whole batch suite**

Run: `make batch-test`
Expected: all pass (including the 3 new tests).

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/drafts.py scripts/tests/test_batch_control_drafts.py
git commit -m "feat(api): report each draft job's try-on seed id

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Models and `DraftStore.apply`

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Models/Drafts.swift`
- Modify: `ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift` (append after `TryonLibraryRecord`)
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift`, `ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift`

**Interfaces:**
- Produces:
  - `Draft.tryonSeed: String?`, `DraftBatchEntry.tryonSeed: String?`
  - `Draft.filledSlots: [String: String]`, `DraftBatchEntry.filledSlots: [String: String]`
  - `DraftPatch(slots: [String: String?] = [:], seed: DraftPatch.Seed = .keep)`, `DraftPatch.Seed { keep, clear, set(String) }`
  - `TryonLibraryEntry { id: String, materialIDs: [String: String], provider: String, savedAt: Double }` (`Identifiable`), `TryonLibraryResponse { entries }`
  - `DraftStore.apply(_ patch: DraftPatch) async -> Bool` (`@discardableResult`), `DraftStore.addToBatch() async -> Bool` (`@discardableResult`), `DraftStore.dropFromBatch(_:) async -> Bool` (`@discardableResult`)

- [ ] **Step 1: Write failing model tests** — append inside `ModelsTests` (plain suite, no stub):

```swift
    @Test func draftSeedIsOptionalAndDecodes() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let old = try decoder.decode(Draft.self, from: Fixtures.data(Fixtures.draft))
        #expect(old.tryonSeed == nil)
        #expect(old.batch[0].tryonSeed == nil)
        #expect(old.filledSlots == ["character": "app/model.png"])
        #expect(old.batch[0].filledSlots == ["character": "app/model.png", "outfit": "app/dress.png"])

        let seeded = Fixtures.draft
            .replacingOccurrences(of: #""estimate_min":null}"#, with: #""estimate_min":null,"tryon_seed":"s1"}"#)
            .replacingOccurrences(of: #""provider":"gemini","slots":{"character""#,
                                  with: #""provider":"gemini","tryon_seed":"s0","slots":{"character""#)
        let draft = try decoder.decode(Draft.self, from: Fixtures.data(seeded))
        #expect(draft.tryonSeed == "s1")
        #expect(draft.batch[0].tryonSeed == "s0")
    }

    @Test func draftPatchEncodesThreeSeedStates() throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = .sortedKeys
        func text(_ p: DraftPatch) throws -> String { String(decoding: try encoder.encode(p), as: UTF8.self) }
        #expect(try text(DraftPatch(slots: ["outfit": "app/o.png"], seed: .set("s1")))
                == #"{"slots":{"outfit":"app\/o.png"},"tryon_seed":"s1"}"#)
        #expect(try text(DraftPatch(slots: ["outfit": "app/o.png"], seed: .clear))
                == #"{"slots":{"outfit":"app\/o.png"},"tryon_seed":null}"#)
        #expect(try text(DraftPatch(slots: ["outfit": nil])) == #"{"slots":{"outfit":null}}"#)
        #expect(try text(DraftPatch(seed: .clear)) == #"{"tryon_seed":null}"#)
    }

    @Test func libraryEntriesDecode() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"{"entries":[{"id":"a1","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o.png"},"provider":"gemini","saved_at":1790000300.5}]}"#
        let response = try decoder.decode(TryonLibraryResponse.self, from: Fixtures.data(json))
        #expect(response.entries == [TryonLibraryEntry(id: "a1", materialIDs: ["character": "app/me.png", "outfit": "app/o.png"],
                                                       provider: "gemini", savedAt: 1790000300.5)])
    }
```

If `APIClient` decodes with a shared decoder factory, use it instead of the local `JSONDecoder` (check `ModelsTests` for the existing pattern and copy it).

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter ModelsTests`
Expected: compile errors (`tryonSeed`, `DraftPatch`, `TryonLibraryEntry` unknown).

- [ ] **Step 3: Implement the models** — in `Drafts.swift`:

`DraftBatchEntry`: add `public let tryonSeed: String?` after `slots`, and `case tryonSeed` to its `CodingKeys` (the decoder's snake-case conversion maps `tryon_seed` → `tryonSeed`).

`Draft`: add `public let tryonSeed: String?` after `dropped`.

Append:

```swift
extension Draft {
    /// The edited job's filled slots, role → material id (empty slots omitted).
    public var filledSlots: [String: String] { slots.compactMapValues(\.materialID) }
}

extension DraftBatchEntry {
    public var filledSlots: [String: String] { slots.compactMapValues { $0 } }
}

/// `PATCH /v1/draft` with slots and a try-on seed in one request (Phase 6).
/// `seed` is three-state: `.keep` omits the key, `.clear` sends `null`,
/// `.set(id)` names a try-on library entry. A `nil` slot value clears it.
public struct DraftPatch: Encodable, Sendable, Equatable {
    public enum Seed: Sendable, Equatable { case keep, clear, set(String) }

    public let slots: [String: String?]
    public let seed: Seed

    public init(slots: [String: String?] = [:], seed: Seed = .keep) {
        self.slots = slots
        self.seed = seed
    }

    private enum CodingKeys: String, CodingKey { case slots, tryonSeed }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if !slots.isEmpty { try container.encode(slots, forKey: .slots) }
        switch seed {
        case .keep: break
        case .clear: try container.encodeNil(forKey: .tryonSeed)
        case .set(let id): try container.encode(id, forKey: .tryonSeed)
        }
    }
}
```

In `RunFlow.swift`, after `TryonLibraryRecord`:

```swift
/// `GET /v1/tryon-library` → `{"entries": [...]}` (API spec §5.10).
public struct TryonLibraryEntry: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    /// Every non-driver input the try-on was made from (bot.py `tryon_save_info`).
    public let materialIDs: [String: String]
    public let provider: String
    public let savedAt: Double

    public init(id: String, materialIDs: [String: String], provider: String, savedAt: Double) {
        self.id = id
        self.materialIDs = materialIDs
        self.provider = provider
        self.savedAt = savedAt
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case materialIDs = "materialIds"
        case provider, savedAt
    }
}

public struct TryonLibraryResponse: Decodable, Sendable, Equatable {
    public let entries: [TryonLibraryEntry]
}
```

- [ ] **Step 4: Run model tests** — `swift test --filter ModelsTests` → PASS.

- [ ] **Step 5: Write the failing `DraftStore` test** — append in `DraftStoreTests`:

```swift
    @Test func applySendsSlotsAndSeedAndReportsTheResult() async throws {
        StubURLProtocol.install { request in
            switch (request.httpMethod, request.url?.path) {
            case ("GET", "/v1/pipelines"): return TestSupport.json(Fixtures.pipelines)
            case ("PATCH", _):
                let body = String(decoding: request.httpBody ?? Data(), as: UTF8.self)
                return body.contains("bad")
                    ? TestSupport.json(#"{"error":{"code":"not_local","message":"tryon_seed only applies to a local try-on provider"}}"#, status: 422)
                    : TestSupport.json(Fixtures.draft)
            default: return TestSupport.json(Fixtures.draft)
            }
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        let ok = await store.apply(DraftPatch(slots: ["outfit": "app/o.png"], seed: .set("s1")))
        #expect(ok)
        let sent = try #require(StubURLProtocol.requests.last)
        #expect(sent.timeoutInterval == 95)
        let body = try #require(JSONSerialization.jsonObject(with: sent.httpBody ?? Data()) as? [String: Any])
        #expect(body["tryon_seed"] as? String == "s1")
        #expect((body["slots"] as? [String: Any])?["outfit"] as? String == "app/o.png")

        let refused = await store.apply(DraftPatch(seed: .set("bad")))
        #expect(!refused)
        #expect(store.message == "tryon_seed only applies to a local try-on provider")
    }
```

(Check `APIError.userMessage` for 422: it returns the server text; if it prefixes anything, match the actual output.)

- [ ] **Step 6: Implement in `DraftStore`**

Change `mutate` to return `Bool` (`@discardableResult private func mutate(...) async -> Bool`): `return false` on the busy guard and in every `catch` path, `return true` after `accept(...)`. Mark `addToBatch`, `dropFromBatch` `@discardableResult ... async -> Bool` returning `await mutate { ... }`. Add:

```swift
    /// Slots and the try-on seed in one PATCH (Phase 6: cross build, "Use in job").
    /// Slow timeout: slot assignment probes the material on the server.
    @discardableResult
    public func apply(_ patch: DraftPatch) async -> Bool {
        await mutate(materialAssignment: !patch.slots.isEmpty) {
            try await self.client.patch(
                Draft.self, body: patch, timeout: Self.slowDraftTimeout, "v1", "draft")
        }
    }
```

Other public mutators keep returning `Void` (they call `await mutate {…}` and discard the result).

- [ ] **Step 7: Run** `cd ios/MotionKit && swift test` → all pass (existing 211 + new).

- [ ] **Step 8: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Models/Drafts.swift ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift \
        ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift \
        ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift
git commit -m "feat(ios): decode try-on seeds and patch slots with a seed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `TryonLibraryStore`

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/TryonLibraryStore.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/TryonLibraryStoreTests.swift`

**Interfaces:**
- Consumes: `TryonLibraryEntry`, `TryonLibraryResponse`, `DraftPatch`, `DraftStore.apply(_:)`, `Draft.filledSlots` (Task 2).
- Produces:
  - `TryonLibraryStore(client: APIClient, draft: DraftStore)`
  - `entries: [TryonLibraryEntry]` (newest first), `loaded`, `lastSuccess`, `error: APIError?`, `message: String?`, `isLoading`, `isStale`
  - `load() async`, `image(id: String) async -> Data?`, `delete(_ entry: TryonLibraryEntry) async`, `use(_ entry: TryonLibraryEntry) async -> Bool`, `dismissMessage()`
  - `matches(slots: [String: String]) -> [TryonLibraryEntry]`
  - `users(of id: String) -> [String]` — run ids of basket entries seeded from `id`, plus `"the job being edited"` when the edited job is.

- [ ] **Step 1: Write the failing tests** — `TryonLibraryStoreTests.swift`:

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct TryonLibraryStoreTests {
    static let library = #"""
    {"entries":[
      {"id":"old","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":100},
      {"id":"new","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"qwen-max","saved_at":200},
      {"id":"bg","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png","background":"app/bg.png"},"provider":"gemini","saved_at":300}
    ]}
    """#

    private func make() -> (TryonLibraryStore, DraftStore) {
        StubURLProtocol.install { request in
            let path = request.url?.path ?? ""
            switch (request.httpMethod ?? "GET", path) {
            case ("GET", "/v1/tryon-library"): return TestSupport.json(Self.library)
            case ("GET", "/v1/pipelines"): return TestSupport.json(Fixtures.pipelines)
            case ("GET", "/v1/draft"), ("PATCH", "/v1/draft"): return TestSupport.json(Fixtures.draft)
            case ("DELETE", "/v1/tryon-library/gone"):
                return TestSupport.json(#"{"error":{"code":"not_found","message":"no such try-on library entry"}}"#, status: 404)
            case ("DELETE", _): return TestSupport.json(#"{"ok":true}"#)
            case ("GET", _) where path.hasSuffix("/image"): return (200, ["Content-Type": "image/png"], Data("png".utf8))
            default: return (404, [:], Data())
            }
        }
        let client = TestSupport.client()
        let draft = DraftStore(client: client)
        return (TryonLibraryStore(client: client, draft: draft), draft)
    }

    @Test func loadSortsNewestFirst() async {
        let (store, _) = make()
        await store.load()
        #expect(store.entries.map(\.id) == ["bg", "new", "old"])
        #expect(store.loaded && !store.isStale)
    }

    @Test func matchesCompareEveryNonDriverRoleExactly() async {
        let (store, _) = make()
        await store.load()
        #expect(store.matches(slots: ["character": "app/me.png", "outfit": "app/o1.png",
                                      "driver": "app/dance.mp4"]).map(\.id) == ["new", "old"])
        #expect(store.matches(slots: ["character": "app/me.png", "outfit": "app/o1.png",
                                      "background": "app/bg.png"]).map(\.id) == ["bg"])
        #expect(store.matches(slots: ["character": "app/me.png", "outfit": "app/o2.png"]).isEmpty)
    }

    @Test func imagesAreCachedById() async {
        let (store, _) = make()
        await store.load()
        _ = await store.image(id: "new")
        _ = await store.image(id: "new")
        #expect(StubURLProtocol.requests.filter { $0.url?.path == "/v1/tryon-library/new/image" }.count == 1)
    }

    @Test func deleteRemovesTheEntryAndAGoneEntryToo() async {
        let (store, _) = make()
        await store.load()
        await store.delete(store.entries[0])
        #expect(store.entries.map(\.id) == ["new", "old"])
        await store.delete(TryonLibraryEntry(id: "gone", materialIDs: [:], provider: "gemini", savedAt: 1))
        #expect(store.message == "That saved try-on was already deleted.")
    }

    @Test func useSendsMaterialIdsAndSeedInOnePatch() async throws {
        let (store, draft) = make()
        await draft.load()
        await store.load()
        let ok = await store.use(store.entries[1])
        #expect(ok)
        let patch = try #require(StubURLProtocol.requests.last { $0.httpMethod == "PATCH" })
        let body = try #require(JSONSerialization.jsonObject(with: patch.httpBody ?? Data()) as? [String: Any])
        #expect(body["tryon_seed"] as? String == "new")
        #expect(body["slots"] as? [String: String] == ["character": "app/me.png", "outfit": "app/o1.png"])
    }
}
}
```

- [ ] **Step 2: Run to verify failure** — `swift test --filter TryonLibraryStoreTests` → compile error.

- [ ] **Step 3: Implement** `TryonLibraryStore.swift`:

```swift
import Foundation
import Observation

/// Saved try-ons (API spec §5.10): list, image, delete, and "Use in job" —
/// one `PATCH {slots, tryon_seed}` through `DraftStore`, so the image always
/// matches the materials it was made from (Phase 6 spec §6).
@MainActor @Observable
public final class TryonLibraryStore {
    public private(set) var entries: [TryonLibraryEntry] = []
    public private(set) var loaded = false
    public private(set) var lastSuccess: Date?
    public private(set) var error: APIError?
    public private(set) var message: String?
    public private(set) var isLoading = false

    private let client: APIClient
    private let draft: DraftStore
    private var images: [String: Data] = [:]

    public init(client: APIClient, draft: DraftStore) {
        self.client = client
        self.draft = draft
    }

    public var isStale: Bool { loaded && error != nil }

    public func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await client.get(TryonLibraryResponse.self, "v1", "tryon-library")
            entries = response.entries.sorted { $0.savedAt > $1.savedAt }
            let ids = Set(entries.map(\.id))
            images = images.filter { ids.contains($0.key) }
            loaded = true
            lastSuccess = .now
            error = nil
        } catch {
            self.error = error
            message = error.userMessage
        }
    }

    /// An entry's image never changes, so it is cached by id.
    public func image(id: String) async -> Data? {
        if let cached = images[id] { return cached }
        guard let data = try? await client.data("v1", "tryon-library", id, "image") else { return nil }
        images[id] = data
        return data
    }

    public func delete(_ entry: TryonLibraryEntry) async {
        message = nil
        do {
            try await client.delete("v1", "tryon-library", entry.id)
            forget(entry.id)
        } catch {
            if case .server(status: 404, code: _, message: _) = error {
                forget(entry.id)
                message = "That saved try-on was already deleted."
            } else {
                self.error = error
                message = error.userMessage
            }
        }
    }

    /// Fills the entry's materials and seeds the job with its image. The
    /// pipeline, provider and driver stay as they are; a pipeline that cannot
    /// use a seed answers 422 and its message is shown.
    public func use(_ entry: TryonLibraryEntry) async -> Bool {
        message = nil
        let slots = entry.materialIDs.mapValues { Optional($0) }
        let ok = await draft.apply(DraftPatch(slots: slots, seed: .set(entry.id)))
        if !ok { message = draft.message ?? draft.error?.userMessage }
        return ok
    }

    /// Entries made from exactly these materials (the driver is ignored: it is
    /// not part of a try-on image), newest first.
    public func matches(slots: [String: String]) -> [TryonLibraryEntry] {
        let wanted = slots.filter { $0.key != "driver" }
        return entries.filter { $0.materialIDs == wanted }
    }

    /// What in the current draft would lose its image if `id` were deleted.
    public func users(of id: String) -> [String] {
        guard let current = draft.draft else { return [] }
        var names = current.batch.filter { $0.tryonSeed == id }.map(\.runID)
        if current.tryonSeed == id { names.append("the job being edited") }
        return names
    }

    public func dismissMessage() { message = nil }

    private func forget(_ id: String) {
        entries.removeAll { $0.id == id }
        images[id] = nil
    }
}
```

If `client.get` is declared `throws(APIError)`, `error` in the catch is already `APIError`; otherwise map with `error as? APIError ?? .transport(error.localizedDescription)` as `DraftStore.apiError` does.

- [ ] **Step 4: Run** `swift test` → all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/TryonLibraryStore.swift ios/MotionKit/Tests/MotionKitTests/TryonLibraryStoreTests.swift
git commit -m "feat(ios): add the try-on library store

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `BatchComposer` (cross build)

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift`

**Interfaces:**
- Consumes: `DraftStore` (`draft`, `selectedPipeline`, `isBusy`, `apply(_:)`, `addToBatch()`, `refresh()`, `error`, `message`), `TryonLibraryStore.matches(slots:)`, `Draft.filledSlots`, `DraftBatchEntry.filledSlots`, `DraftPatch` (Tasks 2–3).
- Produces:
  - `CrossOutfit { outfitID: String, seedID: String? }` (`Identifiable`, `id == outfitID`)
  - `BatchComposer(draft: DraftStore, library: TryonLibraryStore)`
  - `static maxOutfits = 12`, `static outfitRole = "outfit"`, `static supports(_ pipeline: Pipeline) -> Bool`
  - `outfits: [CrossOutfit]` (read-only outside), `isRunning`, `progress: BatchComposer.Progress?` (`done`, `total`), `failure: String?`, `lastAdded: Int?`
  - `sharedSlots: [String: String]`, `missingShared: [String]`, `canRun: Bool`
  - `toggle(outfitID:)`, `setSeed(_ seedID: String?, for outfitID: String)`, `matches(for outfitID: String) -> [TryonLibraryEntry]`, `refreshSeeds()`, `pending(in: Draft) -> [CrossOutfit]`, `run() async`

- [ ] **Step 1: Write the failing tests** — `BatchComposerTests.swift`. A small stateful fake of the draft routes keeps the assertions about requests, not canned responses:

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct BatchComposerTests {
    /// Mirrors drafts.py: add-to-batch copies the edited job (seed included)
    /// and refuses an exact copy with 422 duplicate.
    final class FakeDraftServer: @unchecked Sendable {
        private let lock = NSLock()
        private var shared: [String: String] = ["character": "app/me.png", "driver": "app/dance.mp4"]
        private var outfit: String?
        private var seed: String?
        private var batch: [(slots: [String: String], seed: String?)] = []
        private var failPatchFor: String?
        var library = #"{"entries":[]}"#

        func failNextPatch(for outfit: String) { lock.withLock { failPatchFor = outfit } }
        func preload(outfit: String, seed: String?) {
            lock.withLock { batch.append((shared.merging(["outfit": outfit]) { $1 }, seed)) }
        }

        func answer(_ r: URLRequest) -> (Int, [String: String], Data) {
            lock.withLock {
                switch (r.httpMethod ?? "GET", r.url?.path ?? "") {
                case ("GET", "/v1/pipelines"): return TestSupport.json(Fixtures.pipelines)
                case ("GET", "/v1/tryon-library"): return TestSupport.json(library)
                case ("GET", "/v1/draft"): return TestSupport.json(json())
                case ("PATCH", "/v1/draft"):
                    let body = (try? JSONSerialization.jsonObject(with: r.httpBody ?? Data())) as? [String: Any] ?? [:]
                    if let slots = body["slots"] as? [String: Any], slots.keys.contains("outfit") {
                        let value = slots["outfit"] as? String
                        if let fail = failPatchFor, fail == value {
                            failPatchFor = nil
                            return TestSupport.json(#"{"error":{"code":"unprobeable","message":"could not read o2"}}"#, status: 422)
                        }
                        outfit = value
                    }
                    if body.keys.contains("tryon_seed") { seed = body["tryon_seed"] as? String }
                    return TestSupport.json(json())
                case ("POST", "/v1/draft/add-to-batch"):
                    let slots = shared.merging(outfit.map { ["outfit": $0] } ?? [:]) { $1 }
                    if batch.contains(where: { $0.slots == slots && $0.seed == seed }) {
                        return TestSupport.json(#"{"error":{"code":"duplicate","message":"that exact job is already in the batch"}}"#, status: 422)
                    }
                    batch.append((slots, seed))
                    return TestSupport.json(json())
                default: return (404, [:], Data())
                }
            }
        }

        private func json() -> String {
            let probe: [String: Any] = ["kind": "image", "width": 1, "height": 1, "duration_s": NSNull(),
                                        "bitrate_kbps": NSNull(), "size_bytes": 1, "warning": ""]
            let current = shared.merging(outfit.map { ["outfit": $0] } ?? [:]) { $1 }
            let slots = current.mapValues { id -> [String: Any] in
                ["material_id": id, "name": id, "exists": true, "probe": probe, "warning": ""] }
            let missing = ["character", "driver", "outfit"].filter { current[$0] == nil }
            let entries: [[String: Any]] = batch.enumerated().map { i, entry in
                ["digest": "d\(i)", "run_id": "run\(i)", "pipeline": "tryon-motion-enhance", "provider": "gemini",
                 "slots": entry.slots, "tryon_seed": entry.seed ?? NSNull()] }
            let draft: [String: Any] = [
                "owner": "app", "pipeline": "tryon-motion-enhance", "provider": "gemini", "generation": batch.count,
                "slots": slots, "required": ["character", "driver", "outfit"], "optional": ["mask"],
                "missing": missing, "validated": NSNull(), "batch": entries, "jobs": batch.count,
                "estimate_min": NSNull(), "tryon_seed": seed ?? NSNull()]
            return String(decoding: try! JSONSerialization.data(withJSONObject: draft), as: UTF8.self)
        }
    }

    private func make(_ server: FakeDraftServer) async -> (BatchComposer, DraftStore) {
        StubURLProtocol.install { server.answer($0) }
        let client = TestSupport.client()
        let draft = DraftStore(client: client)
        let library = TryonLibraryStore(client: client, draft: draft)
        await draft.load()
        await library.load()
        return (BatchComposer(draft: draft, library: library), draft)
    }

    private func writes() -> [(String, String, [String: Any]?)] {
        StubURLProtocol.requests.filter { $0.httpMethod != "GET" }.map { r in
            (r.httpMethod ?? "", r.url?.path ?? "",
             (try? JSONSerialization.jsonObject(with: r.httpBody ?? Data())) as? [String: Any])
        }
    }

    @Test func threeOutfitsAreThreePatchAddPairsThenTheOutfitIsCleared() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        composer.toggle(outfitID: "app/o3.png")
        composer.setSeed("s2", for: "app/o2.png")
        #expect(composer.canRun)

        await composer.run()

        let w = writes()
        #expect(w.map { "\($0.0) \($0.1)" } == [
            "PATCH /v1/draft", "POST /v1/draft/add-to-batch",
            "PATCH /v1/draft", "POST /v1/draft/add-to-batch",
            "PATCH /v1/draft", "POST /v1/draft/add-to-batch",
            "PATCH /v1/draft"])
        // Every outfit PATCH names its seed explicitly — the edited job keeps
        // the previous one's seed otherwise (spec §2).
        #expect(w[0].2?["tryon_seed"] is NSNull)
        #expect(w[2].2?["tryon_seed"] as? String == "s2")
        #expect(w[4].2?["tryon_seed"] is NSNull)
        #expect((w[4].2?["slots"] as? [String: Any])?["outfit"] as? String == "app/o3.png")
        // The last PATCH clears the outfit and does not touch the seed.
        #expect((w[6].2?["slots"] as? [String: Any])?["outfit"] is NSNull)
        #expect(w[6].2?.keys.contains("tryon_seed") == false)
        #expect(draft.draft?.batch.count == 3)
        #expect(draft.draft?.missing == ["outfit"])
        #expect(composer.failure == nil && composer.lastAdded == 3 && composer.outfits.isEmpty)
    }

    @Test func aFailureStopsAndContinueSkipsWhatWasAdded() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        for o in ["app/o1.png", "app/o2.png", "app/o3.png"] { composer.toggle(outfitID: o) }
        server.failNextPatch(for: "app/o2.png")

        await composer.run()
        #expect(composer.progress == .init(done: 1, total: 3))
        #expect(composer.failure?.contains("app/o2.png") == true)
        #expect(composer.failure?.contains("could not read o2") == true)
        #expect(composer.outfits.count == 3)
        #expect(draft.draft?.batch.count == 1)

        await composer.run()
        let adds = writes().filter { $0.1 == "/v1/draft/add-to-batch" }
        #expect(adds.count == 3)            // o1 once, then o2 and o3 — never o1 again
        #expect(draft.draft?.batch.count == 3)
        #expect(composer.failure == nil)
    }

    @Test func aDuplicateCountsAsDone() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        server.preload(outfit: "app/o1.png", seed: nil)   // added elsewhere after our last read

        await composer.run()
        #expect(composer.failure == nil)
        #expect(composer.lastAdded == 1)
        #expect(draft.error == nil)
    }

    @Test func seedsDefaultToTheNewestMatchAndCapAtTwelve() async {
        let server = FakeDraftServer()
        server.library = #"""
        {"entries":[{"id":"old","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1},
                    {"id":"new","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":2}]}
        """#
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        #expect(composer.outfits.map(\.seedID) == ["new", nil])
        #expect(composer.matches(for: "app/o1.png").map(\.id) == ["new", "old"])
        for i in 3...20 { composer.toggle(outfitID: "app/x\(i).png") }
        #expect(composer.outfits.count == BatchComposer.maxOutfits)
        composer.toggle(outfitID: "app/o1.png")
        #expect(!composer.outfits.contains { $0.outfitID == "app/o1.png" })
    }

    @Test func onlyCharacterAndOutfitPipelinesQualify() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let catalog = try decoder.decode(PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines))
        #expect(catalog.pipelines.filter(BatchComposer.supports).map(\.id) == ["tryon-motion-enhance"])
    }
}
}
```

- [ ] **Step 2: Run to verify failure** — `swift test --filter BatchComposerTests` → compile error.

- [ ] **Step 3: Implement** `BatchComposer.swift`:

```swift
import Foundation
import Observation

/// One outfit of a cross build and the saved try-on to seed it from.
public struct CrossOutfit: Sendable, Equatable, Identifiable {
    public let outfitID: String
    public var seedID: String?
    public var id: String { outfitID }

    public init(outfitID: String, seedID: String?) {
        self.outfitID = outfitID
        self.seedID = seedID
    }
}

/// Cross build (Phase 6 spec §4): the edited job's own slots are shared, and
/// each outfit becomes one basket job through `PATCH {slots.outfit,
/// tryon_seed}` + `add-to-batch` — no new route (API spec §5.10). Free: no
/// spend gate. Runs sequentially and stops at the first failure; running
/// again re-plans from the server's draft, so nothing is added twice.
@MainActor @Observable
public final class BatchComposer {
    public struct Progress: Equatable, Sendable {
        public var done: Int
        public let total: Int
        public init(done: Int, total: Int) { self.done = done; self.total = total }
    }

    /// A mistap guard, not a server limit: Phase A runs one try-on per outfit
    /// and the rent estimate grows linearly with N.
    public static let maxOutfits = 12
    public static let outfitRole = "outfit"

    public private(set) var outfits: [CrossOutfit] = []
    public private(set) var isRunning = false
    public private(set) var progress: Progress?
    public private(set) var failure: String?
    /// How many outfits the last complete run left in the basket.
    public private(set) var lastAdded: Int?

    private let draft: DraftStore
    private let library: TryonLibraryStore

    public init(draft: DraftStore, library: TryonLibraryStore) {
        self.draft = draft
        self.library = library
    }

    public static func supports(_ pipeline: Pipeline) -> Bool {
        let roles = Set(pipeline.required + pipeline.optional)
        return roles.contains("character") && roles.contains(outfitRole)
    }

    /// Every filled slot of the edited job except the outfit.
    public var sharedSlots: [String: String] {
        (draft.draft?.filledSlots ?? [:]).filter { $0.key != Self.outfitRole }
    }

    /// Required roles still empty, the outfit excluded (the list fills it).
    public var missingShared: [String] {
        (draft.draft?.missing ?? []).filter { $0 != Self.outfitRole }
    }

    public var canRun: Bool {
        !isRunning && !draft.isBusy && missingShared.isEmpty && !outfits.isEmpty
            && outfits.count <= Self.maxOutfits
            && draft.selectedPipeline.map(Self.supports) == true
    }

    public func toggle(outfitID: String) {
        guard !isRunning else { return }
        if let index = outfits.firstIndex(where: { $0.outfitID == outfitID }) {
            outfits.remove(at: index)
        } else if outfits.count < Self.maxOutfits {
            outfits.append(CrossOutfit(outfitID: outfitID, seedID: matches(for: outfitID).first?.id))
        }
        lastAdded = nil
    }

    public func setSeed(_ seedID: String?, for outfitID: String) {
        guard !isRunning, let index = outfits.firstIndex(where: { $0.outfitID == outfitID }) else { return }
        outfits[index].seedID = seedID
    }

    public func matches(for outfitID: String) -> [TryonLibraryEntry] {
        library.matches(slots: sharedSlots.merging([Self.outfitRole: outfitID]) { $1 })
    }

    /// After the shared slots change, earlier matches name other materials.
    public func refreshSeeds() {
        guard !isRunning else { return }
        outfits = outfits.map { CrossOutfit(outfitID: $0.outfitID, seedID: matches(for: $0.outfitID).first?.id) }
    }

    /// Outfits not yet in the basket as exactly this job.
    public func pending(in current: Draft) -> [CrossOutfit] {
        let shared = current.filledSlots.filter { $0.key != Self.outfitRole }
        return outfits.filter { outfit in
            let slots = shared.merging([Self.outfitRole: outfit.outfitID]) { $1 }
            return !current.batch.contains { entry in
                entry.pipeline == current.pipeline && entry.provider == current.provider
                    && entry.filledSlots == slots && entry.tryonSeed == outfit.seedID
            }
        }
    }

    public func run() async {
        guard canRun, let current = draft.draft else { return }
        isRunning = true
        failure = nil
        lastAdded = nil
        defer { isRunning = false }
        let steps = pending(in: current)
        progress = Progress(done: outfits.count - steps.count, total: outfits.count)
        for outfit in steps {
            let seed: DraftPatch.Seed = outfit.seedID.map { .set($0) } ?? .clear
            guard await draft.apply(DraftPatch(slots: [Self.outfitRole: outfit.outfitID], seed: seed)) else {
                return stop(at: outfit)
            }
            if !(await draft.addToBatch()) {
                guard case .server(status: 422, code: "duplicate", message: _) = draft.error else {
                    return stop(at: outfit)
                }
                await draft.refresh()
            }
            progress?.done += 1
        }
        // add-to-batch leaves the edited job a copy of the last outfit; were
        // that basket entry dropped later, the copy would count as a job again.
        if !(await draft.apply(DraftPatch(slots: [Self.outfitRole: nil]))) {
            failure = "All outfits were added, but the outfit slot could not be cleared: "
                + (draft.message ?? draft.error?.userMessage ?? "unknown error")
        }
        lastAdded = outfits.count
        outfits = []
        progress = nil
    }

    private func stop(at outfit: CrossOutfit) {
        let reason = draft.message ?? draft.error?.userMessage ?? "unknown error"
        failure = "Stopped at \(progress?.done ?? 0)/\(progress?.total ?? outfits.count) — \(outfit.outfitID): \(reason)"
    }
}
```

Note: `DraftStore.refresh()` guards `!isRefreshing && !isBusy`; it runs after `mutate` has returned, so `isBusy` is false. It clears `error` on success, which the duplicate test asserts.

- [ ] **Step 4: Run** `swift test` → all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift
git commit -m "feat(ios): cross-build a batch of outfits from one draft

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `RunFlow` — drop a try-on after Phase A

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift`

**Interfaces:**
- Consumes: `Draft.filledSlots`, `DraftBatchEntry.filledSlots`, `DraftBatchEntry.tryonSeed`, `Draft.tryonSeed` (Task 2); existing `SlotPatch`, `DraftValidationResponse`, `client.patch/delete/post`.
- Produces: `RunFlow.batchEntry(for: TryonPreview) -> DraftBatchEntry?`, `RunFlow.isSeeded(_: TryonPreview) -> Bool`, `RunFlow.isDropping: Bool`, `RunFlow.canDropFromBatch: Bool`, `RunFlow.drop(_ preview: TryonPreview) async`.

- [ ] **Step 1: Write the failing tests** — in `RunFlowTests.swift`, extend `Routes.answer` with (above the `/tryon` cases, since order matters):

```swift
            case path.hasPrefix("/v1/draft/batch/"): return TestSupport.json(Self.draftAfterDrop)
            case path == "/v1/draft/validate":
                return TestSupport.json(#"{"valid":true,"stale":false,"output":null,"draft":"# + Self.draftAfterDrop + "}")
```

and add fixtures inside `Routes`:

```swift
        static func quoted(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        static func draftJSON(batch: [String], outfit: String, seed: String?) -> String {
            let probe = #"{"kind":"image","width":1,"height":1,"duration_s":null,"bitrate_kbps":null,"size_bytes":1,"warning":""}"#
            let slots = ["character": "app/model.png", "driver": "app/dance.mp4", "outfit": outfit]
                .sorted { $0.key < $1.key }
                .map { "\"\($0.key)\":{\"material_id\":\"\($0.value)\",\"name\":\"x\",\"exists\":true,\"probe\":\(probe),\"warning\":\"\"}" }
                .joined(separator: ",")
            return #"{"owner":"app","pipeline":"tryon-motion-enhance","provider":"gemini","generation":9,"slots":{"#
                + slots + #"},"required":["character","driver","outfit"],"optional":["mask"],"missing":[],"validated":true,"batch":["#
                + batch.joined(separator: ",") + #"],"jobs":2,"estimate_min":84,"tryon_seed":"# + quoted(seed) + "}"
        }
        static func entry(_ digest: String, _ run: String, _ outfit: String, seed: String? = nil) -> String {
            #"{"digest":"\#(digest)","run_id":"\#(run)","pipeline":"tryon-motion-enhance","provider":"gemini","slots":{"character":"app/model.png","driver":"app/dance.mp4","outfit":"\#(outfit)"},"tryon_seed":"#
                + quoted(seed) + "}"
        }
        /// Two jobs; the edited job is still a copy of the second, seed included
        /// (as add-to-batch leaves it).
        static let draftTwoJobs = draftJSON(
            batch: [entry("d1", "model__dress", "app/dress.png"), entry("d2", "model__blazer", "app/blazer.png", seed: "s9")],
            outfit: "app/blazer.png", seed: "s9")
        static let draftAfterDrop = draftJSON(
            batch: [entry("d1", "model__dress", "app/dress.png")], outfit: "app/blazer.png", seed: "s9")
```

Tests:

```swift
    @Test func dropClearsTheEditedCopyThenDeletesAndRevalidates() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        #expect(flow.phase == .previews)
        #expect(flow.canDropFromBatch)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })
        #expect(flow.batchEntry(for: blazer)?.digest == "d2")
        #expect(flow.isSeeded(blazer))

        await flow.drop(blazer)

        let writes = StubURLProtocol.requests.filter { $0.httpMethod != "GET" }
        #expect(writes.map { "\($0.httpMethod!) \($0.url!.path)" } ==
                ["PATCH /v1/draft", "DELETE /v1/draft/batch/d2", "POST /v1/draft/validate"])
        let body = try #require(JSONSerialization.jsonObject(with: writes[0].httpBody ?? Data()) as? [String: Any])
        #expect((body["slots"] as? [String: Any])?["outfit"] is NSNull)
        #expect(writes[2].timeoutInterval == 95)
        #expect(flow.draft?.batch.map(\.digest) == ["d1"])
        #expect(flow.message == nil)
    }

    @Test func dropRefusesAPreviewWithNoBasketEntry() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = #"{"run_id":"tg-1000","run_token":"1.1","phase_a_running":false,"previews":[{"index":"0","run":"ghost","status":"done","has_image":true},{"index":"1","run":"model__dress","status":"done","has_image":true}]}"#
        let flow = make(routes)
        await flow.start(.existing)
        let ghost = try #require(flow.tryon?.previews.first)
        #expect(flow.batchEntry(for: ghost) == nil)

        await flow.drop(ghost)

        #expect(StubURLProtocol.requests.allSatisfy { $0.httpMethod == "GET" })
        #expect(flow.message == "The draft changed — reload before dropping.")
    }

    @Test func dropIsNotOfferedForASingleJob() async {
        let routes = Routes()          // Fixtures.draft has one basket entry
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        #expect(!flow.canDropFromBatch)
    }
```

- [ ] **Step 2: Run to verify failure** — `swift test --filter RunFlowTests` → compile error.

- [ ] **Step 3: Implement** — in `RunFlow`, add the stored property `public private(set) var isDropping = false` next to `isLoadingPanel`, and a new section before `// MARK: rent panel`:

```swift
    // MARK: drop after Phase A (Phase 6 spec §5)

    /// `preview.run` is the manifest run id; `GET /v1/draft`'s `batch[].run_id`
    /// is the id the manifest gives that entry. `nil` when the draft changed.
    public func batchEntry(for preview: TryonPreview) -> DraftBatchEntry? {
        draft?.batch.first { $0.runID == preview.run }
    }

    public func isSeeded(_ preview: TryonPreview) -> Bool {
        batchEntry(for: preview)?.tryonSeed != nil
    }

    /// Never while a spend is unanswered: the draft must not change under an
    /// in-flight confirm. The last job is never dropped here (Clear does that).
    public var canDropFromBatch: Bool {
        canSpend && !isDropping && (draft?.batch.count ?? 0) >= 2
            && tryon?.phaseARunning != true && (phase == .previews || phase == .rentPanel)
    }

    /// Free: drops one job from the basket, then re-validates, because every
    /// draft change resets `validated` and confirm refuses `not_validated`.
    /// Confirm then takes the fresh-spend branch (`_phase_a_matches_draft` is
    /// false) and may ask reuse/rerun — reuse calls no provider again.
    public func drop(_ preview: TryonPreview) async {
        guard canDropFromBatch else { return }
        isDropping = true
        defer { isDropping = false }
        message = nil
        do {
            let fresh = try await client.get(Draft.self, "v1", "draft")
            draft = fresh
            guard let entry = batchEntry(for: preview) else {
                message = "The draft changed — reload before dropping."
                return
            }
            // add-to-batch leaves the edited job a copy of the last entry; once
            // that entry is gone the copy would count as a job again.
            if editedJobEquals(entry, in: fresh), let role = clearRole(for: entry) {
                draft = try await client.patch(Draft.self, body: SlotPatch(role: role, materialID: nil),
                                               timeout: 95, "v1", "draft")
            }
            draft = try await client.delete(Draft.self, "v1", "draft", "batch", entry.digest)
            let validation = try await client.post(DraftValidationResponse.self, timeout: 95,
                                                   "v1", "draft", "validate")
            draft = validation.draft
            if validation.stale {
                message = "The draft changed during validation. Validate it again from New Job."
            } else if !validation.valid {
                message = "Validation failed after the drop — open New Job to fix it."
            }
        } catch {
            message = apiError(error).userMessage
            if let fresh = try? await client.get(Draft.self, "v1", "draft") { draft = fresh }
        }
        await refreshTryon()
        if phase == .rentPanel { await loadPanel(force: false) }
    }

    private func editedJobEquals(_ entry: DraftBatchEntry, in draft: Draft) -> Bool {
        draft.pipeline == entry.pipeline && draft.provider == entry.provider
            && draft.filledSlots == entry.filledSlots && draft.tryonSeed == entry.tryonSeed
    }

    private func clearRole(for entry: DraftBatchEntry) -> String? {
        if entry.slots.keys.contains("outfit") { return "outfit" }
        return catalog.first { $0.id == entry.pipeline }?.required.sorted().first
    }
```

`refreshTryon()` re-reads previews; if `continueToRent` already produced `.rentPanel`, `loadPanel` fetches a fresh `panel_token` (the old one priced the dropped job).

- [ ] **Step 4: Run** `swift test` → all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift
git commit -m "feat(ios): drop one try-on from a batch after phase A

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `BatchSummary` and the batch progress list

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Models/Runs.swift`
- Modify: `ios/MotionApp/Runs/RunDetailView.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift`

**Interfaces:**
- Produces: `BatchSummary(_ jobs: [JobProgress])` with `total`, `done`, `running`, `failed`, `ordered: [JobProgress]` (failed, then running, then the rest; stable); `JobProgress.finishedSec: Double`.

- [ ] **Step 1: Write the failing test** — in `ModelsTests`:

```swift
    @Test func batchSummaryCountsAndPutsTroubleFirst() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"""
        [{"id":"a","status":"done","stages":[{"name":"tryon","status":"done","elapsed_sec":6},{"name":"motion","status":"done","elapsed_sec":60}]},
         {"id":"b","status":"running","stages":[{"name":"tryon","status":"done","elapsed_sec":7},{"name":"motion","status":"running","elapsed_sec":null}]},
         {"id":"c","status":"error","stages":[{"name":"tryon","status":"error","elapsed_sec":null}]},
         {"id":"d","status":"pending","stages":[]}]
        """#
        let jobs = try decoder.decode([JobProgress].self, from: Fixtures.data(json))
        let summary = BatchSummary(jobs)
        #expect((summary.total, summary.done, summary.running, summary.failed) == (4, 1, 1, 1))
        #expect(summary.ordered.map(\.id) == ["c", "b", "a", "d"])
        #expect(jobs[0].finishedSec == 66)
        #expect(jobs[1].finishedSec == 7)
    }
```

- [ ] **Step 2: Run to verify failure** — `swift test --filter ModelsTests` → compile error.

- [ ] **Step 3: Implement** — append to `Runs.swift`:

```swift
extension JobProgress {
    /// Seconds spent in finished stages; a running stage reports `null`.
    public var finishedSec: Double { stages.compactMap(\.elapsedSec).reduce(0, +) }
}

/// The batch progress header and row order (Phase 6 spec §5): failed and
/// running jobs first, so trouble is on screen without scrolling 12 rows.
public struct BatchSummary: Equatable, Sendable {
    public let total: Int
    public let done: Int
    public let running: Int
    public let failed: Int
    public let ordered: [JobProgress]

    public init(_ jobs: [JobProgress]) {
        total = jobs.count
        done = jobs.filter { $0.status == .done }.count
        running = jobs.filter { $0.status == .running }.count
        failed = jobs.filter { $0.status == .error }.count
        func rank(_ job: JobProgress) -> Int {
            switch job.status { case .error: 0; case .running: 1; default: 2 }
        }
        ordered = jobs.enumerated()
            .sorted { (rank($0.element), $0.offset) < (rank($1.element), $1.offset) }
            .map(\.element)
    }
}
```

In `RunDetailView.swift`, replace `ForEach(d.jobs) { job in JobTimeline(job: job, showTitle: d.jobs.count > 1) }` with:

```swift
                    if d.jobs.count > 1 {
                        BatchProgressList(jobs: d.jobs)
                    } else {
                        ForEach(d.jobs) { job in JobTimeline(job: job, showTitle: false) }
                    }
```

and, where `KillButton` is shown for a batch, add below it when `d.jobs.count > 1`:

```swift
                        Text("Kill stops every remaining job in this batch.")
                            .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
```

Append to the file:

```swift
struct BatchProgressList: View {
    let jobs: [JobProgress]
    @State private var expanded: Set<String> = []

    var body: some View {
        let summary = BatchSummary(jobs)
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Batch · \(summary.done)/\(summary.total) done")
            Text("\(summary.running) running · \(summary.failed) failed")
                .font(Theme.mono(11)).foregroundStyle(summary.failed > 0 ? Theme.red : Theme.ink2)
                .accessibilityIdentifier("batch.summary")
            ForEach(summary.ordered) { job in
                VStack(alignment: .leading, spacing: 10) {
                    Button {
                        if expanded.contains(job.id) { expanded.remove(job.id) } else { expanded.insert(job.id) }
                    } label: {
                        HStack(spacing: 10) {
                            StageDot(status: job.status)
                            Text(job.id).font(Theme.mono(12, .semibold)).foregroundStyle(Theme.ink1).lineLimit(1)
                            Spacer(minLength: 0)
                            if job.finishedSec > 0 {
                                Text(Format.clock(job.finishedSec)).font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                            }
                            Image(systemName: expanded.contains(job.id) ? "chevron.up" : "chevron.down")
                                .foregroundStyle(Theme.ink3)
                        }
                    }
                    .buttonStyle(.plain)
                    if expanded.contains(job.id) { JobTimeline(job: job, showTitle: false) }
                }
                .padding(12).card()
            }
        }
    }
}
```

- [ ] **Step 4: Run** `cd ios/MotionKit && swift test` and `make ios-build` → pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Models/Runs.swift ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift \
        ios/MotionApp/Runs/RunDetailView.swift
git commit -m "feat(ios): show a batch as a compact per-job list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Wiring, the contract route and the Saved try-ons screen

**Files:**
- Modify: `ios/MotionApp/MotionApp.swift`, `ios/MotionApp/RootView.swift`
- Create: `ios/MotionApp/Materials/MaterialTabView.swift`, `ios/MotionApp/Materials/SavedTryonsView.swift`
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`

**Interfaces:**
- Consumes: `TryonLibraryStore` (Task 3), `BatchComposer` (Task 4), `MaterialsStore`, `DraftStore`.
- Produces: `AppModel.tryonLibrary: TryonLibraryStore?`, `AppModel.batchComposer: BatchComposer?`, `AppModel.newJobMode: NewJobMode` (`enum NewJobMode { case single, batch }`, declared in `MotionApp.swift`), `MaterialTabView(materials:library:draft:)`.

- [ ] **Step 1: Wire `AppModel`** — in `MotionApp.swift` add below `AppTab`:

```swift
enum NewJobMode: Hashable { case single, batch }
```

Add `private(set) var tryonLibrary: TryonLibraryStore?`, `private(set) var batchComposer: BatchComposer?`, `var newJobMode: NewJobMode = .single`. In `reconnect()`'s no-credentials branch set both to `nil`; after `draft = DraftStore(client: client)` replace with:

```swift
        let draft = DraftStore(client: client)
        self.draft = draft
        let library = TryonLibraryStore(client: client, draft: draft)
        tryonLibrary = library
        batchComposer = BatchComposer(draft: draft, library: library)
```

- [ ] **Step 2: Material tab wrapper** — `MaterialTabView.swift`:

```swift
import MotionKit
import SwiftUI

/// Material tab: uploaded materials, or saved try-ons (Phase 6 spec §6).
struct MaterialTabView: View {
    let materials: MaterialsStore
    let library: TryonLibraryStore
    let draft: DraftStore
    @State private var showSaved = false

    var body: some View {
        VStack(spacing: 0) {
            Picker("Show", selection: $showSaved) {
                Text("Materials").tag(false)
                Text("Saved try-ons").tag(true)
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("material.mode")
            .padding(.horizontal, 20).padding(.vertical, 8)
            if showSaved {
                SavedTryonsView(library: library, materials: materials, draft: draft)
            } else {
                MaterialsView(store: materials)
            }
        }
        .background(Theme.bg)
    }
}
```

In `RootView`, bind `let library = model.tryonLibrary` in the `if let` chain and replace `NavigationStack { MaterialsView(store: materials) }` with `NavigationStack { MaterialTabView(materials: materials, library: library, draft: draft) }`.

- [ ] **Step 3: Saved try-ons grid** — `SavedTryonsView.swift`:

```swift
import MotionKit
import SwiftUI

struct SavedTryonsView: View {
    let library: TryonLibraryStore
    let materials: MaterialsStore
    let draft: DraftStore
    @Environment(AppModel.self) private var model
    @State private var deleteCandidate: TryonLibraryEntry?

    private let columns = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Saved try-ons").font(Theme.sans(28, .bold)).foregroundStyle(Theme.ink)
                    Spacer()
                    if library.isStale { StaleTag(lastSuccess: library.lastSuccess) }
                }
                if let message = library.message {
                    MessageCard(text: message) { library.dismissMessage() }
                }
                if library.loaded && library.entries.isEmpty {
                    Text("Nothing saved yet. Tap Keep on a try-on preview to save it here.")
                        .font(Theme.sans(14)).foregroundStyle(Theme.ink2)
                        .frame(maxWidth: .infinity).padding(.vertical, 60)
                        .accessibilityIdentifier("saved.empty")
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(library.entries) { entry in
                            SavedTryonTile(entry: entry, library: library, materials: materials,
                                           onUse: { use(entry) }, onDelete: { deleteCandidate = entry })
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
        }
        .refreshable { await library.load() }
        .task {
            await library.load()
            if !materials.loaded { await materials.refresh() }
            if draft.draft == nil { await draft.load() }
        }
        .confirmationDialog(
            "Delete this saved try-on?",
            isPresented: Binding(get: { deleteCandidate != nil }, set: { if !$0 { deleteCandidate = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                guard let entry = deleteCandidate else { return }
                deleteCandidate = nil
                Task { await library.delete(entry) }
            }
            Button("Cancel", role: .cancel) { deleteCandidate = nil }
        } message: {
            let users = deleteCandidate.map { library.users(of: $0.id) } ?? []
            Text(users.isEmpty ? "The image is removed from the VPS."
                 : "Used by \(users.joined(separator: ", ")) — those jobs lose their saved image.")
        }
    }

    private func use(_ entry: TryonLibraryEntry) {
        Task {
            if await library.use(entry) {
                model.newJobMode = .single
                model.selectedTab = .newJob
            }
        }
    }
}

private struct SavedTryonTile: View {
    let entry: TryonLibraryEntry
    let library: TryonLibraryStore
    let materials: MaterialsStore
    let onUse: () -> Void
    let onDelete: () -> Void
    @State private var image: UIImage?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Group {
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    Rectangle().fill(Theme.surface2).overlay(ProgressView())
                }
            }
            .frame(height: 170).frame(maxWidth: .infinity).clipped()
            .clipShape(.rect(cornerRadius: 12))
            Text(name("character") + " · " + name("outfit"))
                .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.ink1).lineLimit(2)
            Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .omitted))")
                .font(Theme.mono(10)).foregroundStyle(Theme.ink2)
            HStack {
                Button("Use in job", action: onUse)
                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                    .accessibilityIdentifier("saved.use.\(entry.id)")
                Spacer()
                Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                    .foregroundStyle(Theme.red)
            }
        }
        .padding(10).card()
        .task(id: entry.id) { image = await library.image(id: entry.id).flatMap(UIImage.init(data:)) }
    }

    private func name(_ role: String) -> String {
        guard let id = entry.materialIDs[role] else { return "—" }
        return materials.materials.first { $0.id == id }?.name ?? "(deleted)"
    }
}
```

(`MessageCard` and `StaleTag` exist already — `RunFlowView.swift` and `Components/`.)

- [ ] **Step 4: Contract route** — in `motion-contract/main.swift`, after the `GET /v1/materials` check:

```swift
await check("GET /v1/tryon-library") {
    _ = try await client.get(TryonLibraryResponse.self, "v1", "tryon-library")
}
```

- [ ] **Step 5: Build and test** — `cd ios/MotionKit && swift test` and `make ios-build` → pass. (The controller runs `make ios-contract` and expects 14/14.)

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/MotionApp.swift ios/MotionApp/RootView.swift ios/MotionApp/Materials/MaterialTabView.swift \
        ios/MotionApp/Materials/SavedTryonsView.swift ios/MotionKit/Sources/motion-contract/main.swift
git commit -m "feat(ios): browse, use and delete saved try-ons

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: New Job — Single | Batch

**Files:**
- Modify: `ios/MotionApp/NewJob/NewJobView.swift`
- Create: `ios/MotionApp/NewJob/BatchComposerSection.swift`
- Modify: `ios/MotionApp/RootView.swift` (pass the composer and library)

**Interfaces:**
- Consumes: `BatchComposer` (Task 4), `TryonLibraryStore` (Task 3), `AppModel.newJobMode` (Task 7), existing `SlotMaterialRow`, `MaterialPicker`, `PipelinePicker`.
- Produces: `NewJobView(store:materials:flow:composer:library:)`; accessibility ids `newjob.mode`, `batch.pickOutfits`, `batch.run`, `batch.progress`, `batch.failure`, `batch.outfit.<materialID>`, `batch.seed.<materialID>`.

- [ ] **Step 1: Make the slot row reusable** — in `NewJobView.swift` change `private struct SlotMaterialRow` to `struct SlotMaterialRow` (internal) so `BatchComposerSection` can use it.

- [ ] **Step 2: Mode switch** — `NewJobView` gains `let composer: BatchComposer`, `let library: TryonLibraryStore`, `@Environment(AppModel.self) private var model`. In `composer(draft:pipeline:)`, after `header(draft)`:

```swift
                Picker("Mode", selection: Binding(get: { model.newJobMode }, set: { model.newJobMode = $0 })) {
                    Text("Single").tag(NewJobMode.single)
                    Text("Batch").tag(NewJobMode.batch)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("newjob.mode")
                .disabled(store.isBusy || composer.isRunning)
```

Then replace the fixed `PipelinePicker … slots … readiness … editorActions` block with:

```swift
                PipelinePicker(
                    pipeline: pipeline,
                    pipelines: model.newJobMode == .batch ? store.catalog.filter(BatchComposer.supports) : store.catalog,
                    selectedProvider: draft.provider,
                    disabled: store.isBusy || composer.isRunning,
                    onPipelineSelected: { id in await store.selectPipeline(id) },
                    onProviderSelected: { id in await store.selectProvider(id) })
                if model.newJobMode == .batch {
                    BatchComposerSection(store: store, composer: composer, library: library,
                                         materials: materials, pipeline: pipeline,
                                         onPickRole: { selectedRole = $0 })
                } else {
                    slots(draft: draft, pipeline: pipeline)
                    seedBadge(draft)
                    readiness(draft)
                    editorActions(draft)
                }
```

Keep `batch(draft)` and `validation(draft)` after it for both modes. In `batch(_:)`, under the `Text(slotSummary(entry))` line add:

```swift
                            if entry.tryonSeed != nil {
                                Text("Saved try-on").font(Theme.mono(9, .semibold)).foregroundStyle(Theme.lime)
                            }
```

Add:

```swift
    @ViewBuilder private func seedBadge(_ draft: Draft) -> some View {
        if let seed = draft.tryonSeed {
            HStack(spacing: 8) {
                Image(systemName: "photo.badge.checkmark").foregroundStyle(Theme.lime)
                Text(library.entries.contains { $0.id == seed } || !library.loaded
                     ? "Uses a saved try-on — Phase A skips the provider for this job"
                     : "The saved try-on no longer exists")
                    .font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                Spacer(minLength: 0)
                Button("Remove") { Task { await store.apply(DraftPatch(seed: .clear)) } }
                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.red)
                    .disabled(store.isBusy)
            }
            .padding(12).card(border: Theme.limeLine)
        }
    }
```

Add `.task { await library.load() }` next to the existing `.task { await store.load() }`. Disable the existing "Add to batch" and "Clear" while `composer.isRunning`.

- [ ] **Step 3: `BatchComposerSection.swift`**:

```swift
import MotionKit
import SwiftUI

/// Batch mode (Phase 6 spec §4): the edited job's slots are shared; each
/// chosen outfit becomes one basket job.
struct BatchComposerSection: View {
    let store: DraftStore
    let composer: BatchComposer
    let library: TryonLibraryStore
    let materials: MaterialsStore
    let pipeline: Pipeline
    let onPickRole: (String) -> Void
    @State private var pickingOutfits = false

    private var sharedRoles: [String] {
        (pipeline.required + pipeline.optional).filter { $0 != BatchComposer.outfitRole }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !BatchComposer.supports(pipeline) {
                Text("This pipeline has no character + outfit pair. Pick a try-on pipeline for a batch.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.amber)
            } else {
                SectionLabel(text: "Shared")
                ForEach(sharedRoles, id: \.self) { role in
                    SlotMaterialRow(role: role, required: pipeline.required.contains(role),
                                    kind: pipeline.roles[role] ?? .unknown,
                                    slot: store.draft?.slots[role], materials: materials,
                                    disabled: store.isBusy || composer.isRunning) { onPickRole(role) }
                }
                SectionLabel(text: "Outfits · \(composer.outfits.count)/\(BatchComposer.maxOutfits)")
                ForEach(composer.outfits) { outfit in outfitRow(outfit) }
                Button("Choose outfits…") { pickingOutfits = true }
                    .buttonStyle(SecondaryButtonStyle())
                    .accessibilityIdentifier("batch.pickOutfits")
                    .disabled(composer.isRunning)
                runButton
            }
        }
        .onChange(of: composer.sharedSlots) { _, _ in composer.refreshSeeds() }
        .sheet(isPresented: $pickingOutfits) {
            OutfitMultiPicker(composer: composer, materials: materials,
                              kind: pipeline.roles[BatchComposer.outfitRole] ?? .image)
        }
    }

    private func outfitRow(_ outfit: CrossOutfit) -> some View {
        let matches = composer.matches(for: outfit.outfitID)
        let name = materials.materials.first { $0.id == outfit.outfitID }?.name ?? outfit.outfitID
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(name).font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1).lineLimit(1)
                Spacer()
                Button(role: .destructive) { composer.toggle(outfitID: outfit.outfitID) } label: {
                    Image(systemName: "xmark.circle")
                }
                .foregroundStyle(Theme.ink3).disabled(composer.isRunning)
            }
            Toggle("Use saved try-on", isOn: Binding(
                get: { outfit.seedID != nil },
                set: { composer.setSeed($0 ? matches.first?.id : nil, for: outfit.outfitID) }))
                .font(Theme.sans(13)).tint(Theme.lime)
                .disabled(matches.isEmpty || composer.isRunning)
                .accessibilityIdentifier("batch.seed.\(outfit.outfitID)")
            if matches.count > 1, outfit.seedID != nil {
                Picker("Saved image", selection: Binding(
                    get: { outfit.seedID ?? "" },
                    set: { composer.setSeed($0, for: outfit.outfitID) })) {
                    ForEach(matches) { entry in
                        Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .shortened))")
                            .tag(entry.id)
                    }
                }
                .font(Theme.sans(12))
            }
            if matches.isEmpty {
                Text("No saved try-on for this pair — Phase A will make one.")
                    .font(Theme.mono(10)).foregroundStyle(Theme.ink3)
            }
        }
        .padding(12).card()
        .accessibilityIdentifier("batch.outfit.\(outfit.outfitID)")
    }

    @ViewBuilder private var runButton: some View {
        if let progress = composer.progress, composer.isRunning {
            HStack(spacing: 8) {
                ProgressView()
                Text("Adding \(progress.done)/\(progress.total)…").font(Theme.sans(13, .semibold))
            }
            .accessibilityIdentifier("batch.progress")
        }
        if let failure = composer.failure {
            Text(failure).font(Theme.sans(13)).foregroundStyle(Theme.red)
                .accessibilityIdentifier("batch.failure")
        }
        if let added = composer.lastAdded {
            Text("Added \(added) job\(added == 1 ? "" : "s") to the batch.")
                .font(Theme.sans(13)).foregroundStyle(Theme.lime)
        }
        if !composer.missingShared.isEmpty {
            Text("Fill \(composer.missingShared.joined(separator: ", ")) first.")
                .font(Theme.sans(12)).foregroundStyle(Theme.amber)
        }
        Button(composer.failure == nil
               ? "Add \(composer.outfits.count) job\(composer.outfits.count == 1 ? "" : "s") to batch"
               : "Continue") {
            Task { await composer.run() }
        }
        .buttonStyle(PrimaryButtonStyle())
        .accessibilityIdentifier("batch.run")
        .disabled(!composer.canRun)
    }
}

/// Multi-select of outfit materials, capped by `BatchComposer.maxOutfits`.
private struct OutfitMultiPicker: View {
    let composer: BatchComposer
    let materials: MaterialsStore
    let kind: PipelineRoleKind
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List(materials.materials.filter(kind.accepts)) { material in
                let chosen = composer.outfits.contains { $0.outfitID == material.id }
                Button {
                    composer.toggle(outfitID: material.id)
                } label: {
                    HStack {
                        Text(material.name).foregroundStyle(Theme.ink1)
                        Spacer()
                        Image(systemName: chosen ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(chosen ? Theme.lime : Theme.ink3)
                    }
                }
                .disabled(!chosen && composer.outfits.count >= BatchComposer.maxOutfits)
                .accessibilityIdentifier("outfit.pick.\(material.id)")
            }
            .navigationTitle("Choose outfits")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task { if !materials.loaded { await materials.refresh() } }
    }
}
```

- [ ] **Step 4: Pass the stores** — in `RootView`, bind `let composer = model.batchComposer` in the `if let` chain and create `NewJobView(store: draft, materials: materials, flow: flow, composer: composer, library: library)`.

- [ ] **Step 5: Build** — `make ios-build` → pass; `cd ios/MotionKit && swift test` → pass.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/NewJob/NewJobView.swift ios/MotionApp/NewJob/BatchComposerSection.swift ios/MotionApp/RootView.swift
git commit -m "feat(ios): add batch mode to New Job

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Drop and seed badge on the try-on preview

**Files:**
- Modify: `ios/MotionApp/RunFlow/TryonPreviewCard.swift`

**Interfaces:**
- Consumes: `RunFlow.canDropFromBatch`, `batchEntry(for:)`, `isSeeded(_:)`, `isDropping`, `drop(_:)` (Task 5).
- Produces: accessibility ids `tryon.drop.<index>`, `tryon.seeded.<index>`.

- [ ] **Step 1: Implement** — add `@State private var confirmDrop = false`. In the header `HStack`, after `Text(preview.run)`:

```swift
                if flow.isSeeded(preview) {
                    Text("Saved try-on").font(Theme.mono(10, .semibold)).foregroundStyle(Theme.lime)
                        .accessibilityIdentifier("tryon.seeded.\(preview.index)")
                }
```

After the `Regenerate…` button:

```swift
            if flow.canDropFromBatch || flow.isDropping {
                Button(flow.isDropping ? "Dropping…" : "Drop from batch", role: .destructive) { confirmDrop = true }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(!flow.canDropFromBatch || flow.batchEntry(for: preview) == nil)
                    .accessibilityIdentifier("tryon.drop.\(preview.index)")
                if flow.batchEntry(for: preview) == nil {
                    Text("Draft changed — reload").font(Theme.mono(10)).foregroundStyle(Theme.amber)
                }
            }
```

and on the card:

```swift
        .confirmationDialog("Drop \(preview.run) from the batch?", isPresented: $confirmDrop, titleVisibility: .visible) {
            Button("Drop", role: .destructive) { Task { await flow.drop(preview) } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Free — nothing is rented yet. The draft is validated again, and Confirm then rents only what is left.")
        }
```

`RunFlowView` already shows `flow.message` in a `MessageCard`; if it does not in the `.previews` phase, add `if let message = flow.message { MessageCard(text: message) { flow.dismissMessage() } }` above the previews `ForEach`.

- [ ] **Step 2: Build** — `make ios-build` → pass.

- [ ] **Step 3: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/RunFlow/TryonPreviewCard.swift ios/MotionApp/RunFlow/RunFlowView.swift
git commit -m "feat(ios): drop a try-on from the batch before renting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Stage `RunFlowView.swift` only if it changed.)

---

### Task 10: Phase 6 UI smoke and docs

**Files:**
- Create: `ios/MotionAppUITests/Phase6SmokeTests.swift`
- Modify: `docs/superpowers/swiftui-app-progress.md`, `docs/superpowers/specs/2026-09-23-swiftui-app-phase-6-design.md` (status line), `docs/superpowers/specs/2026-09-22-swiftui-app-design.md` (row 6), `ios/README.md`

**Interfaces:**
- Consumes: ids from Tasks 7–8 (`newjob.mode`, `batch.pickOutfits`, `outfit.pick.<id>`, `batch.run`, `material.mode`), `Phase4Draft.revealButton`, `Phase4Draft.waitUntil`, `Phase4Draft.selectTryonPipeline`, `Phase4Draft.chooseMaterial` (in `Phase4SmokeTests.swift`).

- [ ] **Step 1: Write the smoke** — `Phase6SmokeTests.swift`:

```swift
import XCTest

/// Zero-spend, live server: only reads and free draft mutations. Builds a
/// two-outfit batch, drops one entry, opens Saved try-ons, clears the draft.
/// Never taps Preview try-on, Rent, Confirm, Kill, Migrate or a GPU row.
final class Phase6SmokeTests: XCTestCase {
    @MainActor
    func testCrossBuildDropAndLibrary() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))
        guard app.staticTexts["0 jobs"].waitForExistence(timeout: 10) else {
            throw XCTSkip("The draft is not empty — this smoke never overwrites a real draft.")
        }

        app.segmentedControls["newjob.mode"].buttons["Batch"].tap()
        Phase4Draft.selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        Phase4Draft.chooseMaterial(for: "Driver", in: app)

        Phase4Draft.revealButton("batch.pickOutfits", in: app).tap()
        let picks = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "outfit.pick."))
        guard Phase4Draft.waitUntil(timeout: 15, condition: { picks.count >= 2 }) else {
            app.buttons["Done"].tap()
            Phase4Draft.clear(in: app)
            throw XCTSkip("Fewer than two image materials to use as outfits.")
        }
        picks.element(boundBy: 0).tap()
        picks.element(boundBy: 1).tap()
        app.buttons["Done"].tap()

        let run = Phase4Draft.revealButton("batch.run", in: app)
        XCTAssertTrue(run.isEnabled)
        run.tap()
        XCTAssertTrue(app.staticTexts["Added 2 jobs to the batch."].waitForExistence(timeout: 120))
        XCTAssertTrue(app.staticTexts["Batch · 2"].exists)

        Phase4Draft.revealButton("Drop", in: app).tap()
        app.sheets["Drop this batch entry?"].buttons["Drop"].tap()
        XCTAssertTrue(app.staticTexts["Batch · 1"].waitForExistence(timeout: 15))

        Phase4Draft.clear(in: app)

        app.tabBars.buttons["Material"].tap()
        app.segmentedControls["material.mode"].buttons["Saved try-ons"].tap()
        XCTAssertTrue(app.staticTexts["Saved try-ons"].waitForExistence(timeout: 15))
        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0")
    }
}
```

Adjust helper calls to the real signatures in `Phase4SmokeTests.swift` (`Phase4Draft.clear(in:)` etc.) if they differ; `Phase4Draft` helpers must stay `@MainActor static`.

- [ ] **Step 2: Build the UI test target** — `make ios-build` → pass. (The controller runs `make ios-ui-test` outside the sandbox and records the result.)

- [ ] **Step 3: Docs**
  - `docs/superpowers/swiftui-app-progress.md`: Phase 6 row → "Complete in code" with what shipped (cross build, seeds, drop after Phase A with re-validate, batch progress list, saved try-ons) and gate counts; "What is implemented now" gains a Phase 6 subsection; "Known incomplete work": no real batch has run (a 2-outfit batch with one seeded job is the proposed spend test, needs approval); "Next work": no phase remains — list the optional spend test and the parent spec's deferred items (pair mode, stock-watch).
  - Phase 6 spec status line: "implemented; gates …" with the real results.
  - Parent spec §5 row 6: "implemented per [Phase 6 design](2026-09-23-swiftui-app-phase-6-design.md)".
  - `ios/README.md`: a Phase 6 section (Batch mode, Saved try-ons, drop after Phase A, `Phase6SmokeTests`).

- [ ] **Step 4: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionAppUITests/Phase6SmokeTests.swift docs/superpowers/swiftui-app-progress.md \
        docs/superpowers/specs/2026-09-23-swiftui-app-phase-6-design.md \
        docs/superpowers/specs/2026-09-22-swiftui-app-design.md ios/README.md
git commit -m "test(ios): phase 6 zero-spend UI smoke and handoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Controller checklist after Task 10

1. `make ios-test`, `make ios-build`, `make batch-test`, `scrub-secrets.sh --check`.
2. `make ios-contract` → expect 14/14 (the `tryon-library` route decodes). Before Task 1 is deployed the draft still decodes (optional field).
3. `make ios-ui-test` outside the sandbox → Phases 3–6 pass or skip with a stated reason.
4. Before merging (Task 1 touches `scripts/**`): on the VPS check `batch/*.state.json`, `.env`'s `GPU_INSTANCE_ID`, `pgrep -af 'drain.py|batch_run.py'`, and `GET /v1/pod` for a lease or migration.
5. After the deploy: `make ios-contract` again and confirm `GET /v1/draft` now carries `tryon_seed`.
