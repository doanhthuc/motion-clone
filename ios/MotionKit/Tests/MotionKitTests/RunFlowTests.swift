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
        var pod: String { get { lock.withLock { _pod } } set { lock.withLock { _pod = newValue } } }
        var tryon: String { get { lock.withLock { _tryon } } set { lock.withLock { _tryon = newValue } } }
        var draft: String { get { lock.withLock { _draft } } set { lock.withLock { _draft = newValue } } }
        func setPanels(_ p: [String]) { lock.withLock { _panels = p } }
        func nextPanel() -> String { lock.withLock { _panels.count > 1 ? _panels.removeFirst() : _panels[0] } }

        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            let path = request.url?.path ?? ""
            switch true {
            case path == "/v1/pod": return TestSupport.json(pod)
            case path == "/v1/pipelines": return TestSupport.json(Fixtures.pipelines)
            case path == "/v1/draft": return TestSupport.json(draft)
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

    private func make(_ routes: Routes, gate: FakeSpendGate = FakeSpendGate()) -> RunFlow {
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
