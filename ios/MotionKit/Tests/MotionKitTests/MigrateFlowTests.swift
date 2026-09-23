import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct MigrateFlowTests {
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _ask = TestSupport.json(Fixtures.migrateAsk)
        var ask: (Int, [String: String], Data) {
            get { lock.withLock { _ask } } set { lock.withLock { _ask = newValue } }
        }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch request.url?.path ?? "" {
            case "/v1/pod/migrate/ask": return ask
            case "/v1/pod": return TestSupport.json(Fixtures.podIdle)
            default: return (404, [:], Data())
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
        StubURLProtocol.install { routes.answer($0) }
        let client = TestSupport.client()
        return MigrateFlow(client: client, gate: gate, pod: PodStore(client: client), now: { clock.now })
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
}
}
