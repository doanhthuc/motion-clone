import Foundation

/// The only sender of `phase-a`, `regen`, `confirm` and `resume`.
///
/// Every rule that keeps one tap from becoming two spends lives here
/// (parent design §4, API spec §5.5/§5.8):
/// - the key is minted in `perform` and nowhere else;
/// - the ledger entry is on disk before the request leaves;
/// - a transport failure or a non-JSON 5xx (the origin may still be working)
///   is resent with the same key; the entry stays until a definitive answer;
/// - `503 bot_busy` means the server recorded nothing: same key, 5 s apart,
///   at most 3 retries;
/// - a JSON 5xx (other than `bot_busy`) is not yet an answer: `server.py`
///   turns an unhandled exception into `500 internal` AFTER `idem.begin`
///   wrote `pending`, so a pod may be half-rented. It is resent with the same
///   key on the unreachable schedule; only the SAME status and code coming
///   back for that key is the server's stored answer, and definitive;
/// - `409 outcome_unknown` is final — resending would only repeat it.
public actor SpendGate: SpendSending {
    /// The server prunes idempotency records after 24 h, after which a replay
    /// would be a FRESH spend. 20 h leaves margin for clock skew.
    public static let replayWindow: TimeInterval = 20 * 3600
    static let busyDelay: Duration = .seconds(5)
    static let busyRetries = 3
    static let unreachableDelays: [Duration] = [.seconds(2), .seconds(5)]

    private let client: APIClient
    private let ledger: IdempotencyLedger
    private let sleep: @Sendable (Duration) async throws -> Void
    private let now: @Sendable () -> Date
    private let makeKey: @Sendable () -> String
    private var sending = false

    public init(client: APIClient, ledger: IdempotencyLedger = IdempotencyLedger(),
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                now: @escaping @Sendable () -> Date = { Date() },
                makeKey: @escaping @Sendable () -> String = { UUID().uuidString }) {
        self.client = client
        self.ledger = ledger
        self.sleep = sleep
        self.now = now
        self.makeKey = makeKey
    }

    public func pending() -> SpendLedgerEntry? {
        try? ledger.load()
    }

    public func perform(_ intent: SpendIntent, label: String,
                        onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        guard !sending else { return .notSent(reason: "Another spend request is still in flight.") }
        sending = true
        defer { sending = false }
        switch loadPending() {
        case .failure(let problem):
            return problem.result
        case .success(let existing?):
            return .notSent(reason: "“\(existing.label)” hasn't been answered yet. Check it before spending again.")
        case .success(nil):
            break
        }
        let entry = SpendLedgerEntry(key: makeKey(), intent: intent, label: label, createdAt: now())
        do {
            try ledger.save(entry)
        } catch {
            return .notSent(reason: "Couldn't record the request on this phone, so it wasn't sent (\(error.localizedDescription)).")
        }
        return await transmit(entry, retrying: true, onRetry: onRetry)
    }

    public func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        guard !sending else { return .notSent(reason: "Another spend request is still in flight.") }
        sending = true
        defer { sending = false }
        switch loadPending() {
        case .failure(let problem):
            return problem.result
        case .success(nil):
            return .notSent(reason: "Nothing is waiting to be checked.")
        case .success(let entry?):
            return await transmit(entry, retrying: true, onRetry: onRetry)
        }
    }

    /// Once per launch. A single attempt: the user is not looking at the
    /// button yet, so no retry loop runs behind their back.
    public func replayPending() async -> SpendResult? {
        guard !sending else { return nil }
        sending = true
        defer { sending = false }
        switch loadPending() {
        case .failure(let problem):
            return problem.result
        case .success(nil):
            return nil
        case .success(let entry?):
            return await transmit(entry, retrying: false, onRetry: { _, _ in })
        }
    }

    /// `.failure` carries the result to return instead of sending: an
    /// unreadable journal or an entry too old to resend. Both clear the ledger.
    private func loadPending() -> Result<SpendLedgerEntry?, PendingProblem> {
        let entry: SpendLedgerEntry?
        do {
            entry = try ledger.load()
        } catch {
            try? ledger.clear()
            return .failure(PendingProblem(.expired(label: "An earlier spend", createdAt: nil)))
        }
        if let entry, now().timeIntervalSince(entry.createdAt) >= Self.replayWindow {
            try? ledger.clear()
            return .failure(PendingProblem(.expired(label: entry.label, createdAt: entry.createdAt)))
        }
        return .success(entry)
    }

    private func transmit(_ entry: SpendLedgerEntry, retrying: Bool,
                          onRetry: SpendRetryHandler) async -> SpendResult {
        var busyRetries = 0
        var unreachableRetries = 0
        // The last JSON 5xx seen for this key. The same status and code again
        // means the server stored it (`idem.finish`), so it is the answer.
        var lastServerError: (status: Int, code: String)?
        while true {
            let raw = await client.spendPost(entry.intent.path, body: entry.intent.body(),
                                             idempotencyKey: entry.key)
            let detail: String
            switch Self.classify(raw, intent: entry.intent) {
            case .definitive(let result):
                try? ledger.clear()
                return result
            case let .serverError(status, code, result):
                if let last = lastServerError, last.status == status, last.code == code {
                    try? ledger.clear()
                    return result
                }
                lastServerError = (status, code)
                detail = "HTTP \(status) \(code), not yet confirmed as the server's answer"
            case .busy:
                guard retrying, busyRetries < Self.busyRetries else {
                    try? ledger.clear()   // nothing was recorded server-side
                    return .busy(attempts: busyRetries + 1)
                }
                busyRetries += 1
                onRetry(busyRetries, .botBusy)
                do { try await sleep(Self.busyDelay) } catch {
                    try? ledger.clear()
                    return .busy(attempts: busyRetries)
                }
                continue
            case .ambiguous(let text):
                detail = text
            }
            guard retrying, unreachableRetries < Self.unreachableDelays.count else {
                return .unreachable(detail: detail)
            }
            let delay = Self.unreachableDelays[unreachableRetries]
            unreachableRetries += 1
            onRetry(unreachableRetries, .unreachable)
            do { try await sleep(delay) } catch { return .unreachable(detail: detail) }
        }
    }

    enum Classified: Equatable {
        case definitive(SpendResult)
        case busy
        /// A JSON error envelope with a 5xx: definitive only once repeated.
        case serverError(status: Int, code: String, result: SpendResult)
        case ambiguous(String)
    }

    struct Envelope: Decodable {
        struct Inner: Decodable { let code: String; let message: String }
        let error: Inner
        let panelToken: String?
    }

    static func classify(_ raw: RawSpendResponse, intent: SpendIntent) -> Classified {
        switch raw {
        case .transport(let detail):
            return .ambiguous(detail)
        case let .http(status, body):
            if (200..<300).contains(status) {
                let accepted = try? MotionJSON.decoder.decode(SpendAccepted.self, from: body)
                return .definitive(.accepted(runID: accepted?.runId ?? intent.runID,
                                             outcome: accepted?.outcome ?? "accepted"))
            }
            if let envelope = try? MotionJSON.decoder.decode(Envelope.self, from: body) {
                if status == 503 && envelope.error.code == "bot_busy" { return .busy }
                if envelope.error.code == "outcome_unknown" { return .definitive(.outcomeUnknown) }
                let refused = SpendResult.refused(status: status, code: envelope.error.code,
                                                  message: envelope.error.message,
                                                  panelToken: envelope.panelToken)
                if status >= 500 {
                    return .serverError(status: status, code: envelope.error.code, result: refused)
                }
                return .definitive(refused)
            }
            // No API envelope: Cloudflare (or another proxy) answered. A 5xx
            // there (502/504/524) may mean the origin is still working, so it
            // is not definitive. A 403 is Access refusing before the origin.
            if status >= 500 { return .ambiguous("HTTP \(status) without an API answer") }
            if status == 403 {
                return .definitive(.refused(status: 403, code: "access_denied",
                                            message: APIError.accessDenied(status: 403).userMessage,
                                            panelToken: nil))
            }
            return .definitive(.refused(status: status, code: "http_\(status)",
                                        message: String(decoding: body.prefix(200), as: UTF8.self),
                                        panelToken: nil))
        }
    }
}

struct PendingProblem: Error {
    let result: SpendResult
    init(_ result: SpendResult) { self.result = result }
}
