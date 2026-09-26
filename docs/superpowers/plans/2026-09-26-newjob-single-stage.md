# New Job as one no-scroll stage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace New Job's scrolling `List` (Single | Batch switch, Settings section, basket section) with one stage that fits the screen: slot cards, a Pipeline · Provider chip, a basket drawer, and an action bar with Add and a validating Continue.

**Architecture:** The decisions live in MotionKit, where they are unit-tested: `NewJobState` (a pure value: cards, missing roles, what Add and Continue would do, the next card of a chained pick) and two `BatchComposer` additions (`adoptDraftSelection()`, `reset()`). The app target gets small single-purpose views (`SlotCard`, `SlotCardGrid`, `SettingsChip`, `BasketDrawer`, `PickerChainSheet`) plus a rewritten `NewJobActionBar`, and `NewJobView` is rewritten last to compose them. The UI smokes move to the new flow in the final code task.

**Tech Stack:** Swift 6, SwiftUI (iOS 26), Swift Testing (MotionKit), XCUITest (live, zero-spend), XcodeGen.

**Spec:** `docs/superpowers/specs/2026-09-26-newjob-single-stage-design.md`

## Global Constraints

- No server change. Nothing under `scripts/**`; every call is an existing route (`PATCH /v1/draft`, `POST /v1/draft/add-to-batch`, `POST /v1/draft/validate`, `POST /v1/draft/clear`, `DELETE /v1/draft/batch/<digest>`).
- Write in English: code, comments, commit messages. No `# #region ALD` markers.
- Comments explain *why*, with the date where a behavior replaces an older one (house style in `ios/MotionApp/NewJob/*`).
- Kept accessibility contract: identifiers `batch.run`, `batch.summary`, `batch.pickOutfits`, `batch.pickDrivers`, `outfit.pick.*`, `driver.pick.*`, `newjob.actionBar`, `newjob.continueToRun`, `newjob.more`, `batch.progress`, `batch.failure`, `batch.capReason`; the "Clear draft" menu item and its "Clear the draft?" dialog; "Drop" buttons and the "Drop this batch entry?" dialog; the "Batch · N" text; the "Ready" text; slot cards labelled by `SlotText.title` with value "Missing required" when a required card is empty.
- `BatchComposer.maxJobs` (12) stays the only job cap.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit (run from the repo root).
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF
  ```
- Work on branch `newjob-single-stage` (already checked out; spec commit `ad2b56d`, WIP commit `6cefb86`).

## Review Focus

1. **"Use in job" from Saved try-ons while New Job holds a hand-picked outfit selection.** Expected: the hand-picked selection stays and the draft's outfit is not adopted over it. Pinned by `adoptLeavesAnExistingSelectionAlone` (Task 1).
2. **A composer build that stops half-way, then Continue.** Expected: Continue does not validate or navigate. The failure line and the composer's own "Continue" stay visible. The view logic has no unit seam, so this is pinned by `continueButton`'s `composer.failure != nil` guard and `runContinue`'s `guard await add()` (Task 6), and checked by hand in Task 10 Step 2 by failing a build with the phone offline mid-add.
3. **Switching to a pipeline without character + outfit while outfits are picked.** Expected: the selection is dropped (`reset()`), and the Outfit card disappears instead of silently holding hidden jobs. Pinned by the Phase 3 smoke rewrite (Task 9).
4. **Clear draft with an outfit selection and an empty basket.** Expected: the cards go back to empty and "Add N" disappears. Pinned by `resetEmptiesTheSelection` (Task 1) and the Phase 6 smoke (Task 9).
5. **iPhone SE with a four-slot pipeline, an expanded drawer and an error banner.** Expected: nothing scrolls except the drawer's own list, and the action bar stays reachable. Pinned by the screenshot check in Task 10.

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift` | modify | `adoptDraftSelection()`, `reset()` |
| `ios/MotionKit/Sources/MotionKit/Stores/NewJobState.swift` | create | pure card/readiness/chain logic |
| `ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift` | modify | adopt/reset tests; fake server gains `preset(outfit:seed:)` |
| `ios/MotionKit/Tests/MotionKitTests/NewJobStateTests.swift` | create | `NewJobState` tests |
| `ios/MotionApp/NewJob/SlotCard.swift` | create | one card for single- and multi-select slots |
| `ios/MotionApp/NewJob/SlotCardGrid.swift` | create | sizes cards to the space left |
| `ios/MotionApp/NewJob/SettingsChip.swift` | create | toolbar chip + settings sheet |
| `ios/MotionApp/NewJob/PipelinePicker.swift` | modify | drop the `PipelinePicker` Section; keep `PipelineText`, `ProviderText`, `ProviderMark`; make the two choice lists reusable |
| `ios/MotionApp/NewJob/BasketDrawer.swift` | create | collapsible basket |
| `ios/MotionApp/NewJob/NewJobActionBar.swift` | rewrite | Add / Continue, composer status lines |
| `ios/MotionApp/NewJob/PickerChainSheet.swift` | create | one sheet that walks the empty required cards |
| `ios/MotionApp/NewJob/MaterialPicker.swift` | modify | optional `onAdvance` instead of dismissing |
| `ios/MotionApp/NewJob/MaterialMultiPicker.swift` | modify | optional `onNext` toolbar button |
| `ios/MotionApp/NewJob/NewJobView.swift` | rewrite | compose the stage |
| `ios/MotionApp/NewJob/SlotRow.swift` | modify | delete `SlotRow`, `SlotTile`; keep `SlotText` |
| `ios/MotionApp/NewJob/BatchComposerSection.swift` | delete | its `BatchPickerSheets` is replaced by `PickerChainSheet` |
| `ios/MotionApp/NewJob/BatchRunBar.swift` | delete | absorbed into `NewJobActionBar` |
| `ios/MotionApp/MotionApp.swift` | modify | delete `NewJobMode`, `newJobMode` |
| `ios/MotionApp/Materials/SavedTryonsView.swift` | modify | stop setting `newJobMode` |
| `ios/MotionAppUITests/Phase3SmokeTests.swift`, `Phase4SmokeTests.swift`, `Phase6SmokeTests.swift` | modify | new flow |
| `docs/superpowers/swiftui-app-progress.md` | modify | handoff entry |

---

### Task 1: `BatchComposer.adoptDraftSelection()` and `reset()`

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/BatchComposer.swift` (add after `setSeed(_:for:)`, ~line 219)
- Test: `ios/MotionKit/Tests/MotionKitTests/BatchComposerTests.swift`

**Interfaces:**
- Produces: `public func adoptDraftSelection() async` and `public func reset()` on `BatchComposer`.

- [ ] **Step 1: Add a preset hook to the fake server**

In `FakeDraftServer` (next to `failNextPatch(for:)`), add:

```swift
        /// The edited job already names an outfit (and maybe a seed), the way
        /// Saved try-ons' "Use in job" leaves it.
        func preset(outfit: String, seed: String?) {
            lock.withLock { self.outfit = outfit; self.seed = seed }
        }
```

- [ ] **Step 2: Write the failing tests**

Append inside `BatchComposerTests`:

```swift
    @Test func adoptMovesTheDraftsOutfitSeedAndDriverIntoTheComposer() async {
        let server = FakeDraftServer()
        server.preset(outfit: "app/o1.png", seed: "s1")
        let (composer, draft) = await make(server)

        await composer.adoptDraftSelection()

        #expect(composer.outfits == [CrossOutfit(outfitID: "app/o1.png", seedID: "s1")])
        #expect(composer.drivers == ["app/dance.mp4"])
        // One PATCH empties both slots and the seed: the composer is now the
        // only place the crossed roles live.
        let w = writes()
        #expect(w.map { "\($0.0) \($0.1)" } == ["PATCH /v1/draft"])
        let slots = w[0].2?["slots"] as? [String: Any]
        #expect(slots?["outfit"] is NSNull)
        #expect(slots?["driver"] is NSNull)
        #expect(w[0].2?["tryon_seed"] is NSNull)
        #expect(draft.draft?.filledSlots["outfit"] == nil)
        #expect(draft.draft?.filledSlots["driver"] == nil)
        #expect(composer.canRun)
    }

    @Test func adoptLeavesAnExistingSelectionAlone() async {
        let server = FakeDraftServer()
        server.preset(outfit: "app/o1.png", seed: nil)
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o9.png")
        composer.toggle(driverID: "app/d9.mp4")

        await composer.adoptDraftSelection()

        #expect(composer.outfits.map(\.outfitID) == ["app/o9.png"])
        #expect(composer.drivers == ["app/d9.mp4"])
        #expect(writes().isEmpty)
    }

    @Test func adoptWithNothingOnTheDraftWritesNothing() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        composer.toggle(driverID: "app/d9.mp4")   // the draft's driver is not adopted over it

        await composer.adoptDraftSelection()

        #expect(composer.outfits.isEmpty)
        #expect(composer.drivers == ["app/d9.mp4"])
        #expect(writes().isEmpty)
    }

    @Test func adoptWithoutASeedFallsBackToTheNewestMatch() async {
        let library = #"{"entries":[{"id":"s7","provider":"gemini","saved_at":10,"slots":{"character":"app/me.png","outfit":"app/o1.png"}}]}"#
        let server = FakeDraftServer(library: library)
        server.preset(outfit: "app/o1.png", seed: nil)
        let (composer, _) = await make(server)

        await composer.adoptDraftSelection()

        #expect(composer.outfits.first?.seedID == "s7")
    }

    @Test func resetEmptiesTheSelection() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(driverID: "app/d1.mp4")

        composer.reset()

        #expect(composer.outfits.isEmpty && composer.drivers.isEmpty)
        #expect(composer.capReason == nil && composer.failure == nil)
        #expect(composer.progress == nil && composer.lastAdded == nil)
        #expect(writes().isEmpty)
    }
