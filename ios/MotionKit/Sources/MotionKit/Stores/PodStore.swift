import Foundation
import Observation

/// `GET /v1/pod`, the kill and the unverified-kill banner (Phase 5 design §4).
///
/// Kill deliberately bypasses `SpendGate`: the server makes a repeated kill
/// harmless (`kill_in_progress`, `nothing_running`, and a live re-check under
/// `BOT_LOCK`), and a kill must never wait behind an unanswered confirm —
/// that is exactly when a pod may be billing. So each tap mints its own key,
/// nothing is journalled, and an ambiguous answer is never resent: the pod
/// status says what happened.
@MainActor @Observable
public final class PodStore {
    public enum KillState: Equatable, Sendable { case idle, sending, killing }

    public struct Notice: Equatable, Sendable {
        public let text: String
        public let isError: Bool
    }

    public private(set) var pod: PodStatus?
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    public private(set) var killState: KillState = .idle
    public private(set) var killNotice: Notice?
    /// The 5-minute poll cap was reached with the kill still running.
    public private(set) var killStillRunning = false

    /// `last_kill.at` the user said they checked by hand (design §4).
    static let ackKey = "pod.ackedKillAt"
    static let killPollInterval: Duration = .seconds(2)
    /// The server's kill takes up to ~210 s (30 s drain wait + 180 s destroy).
    static let killPollCap: TimeInterval = 300
    static let ambiguousProbes = 3
    /// `GET /v1/pod` is files only; a migration takes ~25–30 min.
    static let migrationPollInterval: Duration = .seconds(10)
    /// A worker that raised mid-destroy may have left a pod billing too.
    static let unverifiedCodes: Set<String> = ["destroy_unverified", "error"]

    private let client: APIClient
    private let defaults: UserDefaults
    private let sleep: @Sendable (Duration) async throws -> Void
    private let now: @Sendable () -> Date
    private let makeKey: @Sendable () -> String
    private var ackedKillAt: Double?
    /// `last_kill.at` before the current kill was sent — the server's own
    /// clock, so a changed value means a new result without any skew.
    private var killBaseline: Double?

    public init(client: APIClient, defaults: UserDefaults = .standard,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                now: @escaping @Sendable () -> Date = { Date() },
                makeKey: @escaping @Sendable () -> String = { UUID().uuidString }) {
        self.client = client
        self.defaults = defaults
        self.sleep = sleep
        self.now = now
        self.makeKey = makeKey
        ackedKillAt = defaults.object(forKey: Self.ackKey) as? Double
    }

    public var isStale: Bool { pod != nil && error != nil }
    public var isKilling: Bool { killState != .idle }

    /// `GET /v1/pod` is files and memory only on the server — cheap to call.
    public func refresh() async {
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }

    // MARK: kill

    /// Whether to draw Kill. The server decides (`nothing_running`); this only
    /// draws the button.
    public func showsKill(runStatus: RunStatus? = nil) -> Bool {
        guard let pod, pod.runId != nil else { return false }
        return pod.lease != nil || pod.killRunning || runStatus?.isLive == true
    }

    public func kill(runID: String) async {
        guard killState == .idle else { return }
        killState = .sending
        killNotice = nil
        killStillRunning = false
        killBaseline = pod?.lastKill?.at
        let raw = await client.spendPost(["v1", "runs", runID, "kill"], body: Data("{}".utf8),
                                         idempotencyKey: makeKey(), timeout: 90)
        switch Self.classifyKill(raw) {
        case .started:
            killState = .killing
            await followKill()
        case let .refused(code, text):
            killState = .idle
            killNotice = Notice(text: text, isError: true)
            if code == "nothing_running" { await refresh() }
        case .busy:
            killState = .idle
            killNotice = Notice(text: "The bot is busy — tap Kill again.", isError: true)
        case .ambiguous(let detail):
            await probeAfterAmbiguousKill(detail)
        }
    }

