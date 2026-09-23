import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct PodStoreTests {
    /// `GET /v1/pod` answers from a queue (the last entry repeats); the kill
    /// POST answers from its own script.
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var pods: [String]
        private var kills: [(Int, [String: String], Data)]
        init(pods: [String], kills: [(Int, [String: String], Data)] = []) {
            self.pods = pods
            self.kills = kills
        }
        func setPods(_ p: [String]) { lock.withLock { pods = p } }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            lock.withLock {
                let path = request.url?.path ?? ""
                if request.httpMethod == "POST", path == "/v1/runs/tg-1000/kill" {
                    return kills.isEmpty ? (500, [:], Data()) : kills.removeFirst()
                }
                if path == "/v1/pod" {
                    return TestSupport.json(pods.count > 1 ? pods.removeFirst() : pods[0])
                }
                return (404, [:], Data())
            }
        }
    }

    final class Clock: @unchecked Sendable {
        private let lock = NSLock()
        private var current = Date(timeIntervalSince1970: 1_790_000_000)
        var now: Date { lock.withLock { current } }
        func advance(_ seconds: TimeInterval) { lock.withLock { current += seconds } }
    }

    static func pod(lease: Bool = false, killRunning: Bool = false, lastKill: String = "null",
                    migration: String = "null") -> String {
        let leaseJSON = lease
            ? #"{"provider": "runpod", "provisioned_at": 1789999000, "abs_max_min": 180, "quoted_usd_per_hr": 0.99, "run_id": "tg-1000"}"#
            : "null"
        return """
        {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090", "lease": \(leaseJSON),
         "migration": \(migration), "kill_running": \(killRunning), "last_kill": \(lastKill),
         "failed_rental": null}
        """
    }

    static func lastKill(at: Double, ok: Bool, code: String, message: String) -> String {
        #"{"at": \#(at), "ok": \#(ok), "code": "\#(code)", "message": "\#(message)"}"#
    }

    static let killed = lastKill(at: 1_790_000_100, ok: true, code: "killed",
                                 message: "pod destroyed and verified gone")
    static let accepted = TestSupport.json(#"{"run_id": "tg-1000", "outcome": "kill_started"}"#, status: 202)
    static let dropped: (Int, [String: String], Data) = (-1, [:], Data())

    static func refusal(_ status: Int, _ code: String, _ message: String) -> (Int, [String: String], Data) {
        TestSupport.json(#"{"error": {"code": "\#(code)", "message": "\#(message)"}}"#, status: status)
    }

    static func freshDefaults() -> UserDefaults {
        UserDefaults(suiteName: "podstore-\(UUID().uuidString)")!
    }

    private func make(_ routes: Routes, clock: Clock = Clock(), step: TimeInterval = 2,
                      defaults: UserDefaults = PodStoreTests.freshDefaults()) -> PodStore {
        StubURLProtocol.install { routes.answer($0) }
        return PodStore(client: TestSupport.client(), defaults: defaults,
                        sleep: { _ in clock.advance(step) }, now: { clock.now })
    }

    private var killPosts: [URLRequest] {
        StubURLProtocol.requests.filter { $0.httpMethod == "POST" && $0.url?.path == "/v1/runs/tg-1000/kill" }
    }

    private var podReads: Int {
        StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod" }.count
    }

    // MARK: kill

    @Test func acceptedKillPollsUntilTheServerRecordsTheResult() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.accepted])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(lease: true, killRunning: true), Self.pod(lastKill: Self.killed)])
        await store.kill(runID: "tg-1000")
        #expect(store.killState == .idle)
        #expect(store.killNotice == .init(text: "pod destroyed and verified gone", isError: false))
        #expect(killPosts.count == 1)
        #expect(killPosts.first?.value(forHTTPHeaderField: "Idempotency-Key")?.isEmpty == false)
        #expect(store.pod?.lease == nil)
    }

    @Test func killInProgressIsFollowedLikeAccepted() async {
        let routes = Routes(pods: [Self.pod(lease: true)],
                            kills: [Self.refusal(409, "kill_in_progress", "a kill is already running")])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(killRunning: true), Self.pod(lastKill: Self.killed)])
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice?.text == "pod destroyed and verified gone")
        #expect(killPosts.count == 1)
    }

    @Test func nothingRunningShowsTheServerTextAndRefreshes() async {
        let routes = Routes(pods: [Self.pod()],
                            kills: [Self.refusal(409, "nothing_running", "nothing is running — there is no pod to kill")])
        let store = make(routes)
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice == .init(text: "nothing is running — there is no pod to kill", isError: true))
        #expect(store.killState == .idle)
        #expect(store.pod != nil)
    }

    @Test func botBusyReenablesWithoutResending() async {
        let routes = Routes(pods: [Self.pod(lease: true)],
                            kills: [Self.refusal(503, "bot_busy", "the bot is busy")])
        let store = make(routes)
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice?.text == "The bot is busy — tap Kill again.")
        #expect(store.killState == .idle)
        #expect(killPosts.count == 1)
    }

    @Test func droppedConnectionIsNeverResentAndThePodTellsWhatHappened() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.dropped])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(lease: true, killRunning: true), Self.pod(lastKill: Self.killed)])
        await store.kill(runID: "tg-1000")
        #expect(killPosts.count == 1)
        #expect(store.killNotice?.text == "pod destroyed and verified gone")
    }

    @Test func ambiguousKillWithNoTraceSaysKillingAgainIsSafe() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.dropped])
        let store = make(routes)
        await store.refresh()
        await store.kill(runID: "tg-1000")
        #expect(killPosts.count == 1)
        #expect(store.killNotice?.isError == true)
        #expect(store.killNotice?.text.contains("killing again is safe") == true)
        #expect(podReads == 1 + 3)          // the initial read + three probes
        #expect(store.killState == .idle)
    }

    @Test func aKillThatEndsWithoutANewResultSaysSo() async {
        let earlier = Self.lastKill(at: 1_789_000_000, ok: true, code: "killed", message: "old")
        let routes = Routes(pods: [Self.pod(lease: true, lastKill: earlier)], kills: [Self.accepted])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(lastKill: earlier)])
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice == .init(text: "The kill ended without a result — check Telegram.", isError: true))
    }

    @Test func pollingStopsAtFiveMinutesAndCheckAgainFinishes() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.accepted])
        let store = make(routes, step: 100)
        await store.refresh()
        routes.setPods([Self.pod(lease: true, killRunning: true)])
        await store.kill(runID: "tg-1000")
        #expect(store.killStillRunning)
        #expect(store.killState == .idle)
        #expect(store.killNotice?.text == "The kill is still running on the VPS.")
        routes.setPods([Self.pod(lastKill: Self.killed)])
        await store.checkKillAgain()
        #expect(!store.killStillRunning)
        #expect(store.killNotice?.text == "pod destroyed and verified gone")
    }

    @Test func killIsDrawnForALeaseOrALiveRunOnly() async {
        let routes = Routes(pods: [Self.pod()])
        let store = make(routes)
        await store.refresh()
        #expect(!store.showsKill())
        #expect(store.showsKill(runStatus: .phaseA))
        #expect(!store.showsKill(runStatus: .done))
        routes.setPods([Self.pod(lease: true)])
        await store.refresh()
        #expect(store.showsKill())
    }

    // MARK: banner

    @Test func unverifiedDestroyAndWorkerErrorRaiseTheBanner() async {
        for code in ["destroy_unverified", "error"] {
            let routes = Routes(pods: [Self.pod(lastKill: Self.lastKill(at: 5, ok: false, code: code, message: "check it"))])
            let store = make(routes)
            await store.refresh()
            #expect(store.unverifiedKill?.code == code)
        }
    }

    @Test func successfulOrHarmlessKillsRaiseNoBanner() async {
        let cases: [(Bool, String)] = [(true, "killed"), (true, "phase_a_stopped"),
                                       (false, "nothing_running"), (false, "phase_a_finished")]
        for (ok, code) in cases {
            let routes = Routes(pods: [Self.pod(lastKill: Self.lastKill(at: 5, ok: ok, code: code, message: "m"))])
            let store = make(routes)
            await store.refresh()
            #expect(store.unverifiedKill == nil)
        }
    }

    @Test func acknowledgementHidesThatKillOnlyAndSurvivesARelaunch() async {
        let defaults = Self.freshDefaults()
        let first = Self.lastKill(at: 5, ok: false, code: "destroy_unverified", message: "check it")
        let routes = Routes(pods: [Self.pod(lastKill: first)])
        let store = make(routes, defaults: defaults)
        await store.refresh()
        store.acknowledgeUnverifiedKill()
        #expect(store.unverifiedKill == nil)
        let relaunched = make(routes, defaults: defaults)
        await relaunched.refresh()
        #expect(relaunched.unverifiedKill == nil)
        routes.setPods([Self.pod(lastKill: Self.lastKill(at: 9, ok: false, code: "destroy_unverified", message: "again"))])
        await relaunched.refresh()
        #expect(relaunched.unverifiedKill?.at == 9)
    }

    // MARK: migration poll

    @Test func migrationPollRefreshesOnlyWhileAMigrationRuns() async {
        let running = #"{"running": true, "phase": "copy", "to_dc": "EU-CZ-1", "started_at": 1790000000, "bytes_copied": 1, "total_bytes": 4}"#
        let routes = Routes(pods: [Self.pod(migration: running), Self.pod()])
        StubURLProtocol.install { routes.answer($0) }
        let sleeps = Counter()
        let store = PodStore(client: TestSupport.client(), defaults: Self.freshDefaults(),
                             sleep: { _ in if sleeps.increment() > 3 { throw CancellationError() } })
        await store.refresh()
        await store.pollMigration()
        #expect(podReads == 2)
    }
}
}