```

Before running, check the library entry JSON shape against `TryonLibraryStoreTests.swift` (search for `"entries"`). If the fields differ (for example `saved_at` or `slots`), copy an entry from there verbatim and keep `id: "s7"`, `character: app/me.png`, `outfit: app/o1.png`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ios/MotionKit && swift test --filter BatchComposerTests 2>&1 | tail -20`
Expected: compile error, `value of type 'BatchComposer' has no member 'adoptDraftSelection'`.

- [ ] **Step 4: Implement**

Add to `BatchComposer`, after `setSeed(_:for:)`:

```swift
    /// New Job (2026-09-26 spec) shows one Outfit card and one Driver card, both
    /// backed by this selection, so a crossed role the draft carries — left
    /// there by Saved try-ons' "Use in job", the Telegram bot, or a draft from
    /// before the redesign — is moved in here and cleared on the draft in one
    /// PATCH. Otherwise the card would show nothing while the draft held a job
    /// that Run would submit. A dimension that already has a selection is left
    /// alone: a hand-built selection is never replaced by what the draft says.
    public func adoptDraftSelection() async {
        guard !isRunning, let current = draft.draft,
              let pipeline = draft.selectedPipeline, Self.supports(pipeline) else { return }
        var clear: [String: String?] = [:]
        var seed: DraftPatch.Seed = .keep
        if drivers.isEmpty, Self.supportsDrivers(pipeline),
           let driverID = current.filledSlots[Self.driverRole] {
            drivers = [driverID]
            clear[Self.driverRole] = .some(nil)
        }
        // After the driver, so `matches(for:)` reads the shared slots without it.
        if outfits.isEmpty, let outfitID = current.filledSlots[Self.outfitRole] {
            outfits = [CrossOutfit(outfitID: outfitID,
                                   seedID: current.tryonSeed ?? matches(for: outfitID).first?.id)]
            clear[Self.outfitRole] = .some(nil)
            seed = .clear
        }
        guard !clear.isEmpty else { return }
        selectionChanged()
        await draft.apply(DraftPatch(slots: clear, seed: seed))
    }

    /// Clear draft empties the draft on the server, but this selection lives
    /// only here, so it is emptied beside it. Also called when a pipeline
    /// without a character + outfit pair is selected: its cards cannot show
    /// the selection, and hidden outfits would still count as jobs.
    public func reset() {
        guard !isRunning else { return }
        outfits = []
        drivers = []
        failure = nil
        selectionChanged()
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ios/MotionKit && swift test --filter BatchComposerTests 2>&1 | tail -5`
Expected: all BatchComposerTests pass, the 5 new tests included.

- [ ] **Step 6: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionKit && git commit -m "MotionKit: BatchComposer adopts the draft's outfit and driver; reset empties the selection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 2: `NewJobState`

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/NewJobState.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/NewJobStateTests.swift`

**Interfaces:**
- Consumes: `BatchComposer.supports(_:)`, `BatchComposer.supportsDrivers(_:)`, `BatchComposer.outfitRole`, `.driverRole`, `Draft`, `Pipeline`.
- Produces:
  ```swift
  public struct NewJobState: Equatable, Sendable {
      public enum Card: Hashable, Sendable { case single(String), outfits, drivers }
      public let cards: [Card]
      public let required: Set<String>
      public let missing: [String]
      public let isBatch: Bool
      public let addCount: Int
      public let canContinue: Bool
      public let isFresh: Bool
      public init(pipeline: Pipeline, draft: Draft, outfits: Int, drivers: Int)
      public func role(of card: Card) -> String
      public func isFilled(_ card: Card) -> Bool
      public func next(after card: Card) -> Card?
  }
  ```

- [ ] **Step 1: Write the failing tests**

Create `NewJobStateTests.swift`:

```swift
import Foundation
import Testing
@testable import MotionKit

