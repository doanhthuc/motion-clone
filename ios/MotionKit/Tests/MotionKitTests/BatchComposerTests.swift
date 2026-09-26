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
        private var failAnyPatch = false
        private var basketNextPatchFor: String?
        private var dropBatchArmed = false
        private var characterOnNextRead: String?
        /// Immutable and set at init: every other field is read under `lock`
        /// from the stub handler, and a `var` written from a test body would be
        /// the one field raced outside it.
        private let library: String
        /// The draft's pipeline and the catalog served beside it, fixed at init
        /// for the same reason as `library`.
        private let pipeline: String
        private let catalog: String
        private let provider: String

        init(library: String = #"{"entries":[]}"#, pipeline: String = "tryon-motion-enhance",
             provider: String = "gemini") {
            self.library = library
            self.pipeline = pipeline
            self.provider = provider
            self.catalog = pipeline == "tryon-motion-enhance" ? Fixtures.pipelines : BatchComposerTests.cameraCatalog
        }

        func failNextPatch(for outfit: String) { lock.withLock { failPatchFor = outfit } }
        func failNextPatch() { lock.withLock { failAnyPatch = true } }
        /// The edited job already names an outfit (and maybe a seed), the way
        /// Saved try-ons' "Use in job" leaves it.
        func preset(outfit: String, seed: String?) {
            lock.withLock { self.outfit = outfit; self.seed = seed }
        }
        func preload(outfit: String, seed: String?) {
            lock.withLock { batch.append((shared.merging(["outfit": outfit]) { $1 }, seed)) }
        }
        func preload(outfit: String, driver: String, seed: String?) {
            lock.withLock { batch.append((shared.merging(["outfit": outfit, "driver": driver]) { $1 }, seed)) }
        }
        /// Another surface baskets this outfit while we are patching it, so the
        /// add-to-batch that follows sees an exact copy — the only way a
        /// duplicate survives `run()`'s re-read of the draft.
        func basketNextPatch(for outfit: String) { lock.withLock { basketNextPatchFor = outfit } }
        /// Another surface emptied the basket after our last read.
        func dropBatchOnNextRead() { lock.withLock { dropBatchArmed = true } }
        /// Another surface changed a shared slot after our last read.
        func changeCharacterOnNextRead(to materialID: String) {
            lock.withLock { characterOnNextRead = materialID }
        }

        func answer(_ r: URLRequest) -> (Int, [String: String], Data) {
            lock.withLock {
                switch (r.httpMethod ?? "GET", r.url?.path ?? "") {
                case ("GET", "/v1/pipelines"): return TestSupport.json(catalog)
                case ("GET", "/v1/tryon-library"): return TestSupport.json(library)
                case ("GET", "/v1/draft"):
                    if dropBatchArmed {
                        dropBatchArmed = false
                        batch.removeAll()
                    }
                    if let character = characterOnNextRead {
                        characterOnNextRead = nil
                        shared["character"] = character
                    }
                    return TestSupport.json(json())
                case ("PATCH", "/v1/draft"):
                    let body = (try? JSONSerialization.jsonObject(with: r.httpBody ?? Data())) as? [String: Any] ?? [:]
                    if failAnyPatch {
                        failAnyPatch = false
                        return TestSupport.json(#"{"error":{"code":"unprobeable","message":"could not read"}}"#, status: 422)
                    }
                    if let slots = body["slots"] as? [String: Any], slots.keys.contains("outfit") {
                        let value = slots["outfit"] as? String
                        if let fail = failPatchFor, fail == value {
                            failPatchFor = nil
                            return TestSupport.json(#"{"error":{"code":"unprobeable","message":"could not read o2"}}"#, status: 422)
                        }
                        outfit = value
                    }
                    // A driver slot is shared state, like the character: a nil
                    // value empties it, exactly as drafts.py's PATCH does.
                    if let slots = body["slots"] as? [String: Any], slots.keys.contains("driver") {
                        shared["driver"] = slots["driver"] as? String
                    }
                    if body.keys.contains("tryon_seed") { seed = body["tryon_seed"] as? String }
                    if let racing = basketNextPatchFor, racing == outfit {
                        basketNextPatchFor = nil
                        batch.append((currentSlots(), seed))
                    }
                    return TestSupport.json(json())
                case ("POST", "/v1/draft/add-to-batch"):
                    let slots = currentSlots()
                    if batch.contains(where: { $0.slots == slots && $0.seed == seed }) {
                        return TestSupport.json(#"{"error":{"code":"duplicate","message":"that exact job is already in the batch"}}"#, status: 422)
                    }
                    batch.append((slots, seed))
                    return TestSupport.json(json())
                case ("POST", "/v1/draft/clear"):
                    shared = [:]
                    outfit = nil
                    seed = nil
                    batch.removeAll()
                    return TestSupport.json(json())
                default: return (404, [:], Data())
                }
            }
        }

        /// The edited job's slots — what a PATCH leaves behind, what
        /// add-to-batch copies, and what `_view` reports, all three the same.
        private func currentSlots() -> [String: String] {
            shared.merging(outfit.map { ["outfit": $0] } ?? [:]) { $1 }
        }

        private func json() -> String {
            let probe: [String: Any] = ["kind": "image", "width": 1, "height": 1, "duration_s": NSNull(),
                                        "bitrate_kbps": NSNull(), "size_bytes": 1, "warning": ""]
            let current = currentSlots()
            let slots = current.mapValues { id -> [String: Any] in
                ["material_id": id, "name": id, "exists": true, "probe": probe, "warning": ""] }
            let missing = ["character", "driver", "outfit"].filter { current[$0] == nil }
            let entries: [[String: Any]] = batch.enumerated().map { i, entry in
                ["digest": "d\(i)", "run_id": "run\(i)", "pipeline": pipeline, "provider": provider,
                 "slots": entry.slots, "tryon_seed": entry.seed ?? NSNull()] }
            let draft: [String: Any] = [
                "owner": "app", "pipeline": pipeline, "provider": provider, "generation": batch.count,
                "slots": slots, "required": ["character", "driver", "outfit"], "optional": ["background"],
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

    private func writePaths() -> [String] { writes().map { "\($0.0) \($0.1)" } }

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
        let slots = w.first?.2?["slots"] as? [String: Any]
        #expect(slots?["outfit"] is NSNull)
        #expect(slots?["driver"] is NSNull)
        #expect(w.first?.2?["tryon_seed"] is NSNull)
        #expect(draft.draft?.filledSlots["outfit"] == nil)
        #expect(draft.draft?.filledSlots["driver"] == nil)
        #expect(composer.canRun)
    }

    /// Review finding 4: Saved try-ons' "Use in job" while outfits are picked
    /// adds its outfit (and seed) to the selection instead of leaving it hidden
    /// on the draft. The hand-picked outfits and drivers stay as they were.
    @Test func adoptAppendsTheDraftsOutfitToAnExistingSelection() async {
        let server = FakeDraftServer()
        server.preset(outfit: "app/o1.png", seed: "s1")
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o9.png")
        composer.toggle(driverID: "app/d9.mp4")

        await composer.adoptDraftSelection()

        #expect(composer.outfits == [CrossOutfit(outfitID: "app/o9.png", seedID: nil),
                                     CrossOutfit(outfitID: "app/o1.png", seedID: "s1")])
        #expect(composer.drivers == ["app/d9.mp4"])
        #expect(draft.draft?.filledSlots["outfit"] == nil)
        let slots = writes().first?.2?["slots"] as? [String: Any]
        #expect(slots?.keys.sorted() == ["outfit"])
    }

    @Test func adoptOfAnOutfitAlreadyPickedOnlyClearsTheDraft() async {
        let server = FakeDraftServer()
        server.preset(outfit: "app/o9.png", seed: nil)
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o9.png")

        await composer.adoptDraftSelection()

        #expect(composer.outfits.map(\.outfitID) == ["app/o9.png"])
        #expect(writes().count == 1)
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
        let library = #"{"entries":[{"id":"s7","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":10}]}"#
        let server = FakeDraftServer(library: library)
        server.preset(outfit: "app/o1.png", seed: nil)
        let (composer, _) = await make(server)

        await composer.adoptDraftSelection()

        #expect(composer.outfits.first?.seedID == "s7")
    }

    /// A refused PATCH leaves the outfit and driver on the draft, so the
    /// composer must not hold them too: the draft would count its complete
    /// edited job and the cards would show the same outfit as a second job.
    @Test func adoptThatTheServerRefusesPutsTheSelectionBack() async {
        let server = FakeDraftServer()
        server.preset(outfit: "app/o1.png", seed: "s1")
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o9.png")
        server.failNextPatch()

        await composer.adoptDraftSelection()

        #expect(composer.outfits.map(\.outfitID) == ["app/o9.png"])
        #expect(composer.drivers.isEmpty)
        #expect(draft.draft?.filledSlots["outfit"] == "app/o1.png")
    }

    @Test func clearEmptiesTheDraftAndTheSelection() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(driverID: "app/d1.mp4")

        await composer.clear()

        #expect(composer.outfits.isEmpty && composer.drivers.isEmpty)
        #expect(draft.draft?.filledSlots.isEmpty == true)
    }

    /// A clear that did not land leaves the server's draft as it was, so the
    /// picks on screen stay too rather than vanishing from one half only.
    @Test func clearThatFailsKeepsTheSelection() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(driverID: "app/d1.mp4")
        StubURLProtocol.install { r in
            r.url?.path == "/v1/draft/clear"
                ? TestSupport.json(#"{"error":{"code":"busy","message":"busy"}}"#, status: 409)
                : server.answer(r)
        }

        await composer.clear()

        #expect(composer.outfits.map(\.outfitID) == ["app/o1.png"])
        #expect(composer.drivers == ["app/d1.mp4"])
    }

    /// Review finding 2: leaving a try-on pipeline for one without an outfit
    /// hands a single picked driver back to the draft's driver slot, so a
    /// look at the try-on pipeline does not cost the user their driver.
    @Test func releasingToAPipelineWithADriverHandsASingleDriverBack() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(driverID: "app/d1.mp4")

        await composer.release(keepingDriver: true)

        #expect(composer.outfits.isEmpty && composer.drivers.isEmpty)
        let w = writes()
        #expect(w.map { "\($0.0) \($0.1)" } == ["PATCH /v1/draft"])
        #expect((w.first?.2?["slots"] as? [String: Any])?["driver"] as? String == "app/d1.mp4")
    }

    @Test func releasingWithSeveralDriversOrNoDriverRoleWritesNothing() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        composer.toggle(driverID: "app/d1.mp4")
        composer.toggle(driverID: "app/d2.mp4")
        await composer.release(keepingDriver: true)
        #expect(composer.drivers.isEmpty)
        composer.toggle(driverID: "app/d1.mp4")
        await composer.release(keepingDriver: false)
        #expect(composer.drivers.isEmpty)
        #expect(writes().isEmpty)
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

    @Test func threeOutfitsAreThreePatchAddPairsThenTheOutfitIsCleared() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        // An empty selection has nothing to build, however ready the draft is.
        #expect(!composer.canRun)
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
        #expect(composer.progress == nil)
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
        // A stopped run must not leave the composer locked, or Continue — the
        // whole point of keeping the selection — could never be tapped.
        #expect(composer.isRunning == false)
        #expect(composer.canRun)

        await composer.run()
        let adds = writes().filter { $0.1 == "/v1/draft/add-to-batch" }
        #expect(adds.count == 3)            // o1 once, then o2 and o3 — never o1 again
        #expect(draft.draft?.batch.count == 3)
        #expect(composer.failure == nil)
    }

    /// A stopped run keeps its k/N — spec §4 wants it at the stop — but the
    /// selection edited afterwards must not inherit it, or `BatchComposerSection`
    /// renders "1/3" beside four rows. One failed run per mutator, because the
    /// first edit already clears what the next two would have to prove.
    @Test func editingTheSelectionClearsAStoppedRunsProgress() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        for o in ["app/o1.png", "app/o2.png", "app/o3.png"] { composer.toggle(outfitID: o) }

        server.failNextPatch(for: "app/o1.png")
        await composer.run()
        #expect(composer.progress == .init(done: 0, total: 3))
        composer.setSeed(nil, for: "app/o1.png")
        #expect(composer.progress == nil)

        server.failNextPatch(for: "app/o1.png")
        await composer.run()
        #expect(composer.progress == .init(done: 0, total: 3))
        composer.refreshSeeds()
        #expect(composer.progress == nil)

        server.failNextPatch(for: "app/o1.png")
        await composer.run()
        #expect(composer.progress == .init(done: 0, total: 3))
        composer.toggle(outfitID: "app/o4.png")
        #expect(composer.progress == nil)
        #expect(composer.outfits.count == 4)
    }

    @Test func aDuplicateCountsAsDone() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        // Added elsewhere after our last read *and* after run() re-reads the
        // draft: preloading before the run would be planned away by
        // pending(in:) and the 422 would never be reached.
        server.basketNextPatch(for: "app/o1.png")

        await composer.run()

        // The refused add is still one PATCH/POST pair, then the seedless clear.
        #expect(writePaths() == ["PATCH /v1/draft", "POST /v1/draft/add-to-batch", "PATCH /v1/draft"])
        #expect(draft.draft?.batch.count == 1)
        #expect(composer.failure == nil)
        #expect(composer.lastAdded == 1)
        #expect(draft.error == nil)
    }

    /// `lastAdded` is rendered as "Added N jobs to the batch." on the screen
    /// where the user decides how much GPU to rent, so it must be what THIS run
    /// added — the steps it executed — not the size of the selection it started
    /// from. A Continue re-plans from the server's draft and `pending(in:)`
    /// skips the outfits already basketed; the selection size would report 3
    /// here for one job added.
    @Test func lastAddedCountsTheStepsThisRunExecutedNotTheSelection() async {
        let server = FakeDraftServer()
        server.preload(outfit: "app/o1.png", seed: nil)   // basketed before we ever read
        server.preload(outfit: "app/o2.png", seed: nil)
        let (composer, draft) = await make(server)
        #expect(draft.draft?.batch.count == 2)
        for o in ["app/o1.png", "app/o2.png", "app/o3.png"] { composer.toggle(outfitID: o) }
        #expect(composer.outfits.count == 3)

        await composer.run()

        #expect(composer.failure == nil)
        #expect(draft.draft?.batch.count == 3)             // only o3 was added
        #expect(writePaths() == ["PATCH /v1/draft", "POST /v1/draft/add-to-batch", "PATCH /v1/draft"])
        #expect(composer.lastAdded == 1)
    }

    /// The unsafe direction of the spec §4 re-read: an entry dropped elsewhere
    /// between a failed run and Continue. Planned from the cached draft it would
    /// be skipped and the run would report success having added nothing.
    @Test func runReReadsTheDraftSoADropElsewhereIsAddedAgain() async {
        let server = FakeDraftServer()
        server.preload(outfit: "app/o1.png", seed: nil)   // already basketed when we first read
        let (composer, draft) = await make(server)
        #expect(draft.draft?.batch.count == 1)
        composer.toggle(outfitID: "app/o1.png")           // so pending(in:) would skip it
        server.dropBatchOnNextRead()                      // …until someone drops it server-side

        await composer.run()

        #expect(writePaths() == ["PATCH /v1/draft", "POST /v1/draft/add-to-batch", "PATCH /v1/draft"])
        #expect(draft.draft?.batch.count == 1)
        #expect(composer.failure == nil && composer.lastAdded == 1)
    }

    /// The other unsafe direction of the spec §4 re-read: a shared slot changed
    /// elsewhere *while the run is in flight*. `refreshSeeds()` cannot correct
    /// the seeds then — it refuses on `isRunning` — so the re-read is the only
    /// moment they can be re-picked against the slots the PATCH is sent with.
    /// The stale seed names a try-on saved from the old character, and Phase A
    /// skips the provider and seeds the job from it.
    @Test func runRepicksSeedsAgainstTheSharedSlotsItReRead() async {
        let server = FakeDraftServer(library: #"""
        {"entries":[{"id":"stale","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1}]}
        """#)
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        // Pinned so the test cannot pass by never having had a match to lose.
        #expect(composer.outfits.map(\.seedID) == ["stale"])
        server.changeCharacterOnNextRead(to: "app/someone-else.png")

        await composer.run()

        #expect(draft.draft?.filledSlots["character"] == "app/someone-else.png")
        #expect(writePaths() == ["PATCH /v1/draft", "POST /v1/draft/add-to-batch", "PATCH /v1/draft"])
        let w = writes()
        // Nothing in the library matches the new character, so the seed is
        // cleared and Phase A makes a fresh try-on. `"stale"` here is the defect.
        #expect(w[0].2?["tryon_seed"] is NSNull)
        #expect(composer.failure == nil && composer.lastAdded == 1)
    }

    @Test func seedsDefaultToTheNewestMatchAndCapAtTwelve() async {
        let server = FakeDraftServer(library: #"""
        {"entries":[{"id":"old","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1},
                    {"id":"new","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":2}]}
        """#)
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        #expect(composer.outfits.map(\.seedID) == ["new", nil])
        #expect(composer.matches(for: "app/o1.png").map(\.id) == ["new", "old"])
        for i in 3...20 { composer.toggle(outfitID: "app/x\(i).png") }
        #expect(composer.outfits.count == BatchComposer.maxJobs)
        composer.toggle(outfitID: "app/o1.png")
        #expect(!composer.outfits.contains { $0.outfitID == "app/o1.png" })
    }

    @Test func onlyCharacterAndOutfitPipelinesQualify() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let catalog = try decoder.decode(PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines))
        #expect(catalog.pipelines.filter(BatchComposer.supports).map(\.id) == ["tryon-motion-enhance"])
    }
    /// `Fixtures.pipelines` plus the camera-aware pipeline, whose try-on stage
    /// (`camera-tryon`) is guided by the driver and so cannot be shared across
    /// drivers (`scripts/batchlib/pipelines.py:77-85,155`). Kept out of
    /// `Fixtures.pipelines` so the catalog-wide assertions elsewhere stay put.
    nonisolated static let cameraCatalog = #"""
    {"pipelines":[
      {"id":"tryon-motion-enhance","stages":["tryon","motion","enhance"],
       "required":["character","driver","outfit"],"optional":["background"],
       "roles":{"character":"image","driver":"video","outfit":"image","background":"image"},
       "providers":[{"id":"gemini","label":"Gemini"}]},
      {"id":"tryon-camera-motion-enhance","stages":["camera-tryon","camera-motion","enhance"],
       "required":["character","driver","outfit"],"optional":["background"],
       "roles":{"character":"image","driver":"video","outfit":"image","background":"image"},
       "providers":[{"id":"gemini","label":"Gemini"}]}
    ]}
    """#

    private func slots(_ w: (String, String, [String: Any]?)) -> [String: Any]? { w.2?["slots"] as? [String: Any] }

    @Test func twoOutfitsTwoDriversAreFourPatchAddPairsThenBothSlotsAreCleared() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        composer.toggle(driverID: "app/d1.mp4")
        composer.toggle(driverID: "app/d2.mp4")
        #expect(composer.jobCount == 4)
        #expect(composer.canRun)

        await composer.run()

        let w = writes()
        #expect(w.map { "\($0.0) \($0.1)" } == Array(repeating: ["PATCH /v1/draft", "POST /v1/draft/add-to-batch"], count: 4).flatMap { $0 }
                + ["PATCH /v1/draft"])
        // Outfit-major: every driver of o1 before o2.
        let pairs = stride(from: 0, to: 8, by: 2).map { i in
            "\(slots(w[i])?["outfit"] as? String ?? "?")+\(slots(w[i])?["driver"] as? String ?? "?")" }
        #expect(pairs == ["app/o1.png+app/d1.mp4", "app/o1.png+app/d2.mp4",
                          "app/o2.png+app/d1.mp4", "app/o2.png+app/d2.mp4"])
        // Every step still names its seed.
        #expect((0..<4).allSatisfy { w[$0 * 2].2?.keys.contains("tryon_seed") == true })
        // The final PATCH clears both multi-selected slots and leaves the seed alone.
        #expect(slots(w[8])?["outfit"] is NSNull)
        #expect(slots(w[8])?["driver"] is NSNull)
        #expect(w[8].2?.keys.contains("tryon_seed") == false)
        #expect(draft.draft?.batch.count == 4)
        #expect(composer.failure == nil && composer.lastAdded == 4)
        #expect(composer.outfits.isEmpty && composer.progress == nil)
        // Drivers stay picked, like the character: the next outfits are
        // usually for the same moves (2026-09-26, New Job single stage).
        #expect(composer.drivers == ["app/d1.mp4", "app/d2.mp4"])
        #expect(!composer.canRun)   // nothing left to add until outfits are picked again
    }

    @Test func noDriversSelectedKeepsTodaysBehaviour() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        for o in ["app/o1.png", "app/o2.png", "app/o3.png"] { composer.toggle(outfitID: o) }
        #expect(composer.sharedSlots["driver"] == "app/dance.mp4")
        #expect(composer.jobCount == 3)

        await composer.run()

        let w = writes()
        #expect(w.map { "\($0.0) \($0.1)" } == [
            "PATCH /v1/draft", "POST /v1/draft/add-to-batch",
            "PATCH /v1/draft", "POST /v1/draft/add-to-batch",
            "PATCH /v1/draft", "POST /v1/draft/add-to-batch",
            "PATCH /v1/draft"])
        // No step and no clear touches the driver: it is a shared slot here.
        #expect(w.allSatisfy { slots($0)?.keys.contains("driver") != true })
        #expect(slots(w[6])?.keys.sorted() == ["outfit"])
        #expect(draft.draft?.filledSlots["driver"] == "app/dance.mp4")
        #expect(composer.lastAdded == 3)
    }

    @Test func continueSkipsPairsAlreadyInTheBasket() async {
        let server = FakeDraftServer()
        server.preload(outfit: "app/o1.png", driver: "app/d1.mp4", seed: nil)
        let (composer, draft) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        composer.toggle(driverID: "app/d1.mp4")
        composer.toggle(driverID: "app/d2.mp4")

        await composer.run()

        let adds = writes().filter { $0.1 == "/v1/draft/add-to-batch" }
        #expect(adds.count == 3)
        #expect(draft.draft?.batch.count == 4)
        #expect(composer.failure == nil && composer.lastAdded == 3)
    }

    @Test func aStoppedPairNamesBothMaterialsAndCountsJobs() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        composer.toggle(driverID: "app/d1.mp4")
        composer.toggle(driverID: "app/d2.mp4")
        server.failNextPatch(for: "app/o2.png")

        await composer.run()

        #expect(composer.progress == .init(done: 2, total: 4))
        #expect(composer.failure?.contains("app/o2.png") == true)
        #expect(composer.failure?.contains("app/d1.mp4") == true)
        #expect(composer.drivers.count == 2)
    }

    @Test func aDriverThatWouldExceedTwelveJobsIsRefusedWithAReason() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        for i in 1...6 { composer.toggle(outfitID: "app/o\(i).png") }
        composer.toggle(driverID: "app/d1.mp4")
        composer.toggle(driverID: "app/d2.mp4")
        #expect(composer.jobCount == 12)
        #expect(composer.capReason == nil)

        composer.toggle(driverID: "app/d3.mp4")
        #expect(composer.drivers.count == 2)
        #expect(composer.capReason != nil)

        composer.toggle(outfitID: "app/o5.png")
        composer.toggle(outfitID: "app/o6.png")
        composer.toggle(driverID: "app/d3.mp4")
        #expect(composer.drivers == ["app/d1.mp4", "app/d2.mp4", "app/d3.mp4"])
        #expect(composer.jobCount == 12)
        #expect(composer.capReason == nil)
    }

    @Test func anOutfitThatWouldExceedTwelveJobsIsRefused() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        for i in 1...3 { composer.toggle(driverID: "app/d\(i).mp4") }
        for i in 1...4 { composer.toggle(outfitID: "app/o\(i).png") }
        #expect(composer.jobCount == 12)

        composer.toggle(outfitID: "app/o5.png")
        #expect(composer.outfits.count == 4)
        #expect(composer.capReason != nil)

        composer.toggle(driverID: "app/d3.mp4")          // a removal is always allowed
        #expect(composer.capReason == nil)
        composer.toggle(outfitID: "app/o5.png")          // 5 × 2 = 10
        #expect(composer.outfits.count == 5)
        #expect(composer.capReason == nil)
    }

    @Test func seedsAreSharedAcrossDriversOfOneOutfit() async {
        let server = FakeDraftServer(library: #"""
        {"entries":[{"id":"s1","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1}]}
        """#)
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        composer.toggle(outfitID: "app/o2.png")
        composer.toggle(driverID: "app/d1.mp4")
        composer.toggle(driverID: "app/d2.mp4")
        #expect(composer.outfits.map(\.seedID) == ["s1", nil])

        await composer.run()

        let patches = writes().filter { $0.0 == "PATCH" && slots($0)?["driver"] is String }
        let o1 = patches.filter { slots($0)?["outfit"] as? String == "app/o1.png" }
        let o2 = patches.filter { slots($0)?["outfit"] as? String == "app/o2.png" }
        #expect(o1.count == 2 && o2.count == 2)
        #expect(o1.allSatisfy { $0.2?["tryon_seed"] as? String == "s1" })
        #expect(o2.allSatisfy { $0.2?["tryon_seed"] is NSNull })
    }

    @Test func tryonCountIsOnePerUnseededOutfit() async {
        let library = #"""
        {"entries":[{"id":"s1","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1}]}
        """#
        for (pipeline, cameraAware, tryons) in [("tryon-motion-enhance", false, 2), ("tryon-camera-motion-enhance", true, 4)] {
            let server = FakeDraftServer(library: library, pipeline: pipeline)
            let (composer, draft) = await make(server)
            #expect(draft.selectedPipeline?.id == pipeline)
            for i in 1...3 { composer.toggle(outfitID: "app/o\(i).png") }
            composer.toggle(driverID: "app/d1.mp4")
            composer.toggle(driverID: "app/d2.mp4")
            #expect(composer.outfits.compactMap(\.seedID) == ["s1"])
            #expect(composer.cameraAwareTryon == cameraAware)
            #expect(composer.jobCount == 6)
            #expect(composer.tryonCount == tryons)
        }
    }

    /// Sharing happens only in Phase A, which runs only for the local
    /// providers; a pod provider such as `qwen` does its own try-on inside
    /// every job, so the summary must count one per job, seeded or not.
    @Test func tryonCountIsOnePerJobForANonLocalProvider() async {
        let library = #"""
        {"entries":[{"id":"s1","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1}]}
        """#
        for (provider, tryons) in [("qwen", 6), ("qwen-max", 2)] {
            let server = FakeDraftServer(library: library, provider: provider)
            let (composer, draft) = await make(server)
            #expect(draft.draft?.provider == provider)
            for i in 1...3 { composer.toggle(outfitID: "app/o\(i).png") }
            composer.toggle(driverID: "app/d1.mp4")
            composer.toggle(driverID: "app/d2.mp4")
            #expect(composer.jobCount == 6)
            #expect(composer.tryonCount == tryons)
        }
        let server = FakeDraftServer(library: library, provider: "qwen")
        let (composer, _) = await make(server)
        for i in 1...3 { composer.toggle(outfitID: "app/o\(i).png") }
        #expect(composer.tryonCount == composer.jobCount)
        #expect(composer.tryonCount == 3)
    }

    @Test func missingSharedExcludesTheDriverOnlyWhenDriversAreSelected() async {
        let server = FakeDraftServer()
        let (composer, draft) = await make(server)
        #expect(await draft.apply(DraftPatch(slots: ["driver": nil])))
        #expect(composer.missingShared == ["driver"])
        composer.toggle(outfitID: "app/o1.png")
        #expect(!composer.canRun)

        composer.toggle(driverID: "app/d1.mp4")
        #expect(composer.missingShared.isEmpty)
        #expect(composer.sharedSlots["driver"] == nil)
        #expect(composer.canRun)
    }

    @Test func onlyPipelinesWithADriverRoleTakeDrivers() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let catalog = try decoder.decode(PipelineCatalogResponse.self, from: Fixtures.data(Self.cameraCatalog))
        #expect(catalog.pipelines.allSatisfy(BatchComposer.supportsDrivers))
        let fixture = try decoder.decode(PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines))
        #expect(fixture.pipelines.filter(BatchComposer.supportsDrivers).map(\.id) == ["motion-enhance", "tryon-motion-enhance"])
    }
    /// `NewJobView` re-picks every seed when `seedKey` changes. Toggling the
    /// first driver on (or the last off) moves `driver` in or out of
    /// `sharedSlots`, but the library ignores the driver, so the key — and a
    /// seed chosen by hand, an explicit nil included — must not move.
    @Test func togglingDriversLeavesTheSeedKeyAndManualSeedsAlone() async {
        let server = FakeDraftServer(library: #"""
        {"entries":[{"id":"s1","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":1}]}
        """#)
        let (composer, _) = await make(server)
        composer.toggle(outfitID: "app/o1.png")
        #expect(composer.outfits.map(\.seedID) == ["s1"])
        composer.setSeed(nil, for: "app/o1.png")
        let key = composer.seedKey
        #expect(key == ["character": "app/me.png"])

        composer.toggle(driverID: "app/d1.mp4")
        #expect(composer.seedKey == key)
        #expect(composer.outfits.map(\.seedID) == [nil])

        composer.toggle(driverID: "app/d1.mp4")
        #expect(composer.seedKey == key)
        #expect(composer.outfits.map(\.seedID) == [nil])
    }

    @Test func aThirteenthDriverIsRefusedWithNoOutfits() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        for i in 1...12 { composer.toggle(driverID: "app/d\(i).mp4") }
        #expect(composer.drivers.count == 12)
        #expect(composer.capReason == nil)

        composer.toggle(driverID: "app/d13.mp4")
        #expect(composer.drivers.count == 12)
        #expect(composer.capReason == "At most 12 videos per batch — 1 outfit × 12 drivers is already 12.")
        // One outfit still fits: 1 × 12.
        composer.toggle(outfitID: "app/o1.png")
        #expect(composer.outfits.count == 1 && composer.capReason == nil)
    }

    @Test func theCapReasonNeverCountsZeroDrivers() async {
        let server = FakeDraftServer()
        let (composer, _) = await make(server)
        for i in 1...13 { composer.toggle(outfitID: "app/o\(i).png") }
        #expect(composer.outfits.count == 12)
        #expect(composer.capReason == "At most 12 videos per batch — 12 outfits × 1 driver is already 12.")
    }
}
}