    /// After the 5-minute cap: one read, then either finish or keep following.
    public func checkKillAgain() async {
        guard killState == .idle, killStillRunning else { return }
        await refresh()
        guard error == nil, let pod else { return }
        killStillRunning = false
        if pod.killRunning {
            killState = .killing
            await followKill()
        } else {
            finishKill()
        }
    }

    /// Never resends: probes the pod three times, 2 s apart.
    private func probeAfterAmbiguousKill(_ detail: String) async {
        for _ in 0..<Self.ambiguousProbes {
            do { try await sleep(Self.killPollInterval) } catch { break }
            await refresh()
            guard error == nil, let pod else { continue }
            if pod.killRunning {
                killState = .killing
                await followKill()
                return
            }
            if pod.lastKill?.at != killBaseline {
                finishKill()
                return
            }
        }
        killState = .idle
        killNotice = Notice(text: "Couldn't tell whether the kill started (\(detail)) — killing again is safe.",
                            isError: true)
    }

    private func followKill() async {
        let deadline = now().addingTimeInterval(Self.killPollCap)
        while true {
            await refresh()
            if error == nil, let pod, !pod.killRunning {
                finishKill()
                return
            }
            if now() >= deadline {
                killState = .idle
                killStillRunning = true
                killNotice = Notice(text: "The kill is still running on the VPS.", isError: false)
                return
            }
            do { try await sleep(Self.killPollInterval) } catch {
                killState = .idle
                return
            }
        }
    }

    /// An unchanged `last_kill.at` means the bot restarted mid-kill: the
    /// thread died and wrote nothing.
    private func finishKill() {
        killState = .idle
        if let kill = pod?.lastKill, kill.at != killBaseline {
            killNotice = Notice(text: kill.message, isError: !kill.ok)
        } else {
            killNotice = Notice(text: "The kill ended without a result — check Telegram.", isError: true)
        }
    }

    enum KillAnswer: Equatable {
        case started, busy
        case refused(code: String, text: String)
        case ambiguous(String)
    }

    static func classifyKill(_ raw: RawSpendResponse) -> KillAnswer {
        switch raw {
        case .transport(let detail):
            return .ambiguous(detail)
        case let .http(status, body):
            if (200..<300).contains(status) { return .started }
            if let envelope = try? MotionJSON.decoder.decode(SpendGate.Envelope.self, from: body) {
                let code = envelope.error.code
                if status == 503 && code == "bot_busy" { return .busy }
                if code == "kill_in_progress" { return .started }
                if status >= 500 { return .ambiguous("HTTP \(status) \(code)") }
                return .refused(code: code, text: APIError.server(
                    status: status, code: code, message: envelope.error.message).userMessage)
            }
            if status >= 500 { return .ambiguous("HTTP \(status) without an API answer") }
            if status == 403 {
                return .refused(code: "access_denied", text: APIError.accessDenied(status: 403).userMessage)
            }
            return .refused(code: "http_\(status)", text: "HTTP \(status)")
        }
    }

    // MARK: banner

    /// A kill that could not verify the destroy, not yet acknowledged. The
    /// server clears the lease either way (`_do_kill`), so "no lease" proves
    /// nothing; only the user or a later successful kill clears this.
    public var unverifiedKill: KillResult? {
        guard let kill = pod?.lastKill, !kill.ok, Self.unverifiedCodes.contains(kill.code),
              kill.at != ackedKillAt else { return nil }
        return kill
    }

    public func acknowledgeUnverifiedKill() {
        guard let kill = unverifiedKill else { return }
        ackedKillAt = kill.at
        defaults.set(kill.at, forKey: Self.ackKey)
    }

    // MARK: migration

    /// Every 10 s while a migration runs; the caller's task scopes it to a
    /// visible, active Pod tab.
    public func pollMigration() async {
        while !Task.isCancelled {
            do { try await sleep(Self.migrationPollInterval) } catch { return }
            if pod?.migration?.running == true { await refresh() }
        }
    }
}