@Suite struct NewJobStateTests {
    private let catalog = try! MotionJSON.decoder.decode(
        PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines)).pipelines
    private var tryon: Pipeline { catalog[1] }       // character, driver, outfit + background
    private var motion: Pipeline { catalog[0] }      // character, driver

    /// A draft on `pipeline` whose filled slots are `filled`, `jobs` counted by the server.
    private func draft(_ pipeline: Pipeline, filled: [String: String], jobs: Int = 0) -> Draft {
        let probe = #"{"kind":"image","width":1,"height":1,"duration_s":null,"bitrate_kbps":null,"size_bytes":1,"warning":""}"#
        let slots = filled.map { #""\#($0.key)":{"material_id":"\#($0.value)","name":"\#($0.value)","exists":true,"probe":\#(probe),"warning":""}"# }
            .joined(separator: ",")
        let missing = pipeline.required.filter { filled[$0] == nil }.map { #""\#($0)""# }.joined(separator: ",")
        let required = pipeline.required.map { #""\#($0)""# }.joined(separator: ",")
        let optional = pipeline.optional.map { #""\#($0)""# }.joined(separator: ",")
        let json = #"{"owner":"app","pipeline":"\#(pipeline.id)","provider":"gemini","generation":1,"slots":{\#(slots)},"required":[\#(required)],"optional":[\#(optional)],"missing":[\#(missing)],"validated":null,"batch":[],"jobs":\#(jobs),"estimate_min":null}"#
        return try! MotionJSON.decoder.decode(Draft.self, from: Data(json.utf8))
    }

    @Test func aTryonPipelineCrossesOutfitAndDriver() {
        let s = NewJobState(pipeline: tryon, draft: draft(tryon, filled: [:]), outfits: 0, drivers: 0)
        #expect(s.isBatch)
        #expect(s.cards == [.single("character"), .drivers, .outfits, .single("background")])
        #expect(s.missing == ["character", "driver", "outfit"])
        #expect(s.addCount == 0 && !s.canContinue && s.isFresh)
    }

    @Test func aPipelineWithoutAnOutfitKeepsEveryCardSingle() {
        let s = NewJobState(pipeline: motion, draft: draft(motion, filled: ["character": "c"]), outfits: 0, drivers: 0)
        #expect(!s.isBatch)
        #expect(s.cards == [.single("character"), .single("driver")])
        #expect(s.missing == ["driver"])
        #expect(!s.isFresh)
    }

    @Test func addCountIsOutfitsTimesDriversOnceNothingIsMissing() {
        let d = draft(tryon, filled: ["character": "c"])
        #expect(NewJobState(pipeline: tryon, draft: d, outfits: 3, drivers: 0).addCount == 0)  // driver missing
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 3, drivers: 2)
        #expect(s.missing.isEmpty)
        #expect(s.addCount == 6 && s.canContinue)
    }

    @Test func aSharedDriverOnTheDraftCountsAsFilled() {
        let d = draft(tryon, filled: ["character": "c", "driver": "d"])
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 2, drivers: 0)
        #expect(s.isFilled(.drivers))
        #expect(s.addCount == 2)
    }

    @Test func aSinglePipelineAddsTheEditedJobAndContinuesOnServerJobs() {
        let complete = draft(motion, filled: ["character": "c", "driver": "d"], jobs: 1)
        let s = NewJobState(pipeline: motion, draft: complete, outfits: 0, drivers: 0)
        #expect(s.addCount == 1 && s.canContinue)
        // Basket only, edited job incomplete: nothing to add, still something to run.
        let basketOnly = draft(motion, filled: ["character": "c"], jobs: 2)
        let t = NewJobState(pipeline: motion, draft: basketOnly, outfits: 0, drivers: 0)
        #expect(t.addCount == 0 && t.canContinue)
    }

    @Test func nextSkipsFilledAndOptionalCardsAndEndsWithNil() {
        let d = draft(tryon, filled: ["character": "c"])
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 0, drivers: 0)
        #expect(s.next(after: .single("character")) == .drivers)
        let t = NewJobState(pipeline: tryon, draft: d, outfits: 0, drivers: 1)
        #expect(t.next(after: .drivers) == .outfits)
        let u = NewJobState(pipeline: tryon, draft: d, outfits: 1, drivers: 1)
        #expect(u.next(after: .outfits) == nil)       // background is optional
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter NewJobStateTests 2>&1 | tail -5`
Expected: compile error, `cannot find 'NewJobState' in scope`.

- [ ] **Step 3: Implement**

Create `NewJobState.swift`:

```swift
import Foundation

/// What New Job's single stage shows and allows (2026-09-26 spec), computed
/// from the draft and the composer's selection sizes so the rules are unit
/// tested rather than spread over view bodies. On a pipeline with a character
/// + outfit pair, the Outfit card and (when the pipeline has one) the Driver
/// card are the composer's multi-selects. Every other card is one draft slot.
public struct NewJobState: Equatable, Sendable {
    public enum Card: Hashable, Sendable { case single(String), outfits, drivers }

    public let cards: [Card]
    public let required: Set<String>
    /// Required roles still empty, in the pipeline's order.
    public let missing: [String]
    public let isBatch: Bool
    /// Jobs Add would put in the basket now; 0 means Add has nothing to do.
    public let addCount: Int
    /// Continue has something to run: a pending add, or jobs the server
    /// already counts (the basket plus a complete edited job — `drafts.py`
    /// `jobs_for`).
    public let canContinue: Bool
    /// Nothing picked anywhere: the only state that chains the pickers.
    public let isFresh: Bool

    private let filled: Set<String>

    public init(pipeline: Pipeline, draft: Draft, outfits: Int, drivers: Int) {
        let roles = pipeline.required + pipeline.optional
        let batch = BatchComposer.supports(pipeline)
        let crossDrivers = batch && BatchComposer.supportsDrivers(pipeline)
        isBatch = batch
        required = Set(pipeline.required)
        cards = roles.map { role in
            if batch, role == BatchComposer.outfitRole { return .outfits }
            if crossDrivers, role == BatchComposer.driverRole { return .drivers }
            return .single(role)
        }
        let draftFilled = Set(draft.slots.filter { $0.value.materialID != nil && $0.value.exists }.keys)
        var filled = draftFilled
        if batch {
            if outfits > 0 { filled.insert(BatchComposer.outfitRole) } else { filled.remove(BatchComposer.outfitRole) }
            // An empty driver selection falls back to the draft's shared driver slot.
            if crossDrivers, drivers > 0 { filled.insert(BatchComposer.driverRole) }
        }
        self.filled = filled
        missing = pipeline.required.filter { !filled.contains($0) }
        if !missing.isEmpty {
            addCount = 0
        } else if batch {
            addCount = outfits * max(drivers, 1)
        } else {
            addCount = 1
        }
        canContinue = addCount > 0 || draft.jobs > 0
        isFresh = draftFilled.isEmpty && outfits == 0 && drivers == 0
    }

    public func role(of card: Card) -> String {
        switch card {
        case .single(let role): role
        case .outfits: BatchComposer.outfitRole
        case .drivers: BatchComposer.driverRole
        }
    }

    public func isFilled(_ card: Card) -> Bool { filled.contains(role(of: card)) }

    /// The next empty required card after `card`, in card order; nil when none
    /// is left, which is when a chained pick closes its sheet.
    public func next(after card: Card) -> Card? {
        let start = (cards.firstIndex(of: card) ?? -1) + 1
        // After `card` first, then wrap to the cards before it.
        return (Array(cards[start...]) + Array(cards[..<start])).first {
            $0 != card && required.contains(role(of: $0)) && !isFilled($0)
        }
    }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ios/MotionKit && swift test 2>&1 | tail -5`
Expected: the whole MotionKit suite passes.

- [ ] **Step 5: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionKit && git commit -m "MotionKit: NewJobState, the single stage's cards, readiness and chain order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 3: `SlotCard` and `SlotCardGrid`

**Files:**
- Create: `ios/MotionApp/NewJob/SlotCard.swift`, `ios/MotionApp/NewJob/SlotCardGrid.swift`

**Interfaces:**
- Consumes: `SlotText` (`SlotRow.swift`), `MaterialPeek`, `MaterialsStore.thumbnail(for:)`, `CrossOutfit`.
- Produces:
  ```swift
  struct SlotCardItem: Identifiable, Equatable { let id: String; let seeded: Bool }  // id = material id
  struct SlotCard: View {
      init(role: String, required: Bool, kind: PipelineRoleKind, slot: DraftSlot?,
           items: [SlotCardItem]?, materials: MaterialsStore, disabled: Bool,
           identifier: String?, onTap: @escaping () -> Void,
           menu: @escaping (SlotCardItem?) -> AnyView)
  }
  struct SlotCardGrid<Content: View>: View { init(count: Int, @ViewBuilder content: @escaping (CGSize) -> Content) }
  ```
  `items == nil` is a single-select card that reads `slot`. `items != nil` is a multi-select card; an empty array means nothing picked. `menu(item)` builds the long-press menu for the item on screen (nil for a single card).

- [ ] **Step 1: Create `SlotCardGrid.swift`**

```swift
import SwiftUI

/// The stage's cards, two across, sized to the space the grid is given so New
/// Job never scrolls (2026-09-26 spec §1). The old grid used a fixed 3:4 per
/// tile, and in Batch mode that plus three other sections ran past the screen.
/// A card's picture is at most 3:4. When height is short (iPhone SE, or a
/// banner showing), the picture gets shorter instead of the stage scrolling.
struct SlotCardGrid<Content: View>: View {
    let count: Int
    @ViewBuilder let content: (CGSize) -> Content

    static var spacing: CGFloat { 12 }
    /// Title and subtitle under each picture.
    static var captionHeight: CGFloat { 40 }

    var body: some View {
        GeometryReader { proxy in
            let columns = count <= 1 ? 1 : 2
            let rows = max(Int((Double(count) / Double(columns)).rounded(.up)), 1)
            let width = (proxy.size.width - Self.spacing * CGFloat(columns - 1)) / CGFloat(columns)
            let fitHeight = (proxy.size.height - Self.spacing * CGFloat(rows - 1)) / CGFloat(rows)
                - Self.captionHeight
            let picture = CGSize(width: width, height: max(min(width * 4 / 3, fitHeight), 60))
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(width), spacing: Self.spacing), count: columns),
                      spacing: Self.spacing) {
                content(picture)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
```

- [ ] **Step 2: Create `SlotCard.swift`**

```swift
import MotionKit
import SwiftUI

/// One chosen material of a multi-select card; `seeded` draws the saved
/// try-on badge on an outfit.
struct SlotCardItem: Identifiable, Equatable {
    let id: String
    let seeded: Bool
}

/// One input of the job, picture first (2026-09-26 spec §2). A single-select
/// card shows its draft slot. A multi-select card (Outfit, Driver on a
/// try-on pipeline) pages horizontally through its picks with page dots, and
/// shows "×N" in the corner. Tap opens the picker, and long press opens the
/// menu for what is on screen. The ✕ glyph the tiles had until this change was
/// under 44 pt, so Clear lives in that menu. The accessibility label and
/// value are `SlotText`'s, which the UI smokes read.
@MainActor
struct SlotCard: View {
    let role: String
    let required: Bool
    let kind: PipelineRoleKind
    let slot: DraftSlot?
    let items: [SlotCardItem]?
    let materials: MaterialsStore
    let disabled: Bool
    let identifier: String?
    let size: CGSize
    let onTap: () -> Void
    let menu: (SlotCardItem?) -> AnyView
    @State private var page: String?

    private var text: SlotText { SlotText(role: role, required: required, kind: kind, slot: slot) }
    private var picks: [SlotCardItem] { items ?? [] }
    private var isMulti: Bool { items != nil }
    private var filled: Bool { isMulti ? !picks.isEmpty : text.assigned }

    private var accessibilityValue: String {
        guard isMulti else { return text.state }
        if picks.isEmpty { return required ? "Missing required" : "Empty optional" }
        return "\(picks.count) selected"
    }

    private var subtitle: String {
        if isMulti {
            return picks.isEmpty ? (required ? "Required · pick many" : "Optional") : "\(picks.count) selected"
        }
        return text.assigned ? text.value : (required ? "Required" : "Optional")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            picture
                .frame(width: size.width, height: size.height)
                .clipShape(.rect(cornerRadius: Theme.Radius.medium))
                .contentShape(.rect)
                .onTapGesture { if !disabled { onTap() } }
                .contextMenu {
                    if filled, !disabled { menu(currentItem) }
                } preview: {
                    if let material = currentMaterial { MaterialPeek(material: material, materials: materials) }
                }
                .overlay(alignment: .topTrailing) {
                    if picks.count > 1 {
                        Text("×\(picks.count)")
                            .font(.caption.weight(.bold).monospacedDigit())
                            .foregroundStyle(Theme.onAccent)
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(Theme.accent, in: .capsule)
                            .padding(6)
                    }
                }
                .overlay(alignment: .topLeading) {
                    if let warning = slot?.warning, !warning.isEmpty, !isMulti {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(Theme.warning)
                            .padding(6).background(.black.opacity(0.5), in: .circle).padding(6)
                    }
                }
            VStack(alignment: .leading, spacing: 1) {
                Text(text.title)
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.label)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Text(subtitle)
                    .font(.caption).foregroundStyle(Theme.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
            .frame(height: SlotCardGrid<EmptyView>.captionHeight - 6, alignment: .top)
        }
        .opacity(disabled ? 0.6 : 1)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text.title)
        .accessibilityValue(accessibilityValue)
        .accessibilityHint(isMulti ? "Choose one or more materials" : "Choose a material")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { if !disabled { onTap() } }
        .accessibilityIdentifier(identifier ?? role)
    }

    private var currentItem: SlotCardItem? {
        isMulti ? (picks.first { $0.id == page } ?? picks.first) : nil
    }

    private var currentMaterial: MotionKit.Material? {
        let id = isMulti ? currentItem?.id : slot?.materialID
        return id.flatMap { id in materials.materials.first { $0.id == id } }
    }

    @ViewBuilder private var picture: some View {
        if isMulti, !picks.isEmpty {
            TabView(selection: $page) {
                ForEach(picks) { item in
                    CardThumbnail(materialID: item.id, kind: kind, materials: materials)
                        .overlay(alignment: .bottomTrailing) {
                            if item.seeded {
                                Image(systemName: "photo.badge.checkmark")
                                    .font(.caption).foregroundStyle(Theme.onAccent)
                                    .padding(5).background(Theme.accent, in: .circle).padding(6)
                                    .accessibilityLabel("Saved try-on")
                            }
                        }
                        .tag(Optional(item.id))
                }
            }
            .tabViewStyle(.page(indexDisplayMode: picks.count > 1 ? .always : .never))
        } else if !isMulti, text.assigned, let id = slot?.materialID {
            CardThumbnail(materialID: id, kind: kind, materials: materials)
        } else {
            EmptyCardFace(kind: kind, multi: isMulti)
        }
    }
}

/// A material's thumbnail filling the card, fetched the way `SlotMaterialRow`
/// did it (a `@ViewBuilder` cannot hold the `@State`).
@MainActor
private struct CardThumbnail: View {
    let materialID: String
    let kind: PipelineRoleKind
    let materials: MaterialsStore
    @State private var data: Data?

    private var material: MotionKit.Material? { materials.materials.first { $0.id == materialID } }

    var body: some View {
        ZStack {
            Theme.surfaceRaised
            if let data, let image = UIImage(data: data) {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: kind == .video ? "film" : "photo").font(.title3).foregroundStyle(Theme.tertiary)
            }
        }
        .overlay(alignment: .bottomLeading) {
            if material?.kind == .video {
                Image(systemName: "video.fill").font(.caption2).foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.6), radius: 3).padding(8)
            }
        }
        .task(id: materialID) {
            guard let material else { data = nil; return }
            data = await materials.thumbnail(for: material)
        }
    }
}

