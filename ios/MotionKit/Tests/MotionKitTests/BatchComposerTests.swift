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
        private var basketNextPatchFor: String?
        private var dropBatchArmed = false
        private var characterOnNextRead: String?
        /// Immutable and set at init: every other field is read under `lock`
        /// from the stub handler, and a `var` written from a test body would be
        /// the one field raced outside it.
        private let library: String

        init(library: String = #"{"entries":[]}"#) { self.library = library }

        func failNextPatch(for outfit: String) { lock.withLock { failPatchFor = outfit } }
        func preload(outfit: String, seed: String?) {
            lock.withLock { batch.append((shared.merging(["outfit": outfit]) { $1 }, seed)) }
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
                case ("GET", "/v1/pipelines"): return TestSupport.json(Fixtures.pipelines)
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
                    if let slots = body["slots"] as? [String: Any], slots.keys.contains("outfit") {
                        let value = slots["outfit"] as? String
                        if let fail = failPatchFor, fail == value {
                            failPatchFor = nil
                            return TestSupport.json(#"{"error":{"code":"unprobeable","message":"could not read o2"}}"#, status: 422)
                        }
                        outfit = value
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

    private func writePaths() -> [String] { writes().map { "\($0.0) \($0.1)" } }

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
    /// selection edited afterwards must not inherit it, or Task 8 renders
    /// "1/3" beside four rows. One failed run per mutator, because the first
    /// edit already clears what the next two would have to prove.
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
