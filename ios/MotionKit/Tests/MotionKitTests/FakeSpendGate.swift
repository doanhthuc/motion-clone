import Foundation
@testable import MotionKit

/// Records every intent and answers from a script. Never touches the network.
actor FakeSpendGate: SpendSending {
    private(set) var intents: [SpendIntent] = []
    private(set) var labels: [String] = []
    private(set) var rechecks = 0
    private(set) var replays = 0
    private var results: [SpendResult]
    private var pendingEntry: SpendLedgerEntry?
    private var replayResult: SpendResult?

    init(_ results: [SpendResult] = [], pending: SpendLedgerEntry? = nil, replay: SpendResult? = nil) {
        self.results = results
        self.pendingEntry = pending
        self.replayResult = replay
    }

    func perform(_ intent: SpendIntent, label: String, onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        intents.append(intent)
        labels.append(label)
        return results.isEmpty ? .notSent(reason: "script empty") : results.removeFirst()
    }

    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        rechecks += 1
        return results.isEmpty ? .notSent(reason: "script empty") : results.removeFirst()
    }

    func replayPending() async -> SpendResult? {
        replays += 1
        pendingEntry = nil
        return replayResult
    }

    func pending() async -> SpendLedgerEntry? { pendingEntry }
}