/// The dashed "tap to fill" face, kept from `SlotTile` so an empty card
/// reads the same as before.
private struct EmptyCardFace: View {
    let kind: PipelineRoleKind
    let multi: Bool

    var body: some View {
        RoundedRectangle(cornerRadius: Theme.Radius.medium)
            .strokeBorder(Theme.accent.opacity(0.6), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
            .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
            .overlay {
                VStack(spacing: 6) {
                    Image(systemName: multi ? "plus.square.on.square" : "plus")
                        .font(.title2.weight(.medium)).foregroundStyle(Theme.accent)
                    Image(systemName: kind == .video ? "film" : kind == .image ? "photo" : "questionmark.square.dashed")
                        .font(.footnote).foregroundStyle(Theme.tertiary)
                }
            }
    }
}
```

- [ ] **Step 3: Build**

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | grep -E "error|warning: unused|BUILD" | head -20`
Expected: no `error:` lines (the new views are not used yet).

- [ ] **Step 4: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionApp/NewJob/SlotCard.swift ios/MotionApp/NewJob/SlotCardGrid.swift && \
git commit -m "iOS New Job: SlotCard and a grid that sizes cards to the screen

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 4: `SettingsChip` and `SettingsSheet`

**Files:**
- Create: `ios/MotionApp/NewJob/SettingsChip.swift`
- Modify: `ios/MotionApp/NewJob/PipelinePicker.swift`: delete `struct PipelinePicker` and `SettingRow`. Make `PipelineChoiceList` and `ProviderChoiceList` internal (drop `private`), and add an `onDone: () -> Void` parameter to both that replaces their `dismiss()` calls.

**Interfaces:**
- Consumes: `PipelineText`, `ProviderText`, `ProviderMark`.
- Produces:
  ```swift
  struct SettingsChip: View {
      init(pipeline: Pipeline, pipelines: [Pipeline], selectedProvider: String, disabled: Bool,
           onPipelineSelected: @escaping (String) async -> Void,
           onProviderSelected: @escaping (String) async -> Void)
  }
  ```

- [ ] **Step 1: Edit the choice lists in `PipelinePicker.swift`**

In `PipelineChoiceList` and `ProviderChoiceList`: remove `private` from the struct, remove `@Environment(\.dismiss) private var dismiss`, add `let onDone: () -> Void` as the last stored property, and replace each `dismiss()` with `onDone()`. Drop `.navigationTitle`/`.navigationBarTitleDisplayMode` from both, since the sheet names its sections. Delete `struct PipelinePicker` (lines 1–49 of the current file, from its doc comment to its closing brace) and `private struct SettingRow`. Keep the file header comment short:

```swift
import MotionKit
import SwiftUI

// Pipeline and provider display helpers and choice lists. The choices open
// from `SettingsChip` since 2026-09-26; before that they were a Settings
// section under the materials.
```

- [ ] **Step 2: Create `SettingsChip.swift`**

```swift
import MotionKit
import SwiftUI

/// The pipeline and provider as one toolbar chip, where the Single | Batch
/// switch was (2026-09-26 spec §1). Until then they were a Settings section
/// below the materials, and a growing basket pushed it under the action bar.
/// The label is "Pipeline" and the value is the pipeline's name: the UI smokes
/// find the pipeline control by that pair.
@MainActor
struct SettingsChip: View {
    let pipeline: Pipeline
    let pipelines: [Pipeline]
    let selectedProvider: String
    let disabled: Bool
    let onPipelineSelected: (String) async -> Void
    let onProviderSelected: (String) async -> Void
    @State private var open = false

    private var provider: PipelineProvider? {
        pipeline.providers.first { $0.id == selectedProvider } ?? pipeline.providers.first
    }

    var body: some View {
        Button { open = true } label: {
            HStack(spacing: 6) {
                if let provider { ProviderMark(id: provider.id).frame(width: 18, height: 18) }
                Text(PipelineText.name(pipeline.id)).lineLimit(1)
                if let provider {
                    Text("· \(ProviderText(label: provider.label).name)")
                        .foregroundStyle(Theme.secondary).lineLimit(1)
                }
                Image(systemName: "chevron.down").font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.secondary)
            }
            .font(.subheadline.weight(.semibold))
            .frame(maxWidth: 240)
        }
        .disabled(disabled)
        .accessibilityLabel("Pipeline")
        .accessibilityValue(PipelineText.name(pipeline.id))
        .sheet(isPresented: $open) {
            NavigationStack {
                List {
                    Section("Pipeline") {
                        PipelineChoiceList(current: pipeline.id, pipelines: pipelines,
                                           name: PipelineText.name, stages: PipelineText.stages,
                                           onSelected: onPipelineSelected, onDone: { open = false })
                    }
                    if !pipeline.providers.isEmpty {
                        Section {
                            ProviderChoiceList(providers: pipeline.providers, current: selectedProvider,
                                               onSelected: onProviderSelected, onDone: { open = false })
                        } header: {
                            Text("Provider")
                        } footer: {
                            Text("Hosted providers make the try-on here, before any pod is rented.")
                        }
                    }
                }
                .navigationTitle("Settings")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { open = false } } }
            }
            .presentationDetents([.medium, .large])
        }
    }
}
```

Both choice lists currently render their own `List { Section { ForEach … } footer: … }`. Nested inside the sheet's `List`, change their `body` to render only the `ForEach` of rows (drop their own `List`, `Section` and footer), so the sheet's two sections hold the rows directly. The pipeline footer text ("Stages run left to right; each one's output feeds the next.") moves to the sheet's Pipeline section as its footer:

```swift
                    Section {
                        PipelineChoiceList(…)
                    } header: { Text("Pipeline") } footer: {
                        Text("Stages run left to right; each one's output feeds the next.")
                    }
```

- [ ] **Step 3: Keep the build green**

`NewJobView` still instantiates `PipelinePicker`. Replace that call (inside `List { … }` in `editor(draft:pipeline:)`) with nothing, and add the chip in place of the mode picker's `ToolbarItem(placement: .principal)` body. It will be rewritten in Task 8. For now, the principal item holds:

```swift
                SettingsChip(pipeline: pipeline, pipelines: isBatch ? batchCatalog : store.catalog,
                             selectedProvider: draft.provider,
                             disabled: store.isBusy || composer.isRunning || (isBatch && batchCatalog.isEmpty),
                             onPipelineSelected: { id in await store.selectPipeline(id) },
                             onProviderSelected: { id in await store.selectProvider(id) })
```

Move the Single | Batch `Picker` into `moreMenu` as `Picker("Mode", …).pickerStyle(.inline)` above "Clear draft", so both modes stay reachable until Task 8 deletes it.

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | grep -E "error" | head`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionApp/NewJob && git commit -m "iOS New Job: pipeline and provider move into a toolbar chip and one sheet

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 5: `BasketDrawer`

**Files:**
- Create: `ios/MotionApp/NewJob/BasketDrawer.swift`

**Interfaces:**
- Consumes: `BatchEntryRow(index:entry:pipeline:materials:dropDisabled:onOpen:onDrop:)`, `BatchEntryText.subtitle(_:pipeline:)`, `BatchEntryMaterialTile`.
- Produces:
  ```swift
  struct BasketDrawer: View {
      init(batch: [DraftBatchEntry], pipeline: @escaping (DraftBatchEntry) -> Pipeline?,
           materials: MaterialsStore, locked: Bool, expanded: Binding<Bool>,
           onOpen: @escaping (DraftBatchEntry) -> Void,
           onDrop: @escaping (DraftBatchEntry) async -> Void,
           clearAll: AnyView)
  }
  ```

- [ ] **Step 1: Create the drawer**

```swift
import MotionKit
import SwiftUI

/// The basket above the action bar (2026-09-26 spec §1): collapsed it is one
/// line, "Batch · N" and the first jobs' outfits; tapped or dragged up it lists
/// the jobs over the cards. It is not a system sheet, which would cover the tab
/// bar and be dismissed by every picker, since iOS shows one sheet at a time.
/// The list inside is the stage's only scroll.
@MainActor
struct BasketDrawer: View {
    let batch: [DraftBatchEntry]
    let pipeline: (DraftBatchEntry) -> Pipeline?
    let materials: MaterialsStore
    let locked: Bool
    @Binding var expanded: Bool
    let onOpen: (DraftBatchEntry) -> Void
    let onDrop: (DraftBatchEntry) async -> Void
    let clearAll: AnyView
    @State private var dropCandidate: DraftBatchEntry?
    @GestureState private var drag: CGFloat = 0

    var body: some View {
        VStack(spacing: 0) {
            handle
            if expanded { list.transition(.move(edge: .bottom).combined(with: .opacity)) }
        }
        .background(.regularMaterial, in: .rect(cornerRadius: 20))
        .offset(y: max(drag, expanded ? 0 : -40) * (expanded ? 1 : 0.3))
        .gesture(
            DragGesture(minimumDistance: 12)
                .updating($drag) { value, state, _ in state = value.translation.height }
                .onEnded { value in
                    withAnimation(.snappy) {
                        if value.translation.height < -40 { expanded = true }
                        if value.translation.height > 40 { expanded = false }
                    }
                })
        .animation(.snappy, value: expanded)
    }

