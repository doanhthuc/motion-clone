import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct RunFlowTests {
    /// Routes reads by path. `tryon` and `panel` can be swapped mid-test.
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _pod = Fixtures.podIdle
        private var _tryon = Fixtures.tryonIdle
        private var _panels = [Fixtures.rentPanel]
        private var _draft = Fixtures.draft
        private var _validate = Routes.validation(valid: true, stale: false)
        private var _validateStatus = 200
        var pod: String { get { lock.withLock { _pod } } set { lock.withLock { _pod = newValue } } }
        var tryon: String { get { lock.withLock { _tryon } } set { lock.withLock { _tryon = newValue } } }
        var draft: String { get { lock.withLock { _draft } } set { lock.withLock { _draft = newValue } } }
        var validate: String { get { lock.withLock { _validate } } set { lock.withLock { _validate = newValue } } }
        var validateStatus: Int { get { lock.withLock { _validateStatus } } set { lock.withLock { _validateStatus = newValue } } }
        func setPanels(_ p: [String]) { lock.withLock { _panels = p } }
        func nextPanel() -> String { lock.withLock { _panels.count > 1 ? _panels.removeFirst() : _panels[0] } }

        static func quoted(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        static func draftJSON(batch: [String], outfit: String, seed: String?, generation: Int = 9) -> String {
            let probe = #"{"kind":"image","width":1,"height":1,"duration_s":null,"bitrate_kbps":null,"size_bytes":1,"warning":""}"#
            let slots = ["character": "app/model.png", "driver": "app/dance.mp4", "outfit": outfit]
                .sorted { $0.key < $1.key }
                .map { "\"\($0.key)\":{\"material_id\":\"\($0.value)\",\"name\":\"x\",\"exists\":true,\"probe\":\(probe),\"warning\":\"\"}" }
                .joined(separator: ",")
            return #"{"owner":"app","pipeline":"tryon-motion-enhance","provider":"gemini","generation":"#
                + String(generation) + #","slots":{"#
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
        /// Generation 10, not 9: `_changed()` bumps it on every draft mutation
        /// (drafts.py:275-279), so the drop really does move it. `RunFlow`'s
        /// retry latch reads the field, and a fixture that never moved it would
        /// make that latch untestable through `drop`.
        static let draftAfterDrop = draftJSON(
            batch: [entry("d1", "model__dress", "app/dress.png")], outfit: "app/blazer.png", seed: "s9",
            generation: 10)
        /// The DELETE's answer. `_changed()` resets `validated` to null and
        /// bumps `generation` in the same write (drafts.py:275-279), so this is
        /// the post-drop generation with no verdict on it; the validate that
        /// follows sets `validated` again without moving `generation`
        /// (drafts.py:552-556, "a verdict is not a change"). The null is what
        /// makes `drop`'s `draft = validation.draft` observable instead of a
        /// no-op.
        static let draftAfterDelete = draftAfterDrop.replacingOccurrences(
            of: #"validated":true"#, with: #"validated":null"#)

        /// `POST /v1/draft/validate`. The draft in the verdict is the post-drop
        /// one, so a drop that reaches this route always sees its entry gone.
        /// `d.validated = ok` only runs when the verdict is not stale
        /// (drafts.py:552-556), so a stale verdict still carries the null the
        /// draft change left behind. A stale verdict can carry `valid: false`
        /// (drafts.py:561); a non-stale invalid one never arrives here at all —
        /// the server raises `DraftError("invalid")` (drafts.py:566), which
        /// `_DOMAIN_STATUS` maps to 422, so use `validateStatus` for that case.
        static func validation(valid: Bool, stale: Bool) -> String {
            let body = (valid && !stale) ? draftAfterDrop : draftAfterDelete
            return #"{"valid":"# + (valid ? "true" : "false") + #","stale":"# + (stale ? "true" : "false")
                + #","output":null,"draft":"# + body + "}"
        }
        static func probeJSON(_ kind: String) -> String {
            #"{"kind":"\#(kind)","width":1,"height":1,"duration_s":null,"bitrate_kbps":null,"size_bytes":1,"warning":""}"#
        }
        /// `motion-enhance` has no `outfit` role, so it is the only pipeline in
        /// `Fixtures.pipelines` that reaches `clearRole`'s fallback.
        static func motionDraft(batch: [String], driver: String) -> String {
            let kinds = ["character": "image", "driver": "video"]
            let slots = ["character": "app/model.png", "driver": driver]
                .sorted { $0.key < $1.key }
                .map { "\"\($0.key)\":{\"material_id\":\"\($0.value)\",\"name\":\"x\",\"exists\":true,\"probe\":\(probeJSON(kinds[$0.key] ?? "image")),\"warning\":\"\"}" }
                .joined(separator: ",")
            return #"{"owner":"app","pipeline":"motion-enhance","provider":"gemini","generation":9,"slots":{"#
                + slots + #"},"required":["character","driver"],"optional":[],"missing":[],"validated":true,"batch":["#
                + batch.joined(separator: ",") + #"],"jobs":2,"estimate_min":40,"tryon_seed":null}"#
        }
        static func motionEntry(_ digest: String, _ run: String, _ driver: String) -> String {
            #"{"digest":"\#(digest)","run_id":"\#(run)","pipeline":"motion-enhance","provider":"gemini","slots":{"character":"app/model.png","driver":"\#(driver)"},"tryon_seed":null}"#
        }

        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            let path = request.url?.path ?? ""
            switch true {
            case path == "/v1/pod": return TestSupport.json(pod)
            case path == "/v1/pipelines": return TestSupport.json(Fixtures.pipelines)
            case path == "/v1/draft": return TestSupport.json(draft)
            case path.hasPrefix("/v1/draft/batch/"): return TestSupport.json(Self.draftAfterDelete)
            case path == "/v1/draft/validate": return TestSupport.json(validate, status: validateStatus)
            case path.hasSuffix("/rent-panel"): return TestSupport.json(nextPanel())
            case path.hasSuffix("/tryon"): return TestSupport.json(tryon)
            case path == "/v1/tryon-library": return TestSupport.json(Fixtures.keepRecord)
            case path.contains("/versions/"):
                let n = Int(path.split(separator: "/").last ?? "") ?? 0
                return n <= 2 ? (200, ["Content-Type": "image/png"], Data("v\(n)".utf8))
                              : TestSupport.json(#"{"error":{"code":"not_found","message":"no"}}"#, status: 404)
            case path.contains("/tryon/"): return (200, ["Content-Type": "image/png"], Data("img".utf8))
            default: return (404, [:], Data())
            }
        }
    }

    private func make(_ routes: Routes, gate: any SpendSending = FakeSpendGate()) -> RunFlow {
        StubURLProtocol.install { routes.answer($0) }
        return RunFlow(client: TestSupport.client(), gate: gate, sleep: { _ in })
    }

    @Test func newJobEntryLoadsAndComposes() async {
        let flow = make(Routes())
        await flow.start(.newJob)
        #expect(flow.phase == .compose)
        #expect(flow.runID == "tg-1000")
        #expect(flow.hasLocalTryon)            // draft batch entry: tryon-motion-enhance + gemini
    }

    @Test func motionOnlyDraftHasNoLocalTryon() async {
        let routes = Routes()
        routes.draft = #"""
        {"owner":"app","pipeline":"motion-enhance","provider":"gemini","generation":1,"slots":{},
         "required":["character","driver"],"optional":[],"missing":[],"validated":true,
         "batch":[],"jobs":1,"estimate_min":40}
        """#
        let flow = make(routes)
        await flow.start(.newJob)
        #expect(!flow.hasLocalTryon)
    }

    @Test func existingEntryWithPreviewsShowsPreviews() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        #expect(flow.phase == .previews)
    }

    @Test func runningPhaseAWinsOverEntry() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonRunning
        let flow = make(routes)
        await flow.start(.newJob)
        #expect(flow.phase == .phaseARunning)
        #expect(flow.needsTryonPolling)
    }

    @Test func startPhaseASendsOneIntentAndPolls() async {
        let routes = Routes()
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        routes.tryon = Fixtures.tryonRunning
        await flow.startPhaseA()
        #expect(await gate.intents == [.phaseA])
        #expect(flow.phase == .phaseARunning)
        #expect(flow.inFlightLabel == nil)
    }

    @Test func finishingPhaseAMovesToPreviewsAndBumpsGeneration() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonRunning
        let flow = make(routes)
        await flow.start(.newJob)
        let before = flow.imageGeneration
        routes.tryon = Fixtures.tryonDone
        await flow.refreshTryon()
        #expect(flow.phase == .previews)
        #expect(flow.imageGeneration == before + 1)
    }

    @Test func regenerateSendsOrderedGuidanceAndCurrentToken() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        routes.tryon = Fixtures.tryonRunning
        await flow.regenerate(index: "0", guidance: [.matchLighting, .keepFace])
        #expect(await gate.intents == [.regen(runID: "tg-1000", index: "0", runToken: "1790000000123.4",
                                              guidance: [.keepFace, .matchLighting])])
        #expect(flow.phase == .phaseARunning)
    }

    @Test func acceptedSpendAlreadyFinishedLandsInPreviews() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        let before = flow.imageGeneration
        await flow.regenerate(index: "0", guidance: [])
        #expect(flow.phase == .previews)
        #expect(flow.imageGeneration == before + 1)
    }

    @Test func regenerateStaleRefreshesTryonAndShowsServerText() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_panel",
                                           message: "the run changed — read it again", panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        await flow.regenerate(index: "0", guidance: [])
        #expect(flow.message == "the run changed — read it again")
        #expect(flow.phase == .previews)
        #expect(await gate.intents.count == 1)
    }

    @Test func keepPostsAndMarksTheCurrentImage() async throws {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        await flow.keep(index: "0")
        #expect(flow.isKept("0"))
        let post = try #require(StubURLProtocol.requests.last { $0.url?.path == "/v1/tryon-library" })
        let body = try #require(JSONSerialization.jsonObject(with: post.httpBody ?? Data()) as? [String: String])
        #expect(body == ["run_id": "tg-1000", "index": "0"])
    }

    @Test func versionsStopAtTheFirst404() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        await flow.loadVersions(index: "0")
        #expect(flow.versions["0"] == [Data("v1".utf8), Data("v2".utf8)])
    }

    @Test func reenteringStartInvalidatesImageCache() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        let before = flow.imageGeneration
        _ = await flow.image(index: "0")
        await flow.start(.existing)
        #expect(flow.imageGeneration == before + 1)
        _ = await flow.image(index: "0")
        let hits = StubURLProtocol.requests.filter { $0.url?.path == "/v1/runs/tg-1000/tryon/0" }
        #expect(hits.count == 2)
    }

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
        #expect(writes[0].timeoutInterval == 95)   // the PATCH's probe can take 60 s
        #expect(writes[2].timeoutInterval == 95)
        #expect(flow.draft?.batch.map(\.digest) == ["d1"])
        // The verdict's draft, not the DELETE's: a draft change resets
        // `validated` to null (drafts.py:276) and only the validate that
        // follows sets it again, so this pins `draft = validation.draft`.
        #expect(flow.draft?.validated == true)
        #expect(!flow.isDropping)
        // Dropping from `.previews` must never touch the rent panel — it is not
        // a navigation, and a panel installed here would price nothing real.
        #expect(flow.panel == nil)
        #expect(StubURLProtocol.requests.filter {
            $0.url?.path.hasSuffix("/rent-panel") == true
        }.count == 0)
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

    /// The edited job is not a copy of the dropped entry here, so the
    /// clear-role PATCH must stay out: firing it would blank the outfit the
    /// user is composing to save a job they never asked about.
    @Test func dropLeavesAnUnrelatedEditedJobAlone() async throws {
        let routes = Routes()
        routes.draft = Routes.draftJSON(
            batch: [Routes.entry("d1", "model__dress", "app/dress.png"),
                    Routes.entry("d2", "model__blazer", "app/blazer.png")],
            outfit: "app/other.png", seed: nil)
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        #expect(flow.canDropFromBatch)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })
        #expect(!flow.isSeeded(blazer))

        await flow.drop(blazer)

        let writes = StubURLProtocol.requests.filter { $0.httpMethod != "GET" }
        #expect(writes.map { "\($0.httpMethod!) \($0.url!.path)" } ==
                ["DELETE /v1/draft/batch/d2", "POST /v1/draft/validate"])
        #expect(flow.message == nil)
    }

    /// A stale verdict is *reported*, it does not undo the drop: the entry is
    /// gone and all three writes happened. `valid: false` alongside
    /// `stale: true` is what the server really sends (drafts.py:561), so this
    /// also pins that the stale branch wins over the invalid one.
    @Test func dropReportsAStaleValidationWithoutUndoingTheDrop() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        routes.validate = Routes.validation(valid: false, stale: true)
        let flow = make(routes)
        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        await flow.drop(blazer)

        #expect(flow.message == "The draft changed during validation. Validate it again from New Job.")
        let writes = StubURLProtocol.requests.filter { $0.httpMethod != "GET" }
        #expect(writes.map { "\($0.httpMethod!) \($0.url!.path)" } ==
                ["PATCH /v1/draft", "DELETE /v1/draft/batch/d2", "POST /v1/draft/validate"])
        #expect(flow.draft?.batch.map(\.digest) == ["d1"])
        // A stale verdict never runs `d.validated = ok` (drafts.py:552-556), so
        // the draft keeps the null the delete left behind — Confirm stays off.
        #expect(flow.draft?.validated == nil)
    }

    /// The defensive half of the pair: a 200 verdict that says `valid: false`
    /// without being stale. Today's server cannot send it — an invalid
    /// non-stale validation raises `DraftError("invalid")` and answers 422 (see
    /// the next test) — so this pins the app's tolerance of the shape the
    /// `DraftValidationResponse` model still allows.
    @Test func dropReportsAnInvalidValidationWithoutUndoingTheDrop() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        routes.validate = Routes.validation(valid: false, stale: false)
        let flow = make(routes)
        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        await flow.drop(blazer)

        #expect(flow.message == "Validation failed after the drop — open New Job to fix it.")
        let writes = StubURLProtocol.requests.filter { $0.httpMethod != "GET" }
        #expect(writes.map { "\($0.httpMethod!) \($0.url!.path)" } ==
                ["PATCH /v1/draft", "DELETE /v1/draft/batch/d2", "POST /v1/draft/validate"])
        #expect(flow.draft?.batch.map(\.digest) == ["d1"])
    }

    /// What the server actually answers an invalid non-stale validation with:
    /// 422 `invalid` (drafts.py:566 → server.py:47 `_DOMAIN_STATUS`). Since
    /// 2026-09-24 that code headlines instead of showing the server's text
    /// (spec §5), and `drop`'s catch assigns `apiError(error).userMessage` to a
    /// `String`, so the raw text has nowhere to travel — headline only, with no
    /// `detailMessage` and therefore no disclosure. The three `/v1/draft` GETs
    /// are `start`, the drop's fresh read and the `catch`'s re-read, so this
    /// also pins that a failed drop still refreshes the draft.
    @Test func dropHeadlinesARefusedValidationAndStillRefreshesTheDraft() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        routes.validate = #"{"error":{"code":"invalid","message":"make batch-validate failed: job 2 has no driver"}}"#
        routes.validateStatus = 422
        let flow = make(routes)
        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        await flow.drop(blazer)

        #expect(flow.message == "This draft didn't pass validation, so it can't run yet.")
        let writes = StubURLProtocol.requests.filter { $0.httpMethod != "GET" }
        #expect(writes.map { "\($0.httpMethod!) \($0.url!.path)" } ==
                ["PATCH /v1/draft", "DELETE /v1/draft/batch/d2", "POST /v1/draft/validate"])
        #expect(StubURLProtocol.requests.filter {
            $0.httpMethod == "GET" && $0.url?.path == "/v1/draft"
        }.count == 3)
    }

    /// `motion-enhance` has no `outfit`, so `clearRole` must fall back to a
    /// required role. Returning nil here would leave the edited copy complete,
    /// `jobs_for` would count it as a job again, and the dropped job would come
    /// back into Run — the user rents and runs the job they just dropped.
    @Test func dropClearsAFallbackRoleWhenThePipelineHasNoOutfit() async throws {
        let routes = Routes()
        routes.draft = Routes.motionDraft(
            batch: [Routes.motionEntry("m1", "model__dress", "app/walk.mp4"),
                    Routes.motionEntry("m2", "model__blazer", "app/dance.mp4")],
            driver: "app/dance.mp4")
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        #expect(flow.canDropFromBatch)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        await flow.drop(blazer)

        let writes = StubURLProtocol.requests.filter { $0.httpMethod != "GET" }
        #expect(writes.map { "\($0.httpMethod!) \($0.url!.path)" } ==
                ["PATCH /v1/draft", "DELETE /v1/draft/batch/m2", "POST /v1/draft/validate"])
        let body = try #require(JSONSerialization.jsonObject(with: writes[0].httpBody ?? Data()) as? [String: Any])
        let slots = try #require(body["slots"] as? [String: Any])
        // `character`, the first of motion-enhance's required roles in sorted
        // order — and not `driver`, which the edited job also has filled.
        #expect(slots.keys.sorted() == ["character"])
        #expect(slots["character"] is NSNull)
    }

    /// Dropping while the flow is on the rent panel must re-read it: the
    /// `panel_token` in hand priced the job that just went away. It must not
    /// advance the phase — dropping is not a navigation.
    @Test func dropOnTheRentPanelRereadsThePanelWithoutAdvancing() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        routes.setPanels([Fixtures.rentPanel, Fixtures.rentPanelFresh])
        let flow = make(routes)
        await flow.start(.existing)
        await flow.continueToRent()
        #expect(flow.phase == .rentPanel)
        #expect(flow.panel?.panelToken == "1790000000123.4.9")
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })
        let panelReads = StubURLProtocol.requests.filter { $0.url?.path.hasSuffix("/rent-panel") == true }.count

        await flow.drop(blazer)

        #expect(StubURLProtocol.requests.filter {
            $0.url?.path.hasSuffix("/rent-panel") == true
        }.count == panelReads + 1)
        // Not just that the GET happened — that its answer was installed, so
        // the quote on screen is the one that priced the surviving job.
        #expect(flow.panel?.panelToken == "1790000000999.1.10")
        #expect(flow.phase == .rentPanel)
        #expect(flow.draft?.batch.map(\.digest) == ["d1"])
        #expect(!flow.isDropping)     // a stuck flag would disable Drop for good
    }

    /// The money guard. Until a drop's first write lands, the draft's
    /// `generation` is unchanged and the server's `panel_token` is exactly
    /// `_run_token.generation` (bot.py:6895-6902), so the cached token is still
    /// accepted and `stale_panel` (bot.py:7012) does not fire. A Confirm tapped
    /// inside that window — the fresh `GET` plus a `PATCH` whose server-side
    /// probe can take 60 s — would rent and run the pre-drop basket.
    @Test func confirmIsRefusedWhileADropIsInFlight() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        await flow.continueToRent()
        #expect(flow.canConfirm(.runpod))
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        async let dropTask: Void = flow.drop(blazer)
        // Same spin the spend tests use. `drop` sets `isDropping` before its
        // first await, so the first yield that sees it is the earliest
        // observable point — still before any write landed. Bounded so a
        // scheduling surprise fails loudly instead of hanging.
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)               // never vacuous: a drop really was in flight
        #expect(flow.panel != nil)      // and observed before the drop cleared it
        #expect(!flow.canConfirm(.runpod))

        await dropTask
        #expect(!flow.isDropping)
        #expect(flow.canConfirm(.runpod))   // the guard is temporary, not sticky
    }

    /// The other half of the same money guard, and the system-level property
    /// Phase 6 introduced: a **free** draft mutation invalidates a **paid**
    /// retry. `resume` re-rents the manifest on disk and deliberately never
    /// reads the draft (bot.py:7511-7513), and `_run_token` is the manifest's
    /// `mtime_ns` (bot.py:1493-1506), which moves only when a manifest is
    /// *rewritten* — a drop does not rewrite one. So without the generation
    /// latch, Confirm → rental fails → re-enter → Drop → Retry rental rents a
    /// pod that still runs the job the user just dropped. `confirmIsRefused…`
    /// above covers the drop *in flight*; this covers the drop that succeeded.
    @Test func aDropAfterAConfirmWithdrawsTheRentalRetry() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        await flow.continueToRent()
        await flow.confirm()
        // The accepted confirm is what latches: the server gates it on a token
        // carrying the draft's generation, so acceptance proves the manifest it
        // wrote matches generation 9.
        #expect(flow.confirmedGeneration == 9)

        // The rental failed (`Fixtures.podIdle` carries `failed_rental` and no
        // lease); re-entering the flow is what offers Drop again.
        await flow.start(.existing)
        #expect(flow.phase == .previews)
        #expect(flow.canDropFromBatch)
        #expect(flow.canRetryRental)            // draft unchanged since the confirm
        #expect(flow.retryRentalBlockReason == nil)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        await flow.drop(blazer)

        #expect(flow.draft?.generation == 10)
        #expect(!flow.canRetryRental)
        #expect(flow.retryRentalBlockReason != nil)
        await flow.retryRental()
        // The confirm only — no `.resume` was ever handed to the gate.
        #expect(await gate.intents.count == 1)
        #expect(await gate.intents.allSatisfy { $0.kind == .confirm })
    }

    /// The regression this branch exists for. `confirmedGeneration` is in
    /// memory, so a relaunch loses it and the client latch opens — the server's
    /// confirm stamp is what closes it (2026-09-24 follow-ups spec §3). The
    /// app's job on that path is to surface the refusal verbatim and re-read the
    /// run, not to swallow it and leave the button offering the same tap again.
    ///
    /// The message literal is `RESUME_STALE_GENERATION` in bot.py. Asserting it
    /// here rather than a paraphrase is what keeps the two sides honest: the
    /// server's own tests all compare against the constant, so this literal is
    /// the only byte-level pin on the wording anywhere in either codebase. One
    /// line, not a concatenation, so a grep for the sentence finds it.
    @Test func aRelaunchSurfacesTheServersStaleResumeRefusal() async throws {
        let stale = "the draft changed since this rental was confirmed — Confirm again to rent what is in the draft now"
        let routes = Routes()                       // podIdle carries failed_rental
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_run",
                                           message: stale, panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)

        // A relaunch, exactly: no confirm happened during this store's lifetime.
        #expect(flow.confirmedGeneration == nil)
        #expect(flow.canRetryRental)                // the client latch is open
        #expect(flow.retryRentalBlockReason == nil)

        let tryonReads = StubURLProtocol.requests.filter {
            ($0.url?.path ?? "").hasSuffix("/tryon")
        }.count
        await flow.retryRental()

        #expect(await gate.intents.count == 1)      // the tap really was sent
        #expect(await gate.intents.first?.kind == .resume)
        #expect(flow.message == stale)              // verbatim, not swallowed
        #expect(!flow.needsRecheck)                 // a refusal is definitive
        // A floor of two reads, and the number is the assertion: one
        // `retryRental()` makes two `/tryon` GETs. `retryRental` reads the run
        // itself for the CURRENT run_token before it sends, then `applyRefusal`'s
        // `("stale_run", .resume)` case reads it again — that second read is the
        // recovery, and it is what this test is about. A bare "the count went
        // up" would pass with the case deleted, because the pre-send read alone
        // moves it; a delta of 1 fails `>= 2`. A floor rather than `== 2` so an
        // unrelated extra read-only GET (caching the run_token, say) does not
        // turn this red with a failure that names the recovery read.
        #expect(StubURLProtocol.requests.filter {
            ($0.url?.path ?? "").hasSuffix("/tryon")
        }.count >= tryonReads + 2)
    }

    /// The retry stays available when nothing moved the draft, and when no
    /// confirm was accepted in this store's lifetime — the latter is the state
    /// after an app relaunch, and refusing there would break the legitimate
    /// retry flow the whole card exists for.
    @Test func rentalRetrySurvivesAnUnchangedDraftAndARelaunch() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started"),
                                  .accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        // No confirm: `confirmedGeneration` is nil, as after a relaunch.
        await flow.start(.existing)
        #expect(flow.confirmedGeneration == nil)
        #expect(flow.canRetryRental)

        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.confirmedGeneration == 9)
        await flow.start(.existing)             // re-reads the same generation 9
        #expect(flow.canRetryRental)
        #expect(flow.retryRentalBlockReason == nil)
        await flow.retryRental()
        #expect(await gate.intents.count == 2)
        #expect(await gate.intents.last?.kind == .resume)
        // A resume must not move the latch: the server ignores the draft there.
        #expect(flow.confirmedGeneration == 9)
    }

    /// `spend` is the single funnel for all four spend entry points, so its
    /// `!isDropping` guard reaches the ones `canConfirm` cannot — `regenerate`,
    /// `retryRental` and `choose(_:)` — and any entry point added later.
    /// Probed through `regenerate`, the one whose own preconditions still hold
    /// mid-drop.
    @Test func everySpendIsRefusedWhileADropIsInFlight() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        async let dropTask: Void = flow.drop(blazer)
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)               // never vacuous: a drop really was in flight

        await flow.regenerate(index: "0", guidance: [])
        #expect(flow.message == "A batch drop is still in flight — wait for it before spending.")
        #expect(await gate.intents.isEmpty)

        await dropTask
        #expect(!flow.isDropping)
        #expect(await gate.intents.isEmpty)     // the refused spend never queued
    }

    /// `canSpend` is the money-safety term the spec singles out: the draft must
    /// never change under an unanswered spend. Every other term of
    /// `canDropFromBatch` still holds in this state, so the last assertion can
    /// only fail because of `canSpend`.
    @Test func dropIsNotOfferedWhileASpendIsUnanswered() async {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.unreachable(detail: "timed out")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        await flow.regenerate(index: "0", guidance: [])
        #expect(flow.phase == .previews)
        #expect(flow.draft?.batch.count == 2)
        #expect(flow.tryon?.phaseARunning == false)
        #expect(!flow.isDropping)
        #expect(!flow.canSpend)
        #expect(!flow.canDropFromBatch)
    }

    /// The bail-out writes nothing, but the fresh draft it just read proved the
    /// basket the panel priced is gone — so the quote goes with it, by the same
    /// rule as a successful drop.
    @Test func dropBailOutOnTheRentPanelClearsTheStaleQuote() async throws {
        let routes = Routes()
        routes.draft = Routes.draftTwoJobs
        routes.tryon = #"{"run_id":"tg-1000","run_token":"1.1","phase_a_running":false,"previews":[{"index":"0","run":"ghost","status":"done","has_image":true},{"index":"1","run":"model__dress","status":"done","has_image":true}]}"#
        let flow = make(routes)
        await flow.start(.existing)
        await flow.continueToRent()
        #expect(flow.phase == .rentPanel)
        #expect(flow.panel != nil)
        let ghost = try #require(flow.tryon?.previews.first)

        await flow.drop(ghost)

        #expect(flow.message == "The draft changed — reload before dropping.")
        #expect(flow.panel == nil)
        #expect(flow.phase == .rentPanel)     // dropping never navigates
        #expect(StubURLProtocol.requests.allSatisfy { $0.httpMethod == "GET" })
        #expect(!flow.isDropping)
    }

    @Test func spendInFlightDisablesAndClears() async {
        let gate = FakeSpendGate([.busy(attempts: 4)])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        #expect(!flow.isSpending)
        await flow.startPhaseA()
        #expect(!flow.isSpending)
        #expect(flow.message?.contains("busy") == true)
    }

    @Test func rentPanelSelectsRunpodAndQuotes() async {
        let flow = make(Routes())
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.phase == .rentPanel)
        #expect(flow.selectedProvider == .runpod)
        #expect(flow.quote(for: .runpod) == 84.0 / 60 * 0.99)
        #expect(flow.quote(for: .vast) == 1.05)       // Vast: its own session estimate
        #expect(flow.canConfirm(.runpod))
    }

    @Test func soldOutAndBlockedVastOfferNoSpendButton() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanelSoldOut])
        let flow = make(routes)
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.quote(for: .runpod) == nil)
        #expect(flow.quote(for: .vast) == nil)
        #expect(!flow.canConfirm(.runpod) && !flow.canConfirm(.vast))
    }

    @Test func confirmSendsPanelTokenAndGpu() async {
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(await gate.intents == [.confirm(runID: "tg-1000", provider: .runpod,
                                                panelToken: "1790000000123.4.9",
                                                gpu: "NVIDIA GeForce RTX 5090", tryon: nil)])
        #expect(await gate.labels.first?.contains("$1.39") == true)
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func stalePanelRereadsAndNeverConfirmsAgain() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanel, Fixtures.rentPanelFresh])
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_panel",
                                           message: "the job changed since the panel was read — read it again",
                                           panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(await gate.intents.count == 1)
        #expect(flow.phase == .rentPanel)
        #expect(flow.panel?.panelToken == "1790000000999.1.10")
        #expect(StubURLProtocol.requests.filter { $0.url?.path.hasSuffix("/rent-panel") == true }.count == 2)
        #expect(flow.message == "the job changed since the panel was read — read it again")
    }

    @Test func choiceRequiredOffersTwoFreshTaps() async {
        let gate = FakeSpendGate([
            .refused(status: 409, code: "choice_required", message: "try-on already ran", panelToken: "55.6"),
            .accepted(runID: "tg-1000", outcome: "started"),
        ])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.phase == .choiceRequired(panelToken: "55.6", provider: .runpod))
        await flow.choose(.reuse)
        let intents = await gate.intents
        #expect(intents.count == 2)
        #expect(intents[1] == .confirm(runID: "tg-1000", provider: .runpod, panelToken: "55.6",
                                       gpu: "NVIDIA GeForce RTX 5090", tryon: .reuse))
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func outcomeUnknownRequestsThePod() async {
        let gate = FakeSpendGate([.outcomeUnknown])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.phase == .outcomeUnknown)
        #expect(flow.podRequested)
        flow.acknowledgePodRequest()
        #expect(!flow.podRequested)
    }

    @Test func retryRentalOnlyWithAFailedRentalAndUsesRunToken() async {
        let live = Routes()
        live.pod = Fixtures.podLive
        let liveFlow = make(live)
        await liveFlow.start(.existing)
        #expect(!liveFlow.canRetryRental)

        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)       // podIdle carries failed_rental
        await flow.start(.existing)
        #expect(flow.canRetryRental)
        await flow.retryRental()
        #expect(await gate.intents == [.resume(runID: "tg-1000", provider: .runpod,
                                               runToken: "1790000000123.4",
                                               gpu: "NVIDIA GeForce RTX 5090")])
    }

    @Test func retryRentalSendsTheCurrentCardNotTheFailedOne() async {
        let routes = Routes()
        routes.pod = Fixtures.podFailedOtherGpu
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        #expect(flow.pod?.failedRental?.gpu == "NVIDIA GeForce RTX 5090")
        await flow.retryRental()
        #expect(await gate.intents == [.resume(runID: "tg-1000", provider: .runpod,
                                               runToken: "1790000000123.4",
                                               gpu: "NVIDIA RTX PRO 4500 Blackwell")])
        #expect(await gate.labels == ["Retry rental · NVIDIA RTX PRO 4500 Blackwell"])
    }

    /// `stale_panel` on resume is the server's gpu-mismatch answer: the card
    /// moved again, so the pod is re-read and the button names the new one.
    @Test func stalePanelOnResumeRereadsThePod() async {
        let routes = Routes()
        routes.pod = Fixtures.podFailedOtherGpu
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_panel",
                                           message: "the GPU changed — read the pod again", panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        let podReads = StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod" }.count
        routes.pod = Fixtures.podIdle           // the .env card is now the 5090
        await flow.retryRental()
        #expect(await gate.intents.count == 1)
        #expect(StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod" }.count == podReads + 1)
        #expect(flow.pod?.gpu == "NVIDIA GeForce RTX 5090")
        #expect(flow.message == "the GPU changed — read the pod again")
    }

    @Test func noFailureRefusalIsShownVerbatim() async {
        let gate = FakeSpendGate([.refused(status: 409, code: "no_failure",
                                           message: "no failed rental to retry for this run", panelToken: nil)])
        let flow = make(Routes(), gate: gate)
        await flow.start(.existing)
        await flow.retryRental()
        #expect(flow.message == "no failed rental to retry for this run")
    }

    @Test func unreachableOffersRecheckWhichNeverPerforms() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out"),
                                  .accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.needsRecheck)
        await flow.recheck()
        #expect(await gate.intents.count == 1)
        #expect(await gate.rechecks == 1)
        #expect(!flow.needsRecheck)
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func unansweredSpendDisablesEverySpendUntilChecked() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.canSpend && flow.canConfirm(.runpod))
        await flow.confirm()
        #expect(flow.needsRecheck)
        #expect(!flow.canSpend)
        #expect(!flow.canConfirm(.runpod))
    }

    /// Another spend tapped while one is unanswered is refused by the gate
    /// (`.notSent`); "Check again" and the intent it applies must survive.
    @Test func notSentSpendKeepsThePendingRecheck() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out"),
                                  .notSent(reason: "“Confirm” hasn't been answered yet. Check it before spending again."),
                                  .accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        await flow.startPhaseA()
        #expect(flow.needsRecheck)
        #expect(flow.message == "“Confirm” hasn't been answered yet. Check it before spending again.")
        await flow.recheck()
        #expect(await gate.rechecks == 1)
        #expect(flow.phase == .started(runID: "tg-1000"))   // the confirm's result, not Phase A's
    }

    @Test func recheckAppliesTheIntentTheGateHasPending() async {
        let routes = Routes()
        let entry = SpendLedgerEntry(key: "KEY-1", intent: .phaseA, label: "Try-on preview · 1 job",
                                     createdAt: .now)
        let gate = FakeSpendGate([.unreachable(detail: "timed out"),
                                  .accepted(runID: "tg-1000", outcome: "started")], pending: entry)
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()                    // the flow's own pendingIntent is this confirm
        #expect(flow.needsRecheck)
        routes.tryon = Fixtures.tryonRunning
        await flow.recheck()
        #expect(flow.phase == .phaseARunning)
        #expect(!flow.needsRecheck)
    }

    @Test func recheckWithNothingPendingClearsTheButton() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.needsRecheck)
        await flow.recheck()                    // FakeSpendGate answers .notSent, nothing pending
        #expect(!flow.needsRecheck)
        #expect(flow.canSpend)
    }

    @Test func replayRunsOncePerLaunch() async {
        let entry = SpendLedgerEntry(key: "OLD", intent: .confirm(runID: "tg-1000", provider: .runpod,
                                                                  panelToken: "t", gpu: nil, tryon: nil),
                                     label: "Confirm · RTX 5090 · ~$1.39", createdAt: .now)
        let gate = FakeSpendGate(pending: entry, replay: .accepted(runID: "tg-1000", outcome: "started"))
        let flow = make(Routes(), gate: gate)
        await flow.replayPendingOnce()
        await flow.replayPendingOnce()
        #expect(await gate.replays == 1)
        #expect(flow.phase == .started(runID: "tg-1000"))
        #expect(flow.pendingNotice == nil)
    }

    @Test func chooserKeepsTheConfirmedProvider() async {
        let gate = FakeSpendGate([
            .refused(status: 409, code: "choice_required", message: "try-on already ran", panelToken: "55.6"),
            .accepted(runID: "tg-1000", outcome: "started"),
        ])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        flow.selectedProvider = .vast
        await flow.confirm()
        #expect(flow.phase == .choiceRequired(panelToken: "55.6", provider: .vast))
        // The picker moved on while the confirm was outstanding — must not
        // steer the chooser, which must still act on the confirmed provider.
        flow.selectedProvider = .runpod
        await flow.choose(.reuse)
        let intents = await gate.intents
        #expect(intents.count == 2)
        #expect(intents[1] == .confirm(runID: "tg-1000", provider: .vast, panelToken: "55.6",
                                       gpu: nil, tryon: .reuse))
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func replayedChoiceRequiredLoadsPanelAndUsesEntryProvider() async {
        let entry = SpendLedgerEntry(key: "OLD", intent: .confirm(runID: "tg-1000", provider: .vast,
                                                                  panelToken: "t", gpu: nil, tryon: nil),
                                     label: "Confirm · Vast · ~$1.05", createdAt: .now)
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")], pending: entry,
                                 replay: .refused(status: 409, code: "choice_required",
                                                  message: "try-on already ran", panelToken: "55.6"))
        let flow = make(Routes(), gate: gate)
        await flow.replayPendingOnce()
        #expect(flow.phase == .choiceRequired(panelToken: "55.6", provider: .vast))
        #expect(flow.panel != nil)
        await flow.choose(.rerun)
        #expect(await gate.intents == [.confirm(runID: "tg-1000", provider: .vast, panelToken: "55.6",
                                                gpu: nil, tryon: .rerun)])
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    /// A re-entered `start` (e.g. popping back onto a shared RunFlow while a
    /// spend is outstanding) must not overwrite the outcome that spend is
    /// about to apply — it silently no-ops instead of re-reading `/v1/pod`.
    @Test func startIsRefusedWhileASpendIsInFlight() async {
        let routes = Routes()
        let gate = SuspendingSpendGate()
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        let podHitsBeforeSpend = StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod" }.count

        async let spendTask: Void = flow.startPhaseA()
        while !flow.isSpending { await Task.yield() }

        let phaseDuringSpend = flow.phase
        let tryonDuringSpend = flow.tryon

        await flow.start(.newJob)
        #expect(flow.phase == phaseDuringSpend)
        #expect(flow.tryon == tryonDuringSpend)
        #expect(StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod" }.count == podHitsBeforeSpend)

        routes.tryon = Fixtures.tryonRunning   // still running when apply()'s refreshTryon reads it
        await gate.release()
        await spendTask
        #expect(flow.phase == .phaseARunning)
    }

    @Test func aPendingMigrateIsLeftToMigrateFlow() async {
        let entry = SpendLedgerEntry(key: "K1", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "t"),
                                     label: "Migrate volume to EU-CZ-1", createdAt: .now)
        let gate = FakeSpendGate(pending: entry, replay: .accepted(runID: nil, outcome: "started"))
        let flow = make(Routes(), gate: gate)
        await flow.replayPendingOnce()
        #expect(await gate.replays == 0)
        #expect(flow.pendingNotice == nil)
        #expect(flow.phase == .loading)
    }

    @Test func gpuChangeClearsTheQuoteAndRereadsThePanel() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanel, Fixtures.rentPanelFresh])
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.panel?.panelToken == "1790000000123.4.9")
        await flow.reloadPanelAfterGpuChange()
        #expect(flow.panel?.panelToken == "1790000000999.1.10")
        #expect(await gate.intents.isEmpty)          // a GPU change never confirms by itself
        await flow.confirm()
        guard case let .confirm(_, _, token, _, _)? = await gate.intents.first else {
            Issue.record("no confirm was sent")
            return
        }
        #expect(token == "1790000000999.1.10")
    }

    @Test func chooserRefusesWithoutAPrice() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanelSoldOut])
        let entry = SpendLedgerEntry(key: "OLD", intent: .confirm(runID: "tg-1000", provider: .runpod,
                                                                  panelToken: "t", gpu: nil, tryon: nil),
                                     label: "Confirm · RTX 5090 · ~$1.39", createdAt: .now)
        let gate = FakeSpendGate(pending: entry,
                                 replay: .refused(status: 409, code: "choice_required",
                                                  message: "try-on already ran", panelToken: "55.6"))
        let flow = make(routes, gate: gate)
        await flow.replayPendingOnce()
        #expect(flow.phase == .choiceRequired(panelToken: "55.6", provider: .runpod))
        await flow.choose(.reuse)
        #expect(await gate.intents.isEmpty)
        #expect(flow.message == "Couldn't price this confirm — pull to refresh the rent panel, then choose again.")
    }
}
}
