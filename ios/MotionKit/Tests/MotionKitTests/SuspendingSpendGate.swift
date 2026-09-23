import Foundation
@testable import MotionKit

/// Suspends inside `perform` until `release()` is called — lets a test hold
/// `RunFlow.isSpending == true` open long enough to exercise reentrancy (e.g.
/// a `start` that lands mid-spend) without a real network round trip.
actor SuspendingSpendGate: SpendSending {
    private(set) var intents: [SpendIntent] = []
    private var continuation: CheckedContinuation<Void, Never>?
    private var released = false
    private var result: SpendResult

    init(result: SpendResult = .accepted(runID: "tg-1000", outcome: "started")) {
        self.result = result
    }

    func perform(_ intent: SpendIntent, label: String, onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        intents.append(intent)
        await waitUntilReleased()
        return result
    }

    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        .notSent(reason: "SuspendingSpendGate does not support recheck")
    }

    func replayPending() async -> SpendResult? { nil }
    func pending() async -> SpendLedgerEntry? { nil }

    func release() {
        released = true
        continuation?.resume()
        continuation = nil
    }

    private func waitUntilReleased() async {
        if released { return }
        await withCheckedContinuation { continuation = $0 }
    }
}