    private var handle: some View {
        Button { withAnimation(.snappy) { expanded.toggle() } } label: {
            HStack(spacing: 10) {
                Image(systemName: expanded ? "chevron.down" : "chevron.up")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                // Its own `Text`: the smokes read "Batch · N" by exact string.
                Text("Batch · \(batch.count)").font(.subheadline.weight(.semibold))
                if !expanded {
                    HStack(spacing: -8) {
                        ForEach(batch.prefix(4), id: \.digest) { entry in
                            BatchEntryMaterialTile(role: "outfit",
                                                   materialID: (entry.slots["outfit"] ?? nil) ?? entry.slots.values.compactMap { $0 }.first,
                                                   kind: .image, materials: materials, width: 22,
                                                   showsTitle: false)
                        }
                    }
                    .accessibilityHidden(true)
                }
                Spacer(minLength: 0)
                if expanded { clearAll }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint(expanded ? "Collapse the batch" : "Show the batch")
        .accessibilityIdentifier("newjob.basket")
    }

    private var list: some View {
        List {
            ForEach(Array(batch.enumerated()), id: \.element.digest) { offset, entry in
                BatchEntryRow(index: offset + 1, entry: entry, pipeline: pipeline(entry),
                              materials: materials, dropDisabled: locked,
                              onOpen: { onOpen(entry) }, onDrop: { dropCandidate = entry })
                    .swipeActions(edge: .trailing) {
                        Button("Drop", systemImage: "trash", role: .destructive) { dropCandidate = entry }
                            .disabled(locked)
                    }
                    // On the row, so the popover points at the job it drops.
                    .confirmationDialog(
                        "Drop this batch entry?",
                        isPresented: Binding(get: { dropCandidate?.digest == entry.digest },
                                             set: { if !$0 { dropCandidate = nil } }),
                        titleVisibility: .visible
                    ) {
                        Button("Drop", role: .destructive) {
                            dropCandidate = nil
                            Task { await onDrop(entry) }
                        }
                        Button("Cancel", role: .cancel) { dropCandidate = nil }
                    } message: {
                        Text("Job \(offset + 1) · \(BatchEntryText.subtitle(entry, pipeline: pipeline(entry)))")
                    }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
    }
}
```

`BatchEntryMaterialTile` draws a role caption under the picture. In this step add `var showsTitle = true` to `BatchEntryMaterialTile` in `BatchEntryViews.swift`, declared right after `var showsName = false` so the memberwise order matches the call above, and wrap its `Text(title)` in `if showsTitle { … }`.

The drawer's height when expanded comes from its container: Task 8 caps it with `.frame(maxHeight: proxy.size.height * 0.7)`.

- [ ] **Step 2: Build**

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | grep -E "error" | head`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionApp/NewJob && git commit -m "iOS New Job: basket drawer with swipe to drop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 6: `NewJobActionBar` with Add and a validating Continue

**Files:**
- Rewrite: `ios/MotionApp/NewJob/NewJobActionBar.swift`
- Delete: `ios/MotionApp/NewJob/BatchRunBar.swift`

**Interfaces:**
- Consumes: `NewJobState` (Task 2), `BatchComposer.run()`, `.failure`, `.progress`, `.lastAdded`, `.capReason`, `.jobCount`, `.tryonCount`, `.outfits`, `.drivers`, `DraftStore.addToBatch()`, `.validate()`, `.isReady`, `.isValidating`, `.validationWasStale`, `.isBusy`.
- Produces:
  ```swift
  struct NewJobActionBar: View {
      init(store: DraftStore, composer: BatchComposer, draft: Draft, state: NewJobState,
           onContinue: @escaping () -> Void)
  }
  ```

- [ ] **Step 1: Rewrite the file**

```swift
import MotionKit
import SwiftUI

/// New Job's next step, pinned above the tab bar (2026-09-26 spec §3). Two
/// buttons with fixed meanings. **Add N** queues what is being composed.
/// **Continue** runs everything on screen plus the basket: it adds a pending
/// composition first, then validates, then opens the run flow. Before this
/// change Validate was a step of its own between Add and "Continue to run",
/// and Batch mode's summary lived in a separate `BatchRunBar`.
@MainActor
struct NewJobActionBar: View {
    let store: DraftStore
    let composer: BatchComposer
    let draft: Draft
    let state: NewJobState
    let onContinue: () -> Void
    @State private var continuing = false

    private var locked: Bool { store.isBusy || composer.isRunning || continuing }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            statusLines
            HStack(spacing: 10) {
                if state.addCount > 0 || composer.failure != nil { addButton }
                continueButton
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
        // A container, so the UI smokes can tell a card under the bar from one above it.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("newjob.actionBar")
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }

    private func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }

    @ViewBuilder private var statusLines: some View {
        if state.isBatch, !composer.outfits.isEmpty {
            Text("\(count(composer.outfits.count, "outfit")) × \(count(max(composer.drivers.count, 1), "driver")) = "
                 + "\(count(composer.jobCount, "video")) · \(count(composer.tryonCount, "try-on"))")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .accessibilityIdentifier("batch.summary")
        }
        if let progress = composer.progress, composer.isRunning {
            HStack(spacing: 8) {
                ProgressView()
                Text("Adding \(progress.done) of \(progress.total)…").font(.subheadline.monospacedDigit())
            }
            .accessibilityIdentifier("batch.progress")
        }
        if let failure = composer.failure {
            Label(failure, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline).foregroundStyle(Theme.danger)
                .accessibilityIdentifier("batch.failure")
        }
        if let added = composer.lastAdded {
            // Plain `Text`: the smokes find this line by its exact string.
            Text("Added \(added) job\(added == 1 ? "" : "s") to the batch.")
                .font(.subheadline).foregroundStyle(Theme.secondary)
        }
        if let capReason = composer.capReason {
            Text(capReason).font(.footnote).foregroundStyle(Theme.warning)
                .accessibilityIdentifier("batch.capReason")
        }
        if store.isReady, !continuing {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.label)
                // Separate `Text`s: the smokes find "Ready" by its exact string.
                Text("Ready").font(.subheadline.weight(.semibold))
                if let estimate = draft.estimateMin {
                    Text("· about \(estimate) min").font(.subheadline.monospacedDigit())
                        .foregroundStyle(Theme.secondary)
                }
            }
        } else if store.validationWasStale {
            Label("The draft changed during validation. Tap Continue again.",
                  systemImage: "arrow.triangle.2.circlepath")
                .font(.subheadline).foregroundStyle(Theme.warning)
        } else if !state.missing.isEmpty, !state.canContinue {
            Text("Pick \(missingNames) to continue.")
                .font(.footnote).foregroundStyle(Theme.secondary)
        }
    }

    private var missingNames: String {
        ListFormatter.localizedString(byJoining: state.missing.map {
            SlotText(role: $0, required: true, kind: .unknown, slot: nil).title
        })
    }

    /// "Add to batch" keeps its old label on single pipelines, which the
    /// Phase 3 smoke finds by name. On a try-on pipeline it names the job
    /// count, or "Continue" after a stopped build, which is the composer's
    /// resume. The identifier `batch.run` stays on both.
    private var addButton: some View {
        let title = !state.isBatch ? "Add to batch"
            : composer.failure != nil ? "Continue"
            : "Add \(count(state.addCount, "job"))"
        return Button(title) { Task { await add() } }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(locked || (state.isBatch ? !composer.canRun : state.addCount == 0))
            .accessibilityIdentifier(state.isBatch ? "batch.run" : "newjob.add")
    }

    private var continueButton: some View {
        Button { Task { await runContinue() } } label: {
            HStack(spacing: 8) {
                if continuing || store.isValidating { ProgressView().tint(Theme.onAccent) }
                Text("Continue")
            }
        }
        .buttonStyle(PrimaryButtonStyle())
        .disabled(locked || !state.canContinue || composer.failure != nil)
        .accessibilityIdentifier("newjob.continueToRun")
    }

    @discardableResult
    private func add() async -> Bool {
        if state.isBatch {
            await composer.run()
            return composer.failure == nil
        }
        return await store.addToBatch()
    }

    /// Add what is pending, validate, open the run flow — stopping at the
    /// first step that fails, whose message is already on screen (the
    /// composer's failure line, or the store's banner). A stopped build keeps
    /// Continue disabled until its own "Continue" finishes it, so a half-added
    /// batch never goes to validation.
    private func runContinue() async {
        continuing = true
        defer { continuing = false }
        if state.isBatch, !composer.outfits.isEmpty {
            guard await add() else { return }
        }
        if !store.isReady { await store.validate() }
        if store.isReady { onContinue() }
    }
}
```

On a single pipeline, Continue does not add the edited job first, because the server's `jobs` already counts a complete edited job (`scripts/control/drafts.py` `jobs_for`). Validation covers it, and Run submits it.

- [ ] **Step 2: Delete `BatchRunBar.swift` and fix the call site**

`git rm ios/MotionApp/NewJob/BatchRunBar.swift`. In `NewJobView.editor`, replace the `.safeAreaInset(edge: .bottom) { … }` block and the `.animation(.snappy, value: NewJobActionBar.isVisible(…))` line that follows it with:

```swift
        .safeAreaInset(edge: .bottom) {
            NewJobActionBar(store: store, composer: composer, draft: draft,
                            state: NewJobState(pipeline: pipeline, draft: draft,
                                               outfits: composer.outfits.count, drivers: composer.drivers.count),
                            onContinue: { showRun = true })
        }
```

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | grep -E "error" | head`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add -A ios/MotionApp/NewJob && git commit -m "iOS New Job: one action bar, Add N and a Continue that validates on tap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 7: `PickerChainSheet`

**Files:**
- Create: `ios/MotionApp/NewJob/PickerChainSheet.swift`
- Modify: `ios/MotionApp/NewJob/MaterialPicker.swift` (add `var onAdvance: (() -> Void)? = nil`)
- Modify: `ios/MotionApp/NewJob/MaterialMultiPicker.swift` (add `var onNext: (() -> Void)? = nil`)

**Interfaces:**
- Consumes: `NewJobState.Card`, `NewJobState.next(after:)`, `MaterialPicker`, `MaterialMultiPicker`, `BatchComposer.toggle(outfitID:)`, `.toggle(driverID:)`, `DraftStore.assign(role:materialID:)`.
- Produces:
  ```swift
  struct PickTarget: Identifiable, Equatable { let card: NewJobState.Card; let chained: Bool; var id: String }
  struct PickerChainSheet: View {
      init(start: PickTarget, pipeline: Pipeline, store: DraftStore, composer: BatchComposer,
           materials: MaterialsStore, onClose: @escaping () -> Void)
  }
  ```

- [ ] **Step 1: Let the pickers hand control back instead of dismissing**

In `MaterialPicker.swift`, add under `let onSelect`:

```swift
    /// Set by a chained pick (2026-09-26 spec §3): after a selection the sheet
    /// moves to the next empty card instead of closing.
    var onAdvance: (() -> Void)?
```

Replace each of the two `onSelect(material.id); dismiss()` pairs (the import bar callback at ~line 33 and the grid tap at ~line 118) with:

```swift
                            onSelect(material.id)
                            if let onAdvance { onAdvance() } else { dismiss() }
```

In `MaterialMultiPicker.swift`, add under `let toggle`:

```swift
    /// Set by a chained pick: a Next button walks on to the next empty card;
    /// Done still closes the chain.
    var onNext: (() -> Void)?
```

and in `.toolbar`, before the Done item:

```swift
                if let onNext {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Next", action: onNext).disabled(chosenCount == 0)
                    }
                    ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                } else {
                    ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                }
```

Remove the now-duplicated unconditional Done item.

- [ ] **Step 2: Create `PickerChainSheet.swift`**

```swift
import MotionKit
import SwiftUI

/// What a picker sheet is open for. `chained` is decided when the sheet opens:
/// only a pick started on a fresh draft walks on to the next card
/// (2026-09-26 spec §3). Editing one card of a filled draft closes as before.
struct PickTarget: Identifiable, Equatable {
    let card: NewJobState.Card
    let chained: Bool
    var id: String { "\(card)" }
}

/// One sheet for every card's picker. In a chain it swaps its picker in place
/// for the next empty required card, so a first job is picked in one pass
/// without closing and reopening the sheet. It closes when no required card is
/// empty, or on Done or a swipe down.
@MainActor
struct PickerChainSheet: View {
    let start: PickTarget
    let pipeline: Pipeline
    let store: DraftStore
    let composer: BatchComposer
    let materials: MaterialsStore
    let onClose: () -> Void
    @State private var card: NewJobState.Card?

    private var current: NewJobState.Card { card ?? start.card }

    private var state: NewJobState? {
        store.draft.map { NewJobState(pipeline: pipeline, draft: $0,
                                      outfits: composer.outfits.count, drivers: composer.drivers.count) }
    }

    var body: some View {
        Group {
            switch current {
            case .single(let role):
                MaterialPicker(role: role, kind: pipeline.roles[role] ?? .unknown,
                               selectedID: store.draft?.slots[role]?.materialID, materials: materials,
                               onSelect: { id in Task { await store.assign(role: role, materialID: id); if start.chained { advance() } } },
                               onAdvance: start.chained ? {} : nil)
                    .interactiveDismissDisabled(store.isBusy)
            case .outfits:
                MaterialMultiPicker(
                    title: "Choose outfits", kind: pipeline.roles[BatchComposer.outfitRole] ?? .image,
                    role: .outfit, materials: materials, identifierPrefix: "outfit.pick",
                    disabled: composer.isRunning, note: composer.capReason,
                    isChosen: { id in composer.outfits.contains { $0.outfitID == id } },
                    toggle: { composer.toggle(outfitID: $0) },
                    onNext: start.chained ? { advance() } : nil)
            case .drivers:
                MaterialMultiPicker(
                    title: "Choose drivers", kind: pipeline.roles[BatchComposer.driverRole] ?? .video,
                    role: .driver, materials: materials, identifierPrefix: "driver.pick",
                    disabled: composer.isRunning, note: composer.capReason,
                    isChosen: { composer.drivers.contains($0) },
                    toggle: { composer.toggle(driverID: $0) },
                    onNext: start.chained ? { advance() } : nil)
            }
        }
        .id(current)
        .transition(.push(from: .trailing))
    }

    /// Runs after the pick has landed (single cards await `assign`), so
    /// `next(after:)` reads the card just filled as filled.
    private func advance() {
        guard let next = state?.next(after: current) else { return onClose() }
        withAnimation(.snappy) { card = next }
    }
}
```

`onAdvance: {}` for a single card in a chain is deliberate. It tells `MaterialPicker` not to dismiss, and the advance happens in `onSelect` after `assign` returns.

- [ ] **Step 3: Build**

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | grep -E "error" | head`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionApp/NewJob && git commit -m "iOS New Job: a picker sheet that walks a fresh draft's empty cards

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 8: Rewrite `NewJobView` as the stage; delete the mode

**Files:**
- Rewrite: `ios/MotionApp/NewJob/NewJobView.swift`
- Delete: `ios/MotionApp/NewJob/BatchComposerSection.swift`
- Modify: `ios/MotionApp/NewJob/SlotRow.swift`: delete `SlotRow` and `SlotTile`, keep `SlotText`
- Modify: `ios/MotionApp/MotionApp.swift:62` and `:103`: delete `enum NewJobMode` and `var newJobMode`
- Modify: `ios/MotionApp/Materials/SavedTryonsView.swift:75`: delete `model.newJobMode = .single`

**Interfaces:**
- Consumes: everything from Tasks 1–7.

- [ ] **Step 1: Delete the mode and the old views**

```bash
cd /Users/thucpham/Desktop/motion-clone/ios && git rm MotionApp/NewJob/BatchComposerSection.swift
```

In `MotionApp.swift`, delete line 62 (`enum NewJobMode …`) and line 103 (`var newJobMode …`). In `SavedTryonsView.swift`, delete the `model.newJobMode = .single` line inside `use(_:)`; New Job adopts the draft's outfit instead (Step 2, `adoptKey`). In `SlotRow.swift`, delete `struct SlotRow` and `struct SlotTile` with their doc comments, keeping `SlotText`.

- [ ] **Step 2: Rewrite `NewJobView.swift`**

```swift
import MotionKit
import SwiftUI

/// New Job as one stage that does not scroll (2026-09-26 spec): the job's
/// inputs as cards sized to the screen, the pipeline and provider in a
/// toolbar chip, the basket in a drawer, and Add / Continue pinned above the
/// tab bar. It replaces a `List` with a Single | Batch switch: in Batch mode
/// the shared slots, two strips, Settings and the basket stacked past the
/// screen, and the mode itself was one more thing to hold in mind. The mode
/// is now what is picked. Outfit and Driver take many on a try-on pipeline,
/// and one of each is one job.
@MainActor
struct NewJobView: View {
    let store: DraftStore
    let materials: MaterialsStore
    let flow: RunFlow
    let composer: BatchComposer
    let library: TryonLibraryStore
    @State private var pick: PickTarget?
    @State private var openEntry: DraftBatchEntry?
    @State private var showRun = false
    @State private var basketExpanded = false
    @State private var clearSource: ClearSource?

    var body: some View {
        Group {
            if let draft = store.draft, let pipeline = store.selectedPipeline {
                stage(draft: draft, pipeline: pipeline)
            } else if let error = store.error {
                initialLoadFailure(error)
            } else if store.isRefreshing {
                LoadingBlock(title: "Loading draft…")
            } else {
                ContentUnavailableView("New Job unavailable", systemImage: "exclamationmark.triangle")
            }
        }
        .navigationTitle("New Job")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.load() }
        .task { await library.load() }
        .onChange(of: store.needsMaterialsRefresh) { _, needsRefresh in
            guard needsRefresh else { return }
            Task {
                await materials.refresh()
                store.acknowledgeMaterialsRefresh()
            }
        }
        // Kept from before the mode split was removed, for the reason it was
        // written: a seed picked for the old character/outfit pair must never
        // be PATCHed for a new one. `library.loaded` because `matches(for:)` is
        // empty against an unfetched library.
        .onChange(of: composer.seedKey) { _, _ in composer.refreshSeeds() }
        .onChange(of: library.loaded) { _, loaded in
            if loaded { composer.refreshSeeds() }
        }
        // A crossed role on the draft (Saved try-ons' "Use in job", the
        // Telegram bot, a pre-redesign draft) moves into the composer, the
        // only place the Outfit and Driver cards read.
        .task(id: adoptKey) { await composer.adoptDraftSelection() }
    }

    private var adoptKey: [String] {
        [store.draft?.pipeline ?? "", store.draft?.filledSlots[BatchComposer.outfitRole] ?? "",
         store.draft?.filledSlots[BatchComposer.driverRole] ?? ""]
    }

    private var locked: Bool { store.isBusy || composer.isRunning }

    private func state(_ draft: Draft, _ pipeline: Pipeline) -> NewJobState {
        NewJobState(pipeline: pipeline, draft: draft,
                    outfits: composer.outfits.count, drivers: composer.drivers.count)
    }

    private func stage(draft: Draft, pipeline: Pipeline) -> some View {
        let state = state(draft, pipeline)
        return GeometryReader { proxy in
            SlotCardGrid(count: state.cards.count) { size in
                ForEach(state.cards, id: \.self) { card in
                    slotCard(card, state: state, draft: draft, pipeline: pipeline, size: size)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .overlay(alignment: .bottom) {
                if !draft.batch.isEmpty {
                    BasketDrawer(batch: draft.batch, pipeline: pipeline(for:), materials: materials,
                                 locked: locked, expanded: $basketExpanded,
                                 onOpen: { openEntry = $0 },
                                 onDrop: { await store.dropFromBatch($0.digest) },
                                 clearAll: AnyView(clearAllButton))
                        .frame(maxHeight: basketExpanded ? proxy.size.height * 0.7 : nil, alignment: .bottom)
                        .padding(.horizontal, 12)
                }
            }
            .overlay(alignment: .top) { banners.padding(.horizontal, 12) }
        }
        .toolbar {
            ToolbarItem(placement: .principal) {
                SettingsChip(pipeline: pipeline, pipelines: store.catalog, selectedProvider: draft.provider,
                             disabled: locked,
                             onPipelineSelected: { id in
                                 await store.selectPipeline(id)
                                 if let selected = store.selectedPipeline, !BatchComposer.supports(selected) {
                                     composer.reset()
                                 }
                             },
                             onProviderSelected: { id in await store.selectProvider(id) })
            }
            ToolbarItem(placement: .topBarLeading) {
                Text("\(draft.jobs) job\(draft.jobs == 1 ? "" : "s")")
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(draft.jobs > 0 ? Theme.accent : .secondary)
                    .fixedSize()
                    .contentTransition(.numericText())
                    .animation(.snappy, value: draft.jobs)
            }
            // A count, not a control: without this iOS 26 wraps it in glass.
            .sharedBackgroundVisibility(.hidden)
            if store.isStale {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await store.refresh() } } label: { Image(systemName: "clock.arrow.circlepath") }
                        .tint(Theme.warning)
                        .accessibilityLabel("Stale — refresh")
                }
            }
            ToolbarItem(placement: .topBarTrailing) { moreMenu }
        }
        .safeAreaInset(edge: .bottom) {
            NewJobActionBar(store: store, composer: composer, draft: draft, state: state,
                            onContinue: { showRun = true })
        }
        .sheet(item: $pick) { target in
            PickerChainSheet(start: target, pipeline: pipeline, store: store, composer: composer,
                             materials: materials, onClose: { pick = nil })
        }
        .sheet(item: $openEntry) { entry in
            let index = (store.draft?.batch.firstIndex { $0.digest == entry.digest } ?? 0) + 1
            BatchEntryDetail(index: index, entry: entry, pipeline: pipeline(for: entry),
                             materials: materials, library: library, dropDisabled: locked,
                             onDrop: { await store.dropFromBatch(entry.digest) })
        }
        .navigationDestination(isPresented: $showRun) { RunFlowView(flow: flow, entry: .newJob) }
    }

    private func slotCard(_ card: NewJobState.Card, state: NewJobState, draft: Draft,
                          pipeline: Pipeline, size: CGSize) -> some View {
        let role = state.role(of: card)
        let items: [SlotCardItem]? = switch card {
        case .single: nil
        case .outfits: composer.outfits.map { SlotCardItem(id: $0.outfitID, seeded: $0.seedID != nil) }
        case .drivers: composer.drivers.map { SlotCardItem(id: $0, seeded: false) }
        }
        return SlotCard(
            role: role, required: state.required.contains(role), kind: pipeline.roles[role] ?? .unknown,
            // A Driver card with nothing multi-picked still shows the shared
            // driver slot, which is what one job with no driver list runs on.
            slot: draft.slots[role], items: (card == .drivers && composer.drivers.isEmpty) ? nil : items,
            materials: materials, disabled: locked,
            identifier: card == .outfits ? "batch.pickOutfits" : card == .drivers ? "batch.pickDrivers" : nil,
            size: size,
            onTap: { pick = PickTarget(card: card, chained: state.isFresh) },
            menu: { item in AnyView(cardMenu(card, role: role, item: item)) })
    }

    @ViewBuilder private func cardMenu(_ card: NewJobState.Card, role: String, item: SlotCardItem?) -> some View {
        switch card {
        case .single:
            Button("Clear", systemImage: "xmark.circle", role: .destructive) {
                Task { await store.assign(role: role, materialID: nil) }
            }
        case .outfits:
            if let item {
                let matches = composer.matches(for: item.id)
                let seed = composer.outfits.first { $0.outfitID == item.id }?.seedID
                Toggle("Use saved try-on", systemImage: "photo.badge.checkmark", isOn: Binding(
                    get: { seed != nil },
                    set: { composer.setSeed($0 ? matches.first?.id : nil, for: item.id) }))
                    .disabled(matches.isEmpty)
                    .accessibilityIdentifier("batch.seed.\(item.id)")
                if matches.count > 1, seed != nil {
                    Picker("Saved image", selection: Binding(
                        get: { seed ?? "" }, set: { composer.setSeed($0, for: item.id) })) {
                        ForEach(matches) { entry in
                            Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .shortened))")
                                .tag(entry.id)
                        }
                    }
                    .pickerStyle(.menu)
                }
                if matches.isEmpty { Text("No saved try-on for this pair — Phase A will make one.") }
                Divider()
                Button("Remove", systemImage: "trash", role: .destructive) { composer.toggle(outfitID: item.id) }
            }
        case .drivers:
            if let item {
                Button("Remove", systemImage: "trash", role: .destructive) { composer.toggle(driverID: item.id) }
            } else {
                Button("Clear", systemImage: "xmark.circle", role: .destructive) {
                    Task { await store.assign(role: role, materialID: nil) }
                }
            }
        }
    }

    @ViewBuilder private var banners: some View {
        VStack(spacing: 8) {
            if let error = store.error {
                ErrorBanner(error: error) { await store.refresh() }
            }
            if let message = store.message, message != store.error?.userMessage {
                MessageCard(text: message) { store.dismissMessage() }
            }
        }
        .animation(.snappy, value: store.message)
    }

    private func initialLoadFailure(_ error: APIError) -> some View {
        VStack(spacing: 16) {
            ContentUnavailableView("New Job unavailable", systemImage: "exclamationmark.triangle",
                                   description: Text("The draft and pipeline catalog could not be loaded."))
            ErrorBanner(error: error) { await store.load() }.heroSurface()
        }
        .padding(.horizontal, 16)
    }

    // MARK: Clear

    /// Where Clear was asked from. Each source carries its own dialog: on
    /// iOS 26 a confirmation dialog is a popover pointing at its anchor.
    private enum ClearSource { case menu, basket }

    private func confirmsClear(_ source: ClearSource) -> Binding<Bool> {
        Binding(get: { clearSource == source }, set: { if !$0 { clearSource = nil } })
    }

    private func clearDialog(_ source: ClearSource) -> some ViewModifier {
        ClearDraftDialog(isPresented: confirmsClear(source), message: clearMessage) {
            // The selection lives outside the draft since 2026-09-26, so the
            // server's clear alone would leave the Outfit and Driver cards full.
            composer.reset()
            Task { await store.clear() }
        }
    }

    private var clearMessage: String {
        let queued = store.draft?.batch.count ?? 0
        return queued == 0
            ? "Every picked material is removed."
            : "\(queued) job\(queued == 1 ? "" : "s") in the batch and every picked material are removed."
    }

    private var moreMenu: some View {
        Menu {
            Button("Clear draft", systemImage: "trash", role: .destructive) { clearSource = .menu }
                .disabled(locked)
        } label: {
            Image(systemName: "ellipsis")
        }
        .accessibilityLabel("More")
        .accessibilityIdentifier("newjob.more")
        .modifier(clearDialog(.menu))
    }

    private var clearAllButton: some View {
        Button("Clear all") { clearSource = .basket }
            .font(.subheadline)
            .buttonStyle(.borderless)
            .tint(Theme.danger)
            .disabled(locked)
            .accessibilityIdentifier("newjob.clearAll")
            .modifier(clearDialog(.basket))
    }

    private func pipeline(for entry: DraftBatchEntry) -> Pipeline? {
        store.catalog.first { $0.id == entry.pipeline }
    }
}

/// The one Clear confirmation, attached to whichever control asked for it.
private struct ClearDraftDialog: ViewModifier {
    @Binding var isPresented: Bool
    let message: String
    let onClear: () -> Void

    func body(content: Content) -> some View {
        content.confirmationDialog("Clear the draft?", isPresented: $isPresented, titleVisibility: .visible) {
            Button("Clear", role: .destructive, action: onClear)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(message)
        }
    }
}
```

`SlotMaterialRow`, `SlotTileGrid`, the seed badge section and `readiness(_:)` are gone. Search for their names (`grep -rn "SlotMaterialRow\|SlotTileGrid\|SlotTile\b\|SlotRow(" ios/MotionApp`) and remove any leftover reference.

- [ ] **Step 3: Build and run the unit tests**

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | grep -E "error" | head && make ios-test 2>&1 | tail -3`
Expected: no build errors; the MotionKit suite passes.

- [ ] **Step 4: Look at it**

Boot a simulator (iPhone 17 Pro), install and open New Job:
```bash
cd /Users/thucpham/Desktop/motion-clone && xcrun simctl boot "iPhone 17 Pro" 2>/dev/null; \
xcodebuild -project ios/MotionApp.xcodeproj -scheme MotionApp -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -quiet build && \
xcrun simctl install booted "$(find ~/Library/Developer/Xcode/DerivedData -path '*Debug-iphonesimulator/MotionApp.app' -maxdepth 6 | head -1)" && \
xcrun simctl launch booted "$(defaults read "$(pwd)/ios/MotionApp/Info.plist" CFBundleIdentifier 2>/dev/null || echo com.doanhthuc.motion)"
```
If the bundle id lookup fails, read `PRODUCT_BUNDLE_IDENTIFIER` from `ios/project.yml`. Check by eye: four cards and the action bar are on screen with no scroll, the chip opens the settings sheet, and tapping an empty card on a cleared draft walks the chain. Fix what is off before committing.

- [ ] **Step 5: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add -A ios/MotionApp && git commit -m "iOS New Job: one no-scroll stage; the Single | Batch mode is gone

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 9: Move the UI smokes to the new flow

**Files:**
- Modify: `ios/MotionAppUITests/Phase4SmokeTests.swift` (`Phase4Draft`)
- Modify: `ios/MotionAppUITests/Phase3SmokeTests.swift`
- Modify: `ios/MotionAppUITests/Phase6SmokeTests.swift`

These are live smokes against the VPS. They record spend attempts and never send them (`-UITestRecordingSpendGate`). No GPU is rented.

- [ ] **Step 1: Shared helpers in `Phase4Draft`**

Replace `composeValidatedTryonJob`, `clear`, and `chooseMaterial` with:

```swift
    /// Composes a one-job try-on draft. Continue (validate + navigate) is the
    /// caller's step since 2026-09-26, when Validate stopped being one.
    @MainActor static func composeTryonJob(in app: XCUIApplication) {
        clear(in: app)
        selectTryonPipeline(in: app)
        chooseMaterial(for: "Character", in: app)
        chooseMaterial(for: "Driver", in: app)
        chooseMaterial(for: "Outfit", in: app)
    }

    @MainActor static func clear(in app: XCUIApplication) {
        tapClear(in: app)
        XCTAssertTrue(waitUntil(timeout: 15) {
            (app.buttons["Character"].value as? String) == "Missing required"
        })
    }

    /// One material for `role`, from either picker: the single picker
    /// ("Choose material") or a try-on pipeline's multi-picker ("Choose
    /// outfits"/"Choose drivers"). A pick on a fresh draft chains to the next
    /// card, so a sheet still open afterwards is closed with Done.
    @MainActor static func chooseMaterial(for role: String, in app: XCUIApplication) {
        let card = app.buttons[role]
        XCTAssertTrue(card.waitForExistence(timeout: 10), "Missing card: \(role)")
        card.tap()
        let single = app.navigationBars["Choose material"]
        let multi = app.navigationBars.matching(NSPredicate(format: "identifier BEGINSWITH %@", "Choose ")).firstMatch
        XCTAssertTrue(multi.waitForExistence(timeout: 10), "No picker opened for \(role)")
        if single.exists {
            let choice = app.buttons.matching(NSPredicate(format: "value == %@", "Not selected")).firstMatch
            XCTAssertTrue(choice.waitForExistence(timeout: 10), "A compatible material must exist for \(role)")
            choice.tap()
        } else {
            let prefix = role == "Outfit" ? "outfit.pick." : "driver.pick."
            let tile = app.descendants(matching: .any)
                .matching(NSPredicate(format: "identifier BEGINSWITH %@", prefix)).firstMatch
            XCTAssertTrue(tile.waitForExistence(timeout: 10), "A compatible material must exist for \(role)")
            tile.tap()
        }
        let done = app.navigationBars.buttons["Done"].firstMatch
        if done.waitForExistence(timeout: 2) { done.tap() }
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons[role].value as? String) != "Missing required"
        })
    }
```

`revealButton` stays and still guards against the action bar. With no scroll, its swipes find the button on the first pass.

- [ ] **Step 2: Phase 4**

In `testRunFlowReachesRentPanelWithoutSpending`, replace `Phase4Draft.composeValidatedTryonJob(in: app)` and the `proceed` lines with:

```swift
        Phase4Draft.composeTryonJob(in: app)
        let proceed = app.buttons["newjob.continueToRun"]
        XCTAssertTrue(proceed.waitForExistence(timeout: 10))
        proceed.tap()      // validates, then opens the run flow
```

- [ ] **Step 3: Phase 3**

Rewrite `testDraftCompositionBatchDropAndValidation`'s body after `clearDraft(in: app)` as:

```swift
        selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Outfit", in: app)
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "DecodingError")).firstMatch.exists)

        // A pipeline without an outfit drops the selection instead of hiding it.
        selectPipeline(named: "Motion Enhance", in: app)
        XCTAssertFalse(app.buttons["Outfit"].exists)

        selectTryonPipeline(in: app)
        XCTAssertEqual(app.buttons["Outfit"].value as? String, "Missing required")
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        Phase4Draft.chooseMaterial(for: "Driver", in: app)
        Phase4Draft.chooseMaterial(for: "Outfit", in: app)

        clearMaterial(for: "Character", in: app)
        XCTAssertEqual(app.buttons["Character"].value as? String, "Missing required")
        XCTAssertFalse(app.buttons["newjob.continueToRun"].isEnabled)
        Phase4Draft.chooseMaterial(for: "Character", in: app)

        let add = app.buttons["batch.run"]
        XCTAssertTrue(add.waitForExistence(timeout: 10) && add.isEnabled)
        add.tap()
        XCTAssertTrue(Phase4Draft.revealText("Batch · 1", in: app, timeout: 15))

        app.buttons["newjob.basket"].tap()
        let firstDrop = app.buttons["Drop"].firstMatch
        XCTAssertTrue(firstDrop.waitForExistence(timeout: 5))
        firstDrop.tap()
        XCTAssertTrue(app.sheets["Drop this batch entry?"].waitForExistence(timeout: 5))
        app.sheets["Drop this batch entry?"].buttons["Drop"].tap()
        XCTAssertTrue(app.staticTexts["Batch · 1"].waitForNonExistence(timeout: 10))

        Phase4Draft.chooseMaterial(for: "Outfit", in: app)
        app.buttons["batch.run"].tap()
        XCTAssertTrue(Phase4Draft.revealText("Batch · 1", in: app, timeout: 15))
        app.buttons["newjob.continueToRun"].tap()
        XCTAssertTrue(app.buttons["runflow.rentWithoutPreview"].waitForExistence(timeout: 30))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(Phase4Draft.revealText("Ready", in: app, timeout: 10))
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@ AND label CONTAINS[c] %@", "about", "min")
        ).firstMatch.waitForExistence(timeout: 5))
