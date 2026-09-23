import Foundation

public enum SpendResult: Sendable, Equatable {
    /// 2xx. `outcome` is the server's (`started`, `queued`, …).
    case accepted(runID: String?, outcome: String)
    /// A definitive refusal; nothing was spent. `panelToken` is set only by
    /// `409 choice_required`.
    case refused(status: Int, code: String, message: String, panelToken: String?)
    /// `409 outcome_unknown`: an earlier attempt with this key died midway.
    case outcomeUnknown
    /// `503 bot_busy` on every attempt. The server recorded nothing.
    case busy(attempts: Int)
    /// No definitive answer; the ledger still holds the key for `recheck()`.
    case unreachable(detail: String)
    /// A pending entry was too old (or unreadable) to resend safely.
    case expired(label: String, createdAt: Date?)
    /// Refused on the phone before anything was sent.
    case notSent(reason: String)
}

public enum SpendRetryReason: Sendable, Equatable {
    case botBusy, unreachable
}

public typealias SpendRetryHandler = @Sendable (Int, SpendRetryReason) -> Void

/// What `RunFlow` needs from the gate — a protocol so store tests and the UI
/// test build can substitute a fake that never reaches the VPS.
public protocol SpendSending: Sendable {
    func perform(_ intent: SpendIntent, label: String,
                 onRetry: @escaping SpendRetryHandler) async -> SpendResult
    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult
    func replayPending() async -> SpendResult?
    func pending() async -> SpendLedgerEntry?
}

/// `202 {run_id, outcome}` from phase-a, regen, confirm and resume.
public struct SpendAccepted: Decodable, Sendable, Equatable {
    public let runId: String
    public let outcome: String
}
