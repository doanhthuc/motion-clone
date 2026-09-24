import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct MigrateFlowTests {
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _ask = TestSupport.json(Fixtures.migrateAsk)
        /// The drop-guard test needs an in-flight `RunFlow.drop`, which needs the
        /// run flow's whole route table. Delegating keeps one copy of it instead
        /// of a second table that can drift. `/v1/pod` was the one path both
        /// tables served and both answered `Fixtures.podIdle` — it is
        /// `RunFlowTests.Routes`'s `pod` default — so the delegation changes
        /// nothing for the existing migrate-only tests.
        let runs = RunFlowTests.Routes()
        var ask: (Int, [String: String], Data) {
            get { lock.withLock { _ask } } set { lock.withLock { _ask = newValue } }
        }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch request.url?.path ?? "" {
            case "/v1/pod/migrate/ask": return ask
            default: return runs.answer(request)
            }
        }
    }

    final class Clock: @unchecked Sendable {
        private let lock = NSLock()
        private var current = Date(timeIntervalSince1970: 1_790_000_000)
        var now: Date { lock.withLock { current } }
        func advance(_ seconds: TimeInterval) { lock.withLock { current += seconds } }
    }

    private func make(_ routes: Routes = Routes(), gate: any SpendSending = FakeSpendGate(),
                      clock: Clock = Clock()) -> MigrateFlow {
        makePaired(routes, gate: gate, clock: clock).0
    }

    /// Both flows over one stub and one client, the way
    /// `MotionApp.reconnect()` builds them — `MigrateFlow` reads
    /// `RunFlow.isDropping`, so a test of that guard needs the real pairing.
    private func makePaired(_ routes: Routes = Routes(), gate: any SpendSending = FakeSpendGate(),
                            clock: Clock = Clock()) -> (MigrateFlow, RunFlow) {
        StubURLProtocol.install { routes.answer($0) }
        let client = TestSupport.client()
        let runFlow = RunFlow(client: client, gate: gate, sleep: { _ in })
        return (MigrateFlow(client: client, gate: gate, pod: PodStore(client: client),
                            runFlow: runFlow, now: { clock.now }), runFlow)
    }

    private func askedAndTyped(_ gate: FakeSpendGate, clock: Clock = Clock()) async -> MigrateFlow {
        let flow = make(gate: gate, clock: clock)
        await flow.ask(toDc: "EU-CZ-1")
        flow.typed = "EU-CZ-1"
        return flow
    }

    @Test func askShowsTheWarningAndStartsTheCountdown() async throws {
        let clock = Clock()
        let flow = make(clock: clock)
        await flow.ask(toDc: "EU-CZ-1")
        #expect(flow.currentAsk?.confirmToken == "tok-abc")
        #expect(flow.secondsLeft(at: clock.now) == 585)       // 600 − the 15 s margin
        let post = try #require(StubURLProtocol.requests.last { $0.url?.path == "/v1/pod/migrate/ask" })
        #expect(post.value(forHTTPHeaderField: "Idempotency-Key") == nil)
        let body = try #require(JSONSerialization.jsonObject(with: post.httpBody ?? Data()) as? [String: String])
        #expect(body == ["to_dc": "EU-CZ-1"])
    }

    @Test func askRefusalShowsTheServerText() async {
        let routes = Routes()
        routes.ask = TestSupport.json(
            #"{"error": {"code": "run_active", "message": "a pod is live — stop it before moving the volume"}}"#, status: 409)
        let flow = make(routes)
        await flow.ask(toDc: "EU-CZ-1")
        #expect(flow.message == "a pod is live — stop it before moving the volume")
        #expect(flow.step == .choose)
        #expect(flow.destination == "EU-CZ-1")
    }

    @Test func typedConfirmationMustMatchExactly() async {
        let clock = Clock()
        let flow = make(clock: clock)
        await flow.ask(toDc: "EU-CZ-1")
        flow.typed = "eu-cz-1"
        #expect(!flow.canMigrate(at: clock.now))
        flow.typed = "EU-CZ-1 "
        #expect(!flow.canMigrate(at: clock.now))
        flow.typed = "EU-CZ-1"
        #expect(flow.canMigrate(at: clock.now))
    }

    @Test func anExpiredConfirmationSendsNothing() async {
        let clock = Clock()
        let gate = FakeSpendGate([.accepted(runID: nil, outcome: "started")])
        let flow = await askedAndTyped(gate, clock: clock)
        clock.advance(585)
        #expect(!flow.canMigrate(at: clock.now))
        #expect(flow.secondsLeft(at: clock.now) == 0)
        await flow.migrate()
        #expect(await gate.intents.isEmpty)
    }

    @Test func migrateSendsOneIntentThroughTheGate() async {
        let gate = FakeSpendGate([.accepted(runID: nil, outcome: "started")])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(await gate.intents == [.migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc")])
        #expect(await gate.labels == ["Migrate volume to EU-CZ-1"])
        #expect(flow.step == .started(toDc: "EU-CZ-1"))
        #expect(StubURLProtocol.requests.contains { $0.url?.path == "/v1/pod" })
    }

    @Test func aStaleTokenGoesBackToAsk() async {
        let gate = FakeSpendGate([.refused(status: 409, code: "bad_confirm_token",
                                           message: "that confirmation is no longer valid — ask again",
                                           panelToken: nil)])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.step == .choose)
        #expect(flow.destination == "EU-CZ-1")
        #expect(flow.typed == "")
        #expect(flow.message == "That confirmation expired — ask again.")
    }

    @Test func outcomeUnknownIsNeverRetried() async {
        let gate = FakeSpendGate([.outcomeUnknown])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.step == .outcomeUnknown)
        #expect(await gate.intents.count == 1)
        #expect(await gate.rechecks == 0)
        #expect(flow.message?.contains("Couldn't tell whether the migration started") == true)
    }

    @Test func unreachableIsCheckedWithTheSavedKey() async {
        let clock = Clock()
        let gate = FakeSpendGate([.unreachable(detail: "timed out"), .accepted(runID: nil, outcome: "started")])
        let flow = await askedAndTyped(gate, clock: clock)
        #expect(flow.canMigrate(at: clock.now))
        await flow.migrate()
        #expect(flow.needsRecheck)
        #expect(!flow.canMigrate(at: clock.now))
        await flow.recheck()
        #expect(await gate.rechecks == 1)
        #expect(await gate.intents.count == 1)
        #expect(flow.step == .started(toDc: "EU-CZ-1"))
        #expect(!flow.needsRecheck)
    }

    @Test func askIsRefusedWhileAMigrateIsUnanswered() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out")])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.needsRecheck)
        await flow.ask(toDc: "US-TX-3")
        let asks = StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod/migrate/ask" }
        #expect(asks.count == 1)
        #expect(flow.needsRecheck)
    }

    @Test func recheckRefusesAPendingNonMigrate() async {
        let gate = FakeSpendGate([.unreachable(detail: "x")],
                                 pending: SpendLedgerEntry(key: "K", intent: .phaseA,
                                                            label: "Try-on preview · 1 job", createdAt: .now))
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.needsRecheck)
        await flow.recheck()
        #expect(await gate.rechecks == 0)
        #expect(flow.message?.contains("isn't a migration") == true)
    }

    @Test func launchReplayResolvesAPendingMigrateOnce() async {
        let entry = SpendLedgerEntry(key: "K1", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc"),
                                     label: "Migrate volume to EU-CZ-1", createdAt: .now)
        let gate = FakeSpendGate(pending: entry, replay: .accepted(runID: nil, outcome: "started"))
        let flow = make(gate: gate)
        await flow.replayPendingOnce()
        await flow.replayPendingOnce()
        #expect(await gate.replays == 1)
        #expect(flow.step == .started(toDc: "EU-CZ-1"))
        #expect(flow.pendingNotice == nil)
    }

    @Test func blockerNamesWhyAMigrationCannotStart() throws {
        let decoder = MotionJSON.decoder
        let idle = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podIdle))
        let live = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podLive))
        let migrating = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podMigrating))
        #expect(MigrateFlow.blocker(pod: idle, runStatus: nil) == nil)
        #expect(MigrateFlow.blocker(pod: idle, runStatus: .done) == nil)
        #expect(MigrateFlow.blocker(pod: idle, runStatus: .phaseA)?.contains("run is busy") == true)
        #expect(MigrateFlow.blocker(pod: live, runStatus: nil)?.contains("pod is live") == true)
        #expect(MigrateFlow.blocker(pod: migrating, runStatus: nil)?.contains("already running") == true)
    }

    @Test func beginPreselectsAndResets() async {
        let flow = make()
        await flow.ask(toDc: "EU-CZ-1")
        flow.begin(preselect: "US-TX-3")
        #expect(flow.step == .choose)
        #expect(flow.destination == "US-TX-3")
        #expect(flow.currentAsk == nil)
    }

    /// Phase 6 put `guard !isDropping` in `RunFlow.spend`, the single funnel its
    /// four spend entry points share. `MigrateFlow.migrate()` calls
    /// `gate.perform` directly and so escaped it — this is the fifth entry
    /// point, guarded on its own terms. Probed mid-drop with the same bounded
    /// spin `RunFlowTests` uses, so the assertion can never be vacuous.
    @Test func migrateIsRefusedWhileADropIsInFlight() async throws {
        let clock = Clock()
        let routes = Routes()
        routes.runs.draft = RunFlowTests.Routes.draftTwoJobs
        routes.runs.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let (migrate, flow) = makePaired(routes, gate: gate, clock: clock)
        await migrate.ask(toDc: "EU-CZ-1")
        migrate.typed = "EU-CZ-1"
        #expect(migrate.canMigrate(at: clock.now))          // ready before the drop
        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })

        async let dropTask: Void = flow.drop(blazer)
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)               // never vacuous: a drop really was in flight

        #expect(!migrate.canMigrate(at: clock.now))         // the button is off the glass
        let stepBefore = migrate.step
        await migrate.migrate()
        #expect(migrate.message == "A batch drop is still in flight — wait for it before moving the volume.")
        #expect(migrate.step == stepBefore)                 // still on the typed confirm
        #expect(migrate.currentAsk?.confirmToken != nil)    // ...and its token was not consumed
        #expect(await gate.intents.isEmpty)                 // no spend of any kind was sent

        await dropTask
        #expect(!flow.isDropping)
        #expect(migrate.canMigrate(at: clock.now))          // temporary, not sticky
    }

    /// Review Focus 4, the `recheck()` half — the launch-replay half is
    /// `replayPendingOnceIsNotBlockedByADrop` below. `canMigrate` gained a
    /// term; if `recheck()` consulted it, an unanswered migrate — the one state
    /// the whole pending-notice machinery exists to resolve — could become
    /// unresolvable behind an unrelated drop. It must not.
    @Test func recheckIsNotBlockedByADrop() async throws {
        let clock = Clock()
        let routes = Routes()
        routes.runs.draft = RunFlowTests.Routes.draftTwoJobs
        routes.runs.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.unreachable(detail: "timed out"),
                                  .accepted(runID: "tg-1000", outcome: "started")])
        let (migrate, flow) = makePaired(routes, gate: gate, clock: clock)
        await migrate.ask(toDc: "EU-CZ-1")
        migrate.typed = "EU-CZ-1"
        await migrate.migrate()
        #expect(migrate.needsRecheck)                       // the first attempt never landed

        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })
        async let dropTask: Void = flow.drop(blazer)
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)

        await migrate.recheck()                             // resolves anyway
        #expect(!migrate.needsRecheck)
        #expect(migrate.step == .started(toDc: "EU-CZ-1"))
        #expect(await gate.intents.count == 1)              // resolved the saved request
        #expect(await gate.rechecks == 1)                   // ...it did not mint a new one
        await dropTask
    }

    /// Review Focus 4, the launch-replay half, and the one with no user watching:
    /// `MotionApp.replayPendingSpend()` calls this once per launch, so a replay
    /// stranded behind an unrelated drop is not a greyed button somebody notices
    /// — it is a pending migration that silently never resolves. It gets its own
    /// flow because `recheck()` consumes the ledger entry the replay reads.
    @Test func replayPendingOnceIsNotBlockedByADrop() async throws {
        let clock = Clock()
        let routes = Routes()
        routes.runs.draft = RunFlowTests.Routes.draftTwoJobs
        routes.runs.tryon = Fixtures.tryonDone
        // `.unreachable` is what leaves a real migrate pending; the ledger entry
        // is scripted beside it because `FakeSpendGate` records intents but does
        // not model the ledger `perform` would have written.
        let entry = SpendLedgerEntry(key: "K1", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc"),
                                     label: "Migrate volume to EU-CZ-1", createdAt: .now)
        let gate = FakeSpendGate([.unreachable(detail: "timed out")], pending: entry,
                                 replay: .accepted(runID: nil, outcome: "started"))
        let (migrate, flow) = makePaired(routes, gate: gate, clock: clock)
        await migrate.ask(toDc: "EU-CZ-1")
        migrate.typed = "EU-CZ-1"
        await migrate.migrate()
        #expect(migrate.needsRecheck)                       // the first attempt never landed

        await flow.start(.existing)
        let blazer = try #require(flow.tryon?.previews.first { $0.run == "model__blazer" })
        async let dropTask: Void = flow.drop(blazer)
        var observed = false
        for _ in 0..<1000 {
            if flow.isDropping { observed = true; break }
            await Task.yield()
        }
        #expect(observed)               // never vacuous: a drop really was in flight

        await migrate.replayPendingOnce()                   // resolves anyway
        #expect(!migrate.needsRecheck)
        #expect(migrate.step == .started(toDc: "EU-CZ-1"))
        #expect(await gate.replays == 1)
        #expect(await gate.intents.count == 1)              // the replay resends, never re-spends
        #expect(migrate.pendingNotice == nil)
        await dropTask
    }
}
}