```

Keep the "forbidden buttons" loop and the final `clearDraft(in: app)`. Replace the private `clearDraft` body with `Phase4Draft.clear(in: app)`. Delete the private `chooseMaterial`, whose peek coverage moves into `Phase4Draft` only if needed: keep the `peekFirst` branch by adding a `peekFirst: Bool = false` parameter to `Phase4Draft.chooseMaterial` and pasting that block into its single-picker arm, called for "Character". `clearMaterial` now uses the long-press menu:

```swift
    @MainActor
    private func clearMaterial(for role: String, in app: XCUIApplication) {
        let card = app.buttons[role]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.press(forDuration: 1.0)
        let clear = app.buttons["Clear"]
        XCTAssertTrue(clear.waitForExistence(timeout: 5))
        clear.tap()
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 10) {
            (app.buttons[role].value as? String) == "Missing required"
        })
    }
```

- [ ] **Step 4: Phase 6**

In both tests that tap `app.segmentedControls["newjob.mode"]` (around lines 90 and 199), delete the mode lines (`let mode = …`, its `waitForExistence`, `mode.buttons["Batch"].tap()`). `batch.pickOutfits` and `batch.pickDrivers` are now the card identifiers, so `Phase4Draft.revealButton("batch.pickOutfits", in: app).tap()` still opens the multi-picker, and `batch.run` is still the Add button. Before each "Drop" lookup, add `app.buttons["newjob.basket"].tap()` so the drawer is open. Leave the rest.

- [ ] **Step 5: Run the smokes**

Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-ui-test 2>&1 | tail -30`
Expected: exit 0. Phase 3, 4, 5 and 6 plus the other suites pass, and the spend gate records zero sends. If one fails, read its failure screenshot in the `.xcresult` (`xcrun xcresulttool`), fix the view or the helper, and rerun. Do not weaken an assertion to pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add ios/MotionAppUITests && git commit -m "iOS smokes: drive the single-stage New Job

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```

---

### Task 10: Prove no-scroll on small and large phones; hand off

**Files:**
- Modify: `docs/superpowers/swiftui-app-progress.md`

- [ ] **Step 1: Screenshots**

On an iPhone SE (3rd generation) simulator and an iPhone 17 Pro Max simulator, open New Job with the try-on pipeline, all four cards filled, three outfits, two drivers, and a basket of at least 3 jobs. Take the four screenshots below:
```bash
xcrun simctl io booted screenshot /tmp/newjob-<device>-<state>.png
```
States: `collapsed`, `drawer-open`, `error-banner` (put the phone offline with `xcrun simctl status_bar`, or trigger a refused PATCH), `chain` (a cleared draft with the chained picker open). Check that every card and the action bar are fully visible in `collapsed` and `error-banner`, and that the drawer's own list is the only thing that scrolls. Send the SE `collapsed` and `drawer-open` shots to the user.

- [ ] **Step 2: Install on the phone**

Follow `ios/README.md` §install to put the build on the user's phone. Ask the user to try: a build interrupted by airplane mode, whose Continue must stay disabled (Review Focus 2); swipe through outfits on the card, long-press menus, swipe to drop, the drawer drag, and the chain on a cleared draft.

- [ ] **Step 3: Handoff entry**

Add a section to `docs/superpowers/swiftui-app-progress.md` under the latest entry:

```markdown
## New Job single stage (2026-09-26, branch `newjob-single-stage`)

