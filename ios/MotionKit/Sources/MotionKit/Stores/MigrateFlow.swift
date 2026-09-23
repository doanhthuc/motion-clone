import Foundation
import Observation

/// The Network Volume migration (Phase 5 design §6): ask → typed confirmation
/// → migrate. `ask` acts on nothing, so it is a plain POST. `migrate` deletes
/// the source volume once the copy verifies, so it goes through `SpendGate`
/// like every spend: one key per tap, journalled before it leaves, resent only
/// with that same key, never re-minted.
@MainActor @Observable
public final class MigrateFlow {
    public enum Step: Equatable, Sendable {
        case choose
        case asking(toDc: String)
        case confirm(MigrateAsk)
        case started(toDc: String)
        case outcomeUnknown
    }

    public private(set) var step: Step = .choose
    /// The destination last asked about, or preselected from a GPU row.
    public private(set) var destination: String?
    public private(set) var message: String?
    public private(set) var inFlightLabel: String?
    public private(set) var retryNote: String?
    /// The migrate got no definitive answer; "Check again" resends its key.
    public private(set) var needsRecheck = false
    /// "Checking the earlier Migrate…" while the launch replay is outstanding.
    public private(set) var pendingNotice: String?
    /// Migrate enables only on an exact, case-sensitive match with `to_dc`.
    public var typed = ""

    /// The server's 10-minute clock started before its answer reached the phone.
    static let expiryMargin: TimeInterval = 15

    private let client: APIClient
    private let gate: any SpendSending
    private let pod: PodStore
    private let now: @Sendable () -> Date
    private var deadline: Date?
    private var didReplay = false

    public init(client: APIClient, gate: any SpendSending, pod: PodStore,
                now: @escaping @Sendable () -> Date = { Date() }) {
        self.client = client
        self.gate = gate
        self.pod = pod
        self.now = now
    }

    public var isSending: Bool { inFlightLabel != nil }

    public var currentAsk: MigrateAsk? {
        if case let .confirm(ask) = step { return ask }
        return nil
    }

    public func secondsLeft(at date: Date) -> Int {
        guard let deadline else { return 0 }
        return max(0, Int(deadline.timeIntervalSince(date).rounded(.down)))
    }

    public func canMigrate(at date: Date) -> Bool {
        guard let ask = currentAsk, let deadline else { return false }
        return typed == ask.toDc && date < deadline
            && !isSending && !needsRecheck && pendingNotice == nil
    }

    /// Why a migration can't start, or nil. The server re-checks every one of
    /// these under `BOT_LOCK` (`_migrate_blocked`); this only explains.
    public static func blocker(pod: PodStatus?, runStatus: RunStatus?) -> String? {
        if pod?.migration?.running == true { return "A volume migration is already running." }
        if pod?.lease != nil { return "A pod is live — kill it before moving the volume." }
        if runStatus?.isLive == true { return "The run is busy — wait for it before moving the volume." }
        return nil
    }

    /// Opening the sheet. An unanswered migrate is never reset: it must be
    /// resolved through Check again.
    public func begin(preselect: String?) {
        guard !isSending, !needsRecheck, pendingNotice == nil else { return }
        step = .choose
        destination = preselect
        typed = ""
        message = nil
        deadline = nil
    }

    /// Refused like `begin` while a migrate is unanswered: a fresh token
    /// would replace the confirm step that Check again belongs to.
    public func ask(toDc: String) async {
        guard !isSending, !needsRecheck, pendingNotice == nil else { return }
        if case .asking = step { return }
        destination = toDc
        step = .asking(toDc: toDc)
        message = nil
        typed = ""
        deadline = nil
        do {
            let answer = try await client.post(MigrateAsk.self, body: MigrateAskRequest(toDc: toDc),
                                               timeout: 90, "v1", "pod", "migrate", "ask")
            deadline = now().addingTimeInterval(answer.expiresInSec - Self.expiryMargin)
            step = .confirm(answer)
        } catch {
            message = error.userMessage
            step = .choose
        }
    }

    public func migrate() async {
        guard canMigrate(at: now()), let ask = currentAsk else { return }
        let label = "Migrate volume to \(ask.toDc)"
        inFlightLabel = label
        retryNote = nil
        message = nil
        let result = await gate.perform(.migrate(toDc: ask.toDc, confirmToken: ask.confirmToken),
                                        label: label) { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        await apply(result, toDc: ask.toDc)
    }

    /// Resends the saved request with its saved key. Never mints a new one.
    public func recheck() async {
        guard needsRecheck, !isSending else { return }
        let saved = await gate.pending()
        if let intent = saved?.intent, intent.kind != .migrate {
            message = "The pending request isn't a migration — check it from the run flow."
            return
        }
        var toDc = destination ?? ""
        if case let .migrate(dc, _)? = saved?.intent { toDc = dc }
        inFlightLabel = "Checking the earlier migrate…"
        message = nil
        let result = await gate.recheck { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        if case let .notSent(reason) = result {
            message = reason
            if await gate.pending() == nil { needsRecheck = false }
            return
        }
        await apply(result, toDc: toDc)
    }

    /// Once per launch, and only for a pending migrate (AppModel routes it).
    public func replayPendingOnce() async {
        guard !didReplay else { return }
        didReplay = true
        guard let entry = await gate.pending(), case let .migrate(toDc, _) = entry.intent else { return }
        pendingNotice = "Checking the earlier \(entry.label)…"
        let result = await gate.replayPending()
        pendingNotice = nil
        guard let result else { return }
        await apply(result, toDc: toDc)
    }

    private func noteRetry(_ attempt: Int, _ reason: SpendRetryReason) {
        guard inFlightLabel != nil else { return }
        retryNote = reason == .botBusy
            ? "The bot is busy — retry \(attempt) of 3"
            : "No answer — retry \(attempt) of 2 with the same request"
    }

    func apply(_ result: SpendResult, toDc: String) async {
        switch result {
        case .accepted:
            needsRecheck = false
            deadline = nil
            step = .started(toDc: toDc)
            await pod.refresh()
        case let .refused(status, code, text, _):
            needsRecheck = false
            if code == "bad_confirm_token" {
                step = .choose
                destination = toDc
                typed = ""
                deadline = nil
                message = "That confirmation expired — ask again."
            } else {
                message = APIError.server(status: status, code: code, message: text).userMessage
            }
        case .outcomeUnknown:
            needsRecheck = false
            step = .outcomeUnknown
            message = "Couldn't tell whether the migration started — check Pod or Telegram before trying again."
            await pod.refresh()
        case let .busy(attempts):
            needsRecheck = false
            message = "The bot stayed busy after \(attempts) attempts. Nothing was recorded — tap again when ready."
        case let .unreachable(detail):
            needsRecheck = true
            message = "No answer from the VPS (\(detail)). The request is saved — Check again resends it without migrating twice."
        case let .expired(label, createdAt):
            needsRecheck = false
            let when = createdAt.map { $0.formatted(date: .omitted, time: .shortened) } ?? "earlier"
            message = "\(label) from \(when) couldn't be verified. Check Pod before trying again."
        case let .notSent(reason):
            message = reason
        }
    }
}