Spec: [`specs/2026-09-26-newjob-single-stage-design.md`](specs/2026-09-26-newjob-single-stage-design.md);
plan: [`plans/2026-09-26-newjob-single-stage.md`](plans/2026-09-26-newjob-single-stage.md).

- New Job is one stage that does not scroll: slot cards sized to the screen, a Pipeline · Provider
  chip, a basket drawer, Add / Continue. The Single | Batch switch is gone; Outfit and Driver take
  many on a try-on pipeline (`NewJobState`).
- Continue adds a pending composition, validates, and opens the run flow in one tap.
- The crossed roles live only in `BatchComposer`; `adoptDraftSelection()` moves a draft's outfit,
  seed and driver in (Saved try-ons "Use in job"), `reset()` empties it on Clear and on a
  non-try-on pipeline.
- Gates: `make ios-test`, `make ios-build`, `make ios-ui-test` (<result>), screenshots on SE and
  Pro Max (<paths>), installed on the phone (<date>, what the user said).
```

Fill in the placeholders with the actual results from Steps 1–2 and Task 9 before committing.

- [ ] **Step 4: Commit**

```bash
cd /Users/thucpham/Desktop/motion-clone && motions-studio/setup/scrub-secrets.sh --check >/dev/null && \
git add docs/superpowers/swiftui-app-progress.md && git commit -m "Progress: New Job single stage handoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011tFE8K5GRCvqbvoU9K8VHF"
```
